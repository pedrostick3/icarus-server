import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from icarus_manager import a2s

FAKES = Path(__file__).parent / "fakes"


def free_udp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class A2STest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.players = Path(self.tmp.name) / "players.txt"
        self.port = free_udp_port()
        env = dict(os.environ, QUERY_PORT=str(self.port), FAKE_PLAYERS_FILE=str(self.players), DATA_DIR=self.tmp.name)
        self.proc = subprocess.Popen([sys.executable, str(FAKES / "fake_icarus.py")], env=env)
        time.sleep(0.5)

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait()
        self.tmp.cleanup()

    def test_info_and_players_with_challenge(self):
        self.players.write_text("Pedro\nAmigo\n")
        info = a2s.query_info("127.0.0.1", self.port)
        self.assertEqual(info["name"], "Fake Icarus")
        self.assertEqual((info["players"], info["max_players"]), (2, 4))
        self.assertEqual([p["name"] for p in a2s.query_players("127.0.0.1", self.port)], ["Pedro", "Amigo"])

    def test_timeout_raises(self):
        with self.assertRaises(a2s.A2SError):
            a2s.query_info("127.0.0.1", free_udp_port(), timeout=0.5)


if __name__ == "__main__":
    unittest.main()
