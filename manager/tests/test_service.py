import os
import time
import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest import mock

from icarus_manager import service
from icarus_manager.config import Config
from icarus_manager.service import Manager


def fixed_now(hour, minute):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 22, hour, minute)
    return FixedDatetime


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        env = {"DATA_DIR": self.tmp.name, "BACKUP_DIR": self.tmp.name, "CONTROL_DIR": self.tmp.name,
               "DAILY_RESTART_TIME": "07:00", "DAILY_RESTART_GIVE_UP_MINUTES": "240"}
        with mock.patch.dict(os.environ, env):
            self.m = Manager(Config.from_env())
        self.sent = []
        self.m.notify = self.sent.append
        self.m.restart = mock.Mock(return_value=(True, "ok"))
        self.online = 0
        self.m.players = lambda: self.online
        self.status = {"state": "running", "alive": True, "buildid": "100",
                       "started_at": "2026-10-21T07:00:00", "heartbeat": time.time()}
        self.m.control.status = lambda: dict(self.status)

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_restart_waits_for_empty_server(self):
        self.online = 2
        with mock.patch.object(service, "datetime", fixed_now(7, 1)):
            self.m._daily_restart()
        self.m.restart.assert_not_called()
        self.assertIn("adiado", self.sent[-1])

        self.m.daily_next_try = 0
        self.online = 0
        with mock.patch.object(service, "datetime", fixed_now(7, 20)):
            self.m._daily_restart()
            self.m._daily_restart()  # only once per day
        self.m.restart.assert_called_once_with("reinício diário", update=True)

    def test_daily_restart_gives_up_and_skips_if_rebooted(self):
        self.online = 1
        with mock.patch.object(service, "datetime", fixed_now(11, 1)):
            self.m._daily_restart()
        self.assertIn("cancelado", self.sent[-1])

        self.m.daily_done = None
        self.online = 0
        self.status["started_at"] = "2026-10-22T07:30:00"  # PC rebooted after 07:00
        with mock.patch.object(service, "datetime", fixed_now(8, 0)):
            self.m._daily_restart()
        self.m.restart.assert_not_called()

    def test_before_time_does_nothing(self):
        with mock.patch.object(service, "datetime", fixed_now(6, 59)):
            self.m._daily_restart()
        self.m.restart.assert_not_called()

    def test_update_applied_only_when_empty(self):
        self.m.health = "online"
        self.online = 1
        with mock.patch.object(service.steam, "latest_buildid", return_value="200"):
            self.m._check_updates()
            self.m._check_updates()
        self.m.restart.assert_not_called()
        self.assertEqual(sum("nova versão" in s for s in self.sent), 1)

        self.online = 0
        def do_restart(*a, **kw):
            self.status["buildid"] = "200"
            return True, "ok"
        self.m.restart.side_effect = do_restart
        self.m._check_updates()
        self.m.restart.assert_called_once()
        self.assertTrue(self.m.restart.call_args.kwargs["update"])
        self.assertIsNone(self.m.pending_update)

    def test_failed_update_is_not_retried_immediately(self):
        self.m.health = "online"
        with mock.patch.object(service.steam, "latest_buildid", return_value="200"):
            self.m._check_updates()
            self.m._check_updates()
        self.m.restart.assert_called_once()
        self.assertIn("não ficou aplicada", self.sent[-1])

    def test_status_notifications(self):
        self.m._monitor_server()
        self.assertEqual(self.sent, [], "no message for the initial online state")
        self.status["state"] = "crashed"
        self.m._monitor_server()
        self.status["state"] = "running"
        self.m._monitor_server()
        self.assertIn("foi abaixo", self.sent[0])
        self.assertIn("online", self.sent[1])
        self.status.update(state="offline", alive=False, heartbeat=time.time() - 300)
        self.m._monitor_server()
        self.assertIn("não está a responder", self.sent[-1])


if __name__ == "__main__":
    unittest.main()
