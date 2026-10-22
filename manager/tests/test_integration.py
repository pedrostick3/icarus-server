"""Runs the real server/supervisor.sh with fake wine/SteamCMD and drives it with the manager."""

import json
import os
import shutil
import signal
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from icarus_manager.config import Config
from icarus_manager.service import Manager
from test_a2s import free_udp_port

REPO = Path(__file__).resolve().parents[2]
FAKES = Path(__file__).parent / "fakes"


@unittest.skipUnless(shutil.which("jq") and shutil.which("unzip"), "precisa de jq e unzip")
class SupervisorIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dirs = {name: root / name for name in ("data", "game", "control", "import", "backups", "steamcmd", "bin", "wine")}
        for d in self.dirs.values():
            d.mkdir()
        shutil.copy(FAKES / "steamcmd.sh", self.dirs["steamcmd"] / "steamcmd.sh")
        for tool in ("wine", "wineboot", "wineserver"):
            shutil.copy(FAKES / tool, self.dirs["bin"] / tool)
        shutil.copy(FAKES / "fake_icarus.py", self.dirs["bin"] / "fake_icarus.py")
        self.build_file = root / "build.txt"
        self.build_file.write_text("100")
        self.players = root / "players.txt"
        self.players.write_text("")
        (self.dirs["import"] / "Mundo.json").write_text(json.dumps({"world": "original"}))
        self.steam_fail = root / "steam-fail"
        self.port = free_udp_port()

        self.env = dict(
            os.environ,
            PATH=f"{self.dirs['bin']}:{os.environ['PATH']}",
            GAME_DIR=str(self.dirs["game"]), DATA_DIR=str(self.dirs["data"]),
            CONTROL_DIR=str(self.dirs["control"]), IMPORT_DIR=str(self.dirs["import"]),
            BACKUPS_DIR=str(self.dirs["backups"]), STEAMCMD_DIR=str(self.dirs["steamcmd"]),
            WINEPREFIX=str(self.dirs["wine"]), QUERY_PORT=str(self.port), SERVER_NAME="Teste & Cª",
            FAKE_BUILD_FILE=str(self.build_file), FAKE_PLAYERS_FILE=str(self.players),
            FAKE_STEAM_FAIL_FILE=str(self.steam_fail), SHUTDOWN_TIMEOUT="5", LOAD_PROSPECT="NaoExiste.json", LOG_TO_STDOUT="false", CRASH_LIMIT="2",
        )
        self.log_fh = open(root / "supervisor.log", "w")
        self.proc = subprocess.Popen(["bash", str(REPO / "server/supervisor.sh")], env=self.env,
                                     stdout=self.log_fh, stderr=subprocess.STDOUT)

        manager_env = {
            "DATA_DIR": str(self.dirs["data"]), "BACKUP_DIR": str(self.dirs["backups"]),
            "CONTROL_DIR": str(self.dirs["control"]), "SERVER_HOST": "127.0.0.1", "QUERY_PORT": str(self.port),
            "BACKUP_FILE_STABLE_SECONDS": "0", "DISCORD_WEBHOOK_URL": "",
        }
        with mock.patch.dict(os.environ, manager_env):
            self.manager = Manager(Config.from_env())

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=30)
        self.log_fh.close()
        print("\n--- supervisor.log ---\n" + (Path(self.tmp.name) / "supervisor.log").read_text()[-3000:])
        self.tmp.cleanup()

    def wait_for(self, predicate, timeout=30, what="condição"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.5)
        self.fail(f"timeout à espera de {what}")

    def status(self):
        return self.manager.control.status()

    def test_full_lifecycle(self):
        saves = self.dirs["data"] / "Saved/PlayerData/DedicatedServer/Prospects"
        self.wait_for(lambda: self.status().get("state") == "running", what="running")
        self.wait_for(lambda: self.manager.players() == 0 and self._a2s_up(), what="A2S")

        # first boot: installed, prospect imported, ini written with special characters intact
        self.assertEqual(self.status()["buildid"], "100")
        self.assertEqual(json.loads((saves / "Mundo.json").read_text()), {"world": "original"})
        ini = (self.dirs["data"] / "Saved/Config/WindowsServer/ServerSettings.ini").read_text()
        self.assertIn("SessionName=Teste & Cª", ini)
        self.assertIn("ShutdownIfEmptyFor=-1", ini)
        self.assertIn("LoadProspect=NaoExiste\n", ini)
        supervisor_log = (Path(self.tmp.name) / "supervisor.log").read_text()
        self.assertIn("AVISO: LOAD_PROSPECT=NaoExiste mas não existe", supervisor_log)
        self.assertIn("prospects disponíveis: Mundo", supervisor_log)

        # restart is refused while someone plays, unless forced
        self.players.write_text("Pedro\n")
        ok, message = self.manager.restart("teste", update=False)
        self.assertFalse(ok)
        self.assertIn("1 jogador", message)

        # update: new build gets installed and the server comes back
        self.players.write_text("")
        self.build_file.write_text("101")
        ok, message = self.manager.restart("teste", update=True)
        self.assertTrue(ok, message)
        self.assertEqual(self.status()["buildid"], "101")
        self.assertEqual(self.status()["state"], "running")

        # failed update keeps the old build running
        self.steam_fail.touch()
        self.build_file.write_text("102")
        ok, message = self.manager.restart("teste", update=True)
        self.assertFalse(ok)
        self.assertIn("atualização falhou", message)
        self.assertEqual(self.status()["buildid"], "101")
        self.assertEqual(self.status()["state"], "running")
        self.steam_fail.unlink()

        # backup + restore round trip
        backup_msg = self.manager.manual_backup()
        self.assertIn("Backup criado", backup_msg)
        backup_name = self.manager.backup.list_local()[0].name
        (saves / "Mundo.json").write_text(json.dumps({"world": "estragado"}))
        (saves / "Novo.json").write_text("{}")
        ok, message = self.manager.restore(backup_name)
        self.assertTrue(ok, message)
        self.assertEqual(json.loads((saves / "Mundo.json").read_text()), {"world": "original"})
        self.assertFalse((saves / "Novo.json").exists(), "restore replaces the saves folder")
        snapshots = list((self.dirs["data"] / "restore-snapshots").iterdir())
        self.assertEqual(len(snapshots), 1)
        self.assertTrue((snapshots[0] / "DedicatedServer/Prospects/Novo.json").exists())
        self.assertEqual(self.status()["state"], "running")

        # crash -> automatic restart; repeated crashes -> crash_loop until a command
        os.kill(int(self.status()["pid"]), signal.SIGKILL)
        self.wait_for(lambda: self.status().get("state") == "crashed", what="crashed")
        self.wait_for(lambda: self.status().get("state") == "running", timeout=40, what="running após crash")
        os.kill(int(self.status()["pid"]), signal.SIGKILL)
        self.wait_for(lambda: self.status().get("state") == "crash_loop", what="crash_loop")
        self.assertEqual(self.manager.compute_health(self.status()), "crash_loop")
        ok, message = self.manager.control.run("start", timeout=30)
        self.assertTrue(ok, message)
        self.wait_for(lambda: self.status().get("state") == "running", what="running após start")

        # docker stop -> graceful stop
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=30)
        self.assertEqual(json.loads((self.dirs["control"] / "status.json").read_text())["state"], "stopped")
        log = (self.dirs["data"] / "Saved/Logs/Icarus.log").read_text()
        self.assertIn("LogExit: shutting down", log)

    def _a2s_up(self):
        try:
            from icarus_manager import a2s
            a2s.query_info("127.0.0.1", self.port, timeout=0.5)
            return True
        except Exception:
            return False


if __name__ == "__main__":
    unittest.main()
