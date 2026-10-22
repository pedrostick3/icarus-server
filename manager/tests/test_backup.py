import http.server
import json
import os
import threading
import time
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from icarus_manager.backup import Backup, NotReady
from icarus_manager.config import Config
from icarus_manager.discord_webhook import Discord
from icarus_manager.rclone import Rclone


class Hook(http.server.BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Hook.received.append((self.headers["Content-Type"], body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class BackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Hook.received.clear()
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.prospects = root / "data/Saved/PlayerData/DedicatedServer/Prospects"
        self.prospects.mkdir(parents=True)
        self.save = self.prospects / "Mundo.json"
        self.write_save({"blob": os.urandom(400_000).hex()})
        env = {
            "DATA_DIR": str(root / "data"), "BACKUP_DIR": str(root / "backups"),
            "DISCORD_WEBHOOK_URL": f"http://127.0.0.1:{self.server.server_port}/api/webhooks/1/x",
            "DISCORD_MAX_FILE_MB": "0.25", "BACKUP_FILE_STABLE_SECONDS": "1", "LOCAL_KEEP_LAST": "2",
            "LOCAL_KEEP_DAILY": "0", "LOCAL_KEEP_WEEKLY": "0", "LOCAL_KEEP_MONTHLY": "0",
        }
        with mock.patch.dict(os.environ, env):
            self.cfg = Config.from_env()
        rclone = Rclone("", root / "missing.conf", self.cfg.rclone_retention)
        self.backup = Backup(self.cfg, Discord(self.cfg.webhook_url, "t"), rclone)

    def tearDown(self):
        self.tmp.cleanup()

    def write_save(self, data, age=60):
        self.save.write_text(json.dumps(data))
        past = time.time() - age
        os.utime(self.save, (past, past))

    def test_backup_split_upload_and_skip_unchanged(self):
        path = self.backup.run("teste")
        self.assertTrue(path and path.is_file())
        with zipfile.ZipFile(path) as zf:
            self.assertIn("Saved/PlayerData/DedicatedServer/Prospects/Mundo.json", zf.namelist())
        self.assertGreater(len(Hook.received), 1, "zip larger than the limit must be split")
        joined = b"".join(body.split(b"\r\n\r\n", 2)[-1].rsplit(b"\r\n--", 1)[0] for _, body in Hook.received)
        self.assertEqual(joined, path.read_bytes())
        self.assertIsNone(self.backup.run("de novo"))  # unchanged

    def test_refuses_fresh_or_invalid_save(self):
        self.write_save({"a": 1}, age=0)
        with self.assertRaises(NotReady):
            self.backup.run("fresco")
        self.save.write_text('{"truncated": ')
        os.utime(self.save, (time.time() - 60,) * 2)
        with self.assertRaises(NotReady):
            self.backup.run("inválido")

    def test_local_rotation_and_restore_prepare(self):
        made = []
        for i in range(3):
            self.write_save({"v": i})
            made.append(self.backup.run(f"r{i}"))
            time.sleep(1.1)  # distinct timestamps in the file name
        self.assertEqual([p.name for p in self.backup.list_local()], [made[2].name, made[1].name])
        data = made[2].read_bytes()
        for n, i in enumerate(range(0, len(data), 100), 1):
            (self.cfg.backup_dir / f"joined.zip.{n:03d}").write_bytes(data[i:i + 100])
        self.assertEqual(self.backup.prepare_restore("joined.zip.001").read_bytes(), data)

    def test_server_settings_never_backed_up(self):
        ini = self.prospects.parent / "ServerSettings.ini"
        ini.write_text("AdminPassword=segredo")
        os.utime(ini, (time.time() - 60,) * 2)
        with zipfile.ZipFile(self.backup.run("com ini")) as zf:
            self.assertFalse(any(n.endswith("ServerSettings.ini") for n in zf.namelist()))

    def test_verify_detects_broken_saves(self):
        good = self.backup.run("bom")
        self.assertEqual(self.backup.verify(good), ["Mundo"])
        bad = self.cfg.backup_dir / "icarus-20260101-000000-mau.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("Saved/PlayerData/DedicatedServer/Prospects/Mundo.json", '{"cortado": ')
        with self.assertRaises(ValueError):
            self.backup.verify(bad)
        empty = self.cfg.backup_dir / "icarus-20260101-000001-vazio.zip"
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("outra/coisa.txt", "x")
        with self.assertRaises(ValueError):
            self.backup.verify(empty)


if __name__ == "__main__":
    unittest.main()
