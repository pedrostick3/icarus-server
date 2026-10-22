import unittest
from unittest import mock

from icarus_manager.bot import PasswordGuard


class PasswordGuardTest(unittest.TestCase):
    def test_accepts_only_the_right_password(self):
        guard = PasswordGuard("s3gredo", 3)
        self.assertFalse(guard.check(1, "errada"))
        self.assertTrue(guard.check(1, "s3gredo"))

    def test_lockout_after_failures_and_expiry(self):
        guard = PasswordGuard("s3gredo", 3)
        with mock.patch("icarus_manager.bot.time.time", return_value=1000):
            for _ in range(3):
                guard.check(7, "x")
            self.assertTrue(guard.locked(7))
            self.assertFalse(guard.check(7, "s3gredo"), "locked users are refused even with the right password")
            self.assertFalse(guard.locked(8), "other users are not affected")
        with mock.patch("icarus_manager.bot.time.time", return_value=1000 + 15 * 60 + 1):
            self.assertFalse(guard.locked(7))
            self.assertTrue(guard.check(7, "s3gredo"))

    def test_success_clears_failures(self):
        guard = PasswordGuard("s3gredo", 3)
        guard.check(1, "x")
        guard.check(1, "x")
        guard.check(1, "s3gredo")
        guard.check(1, "x")
        self.assertFalse(guard.locked(1))

    def test_no_password_configured_refuses_everything(self):
        guard = PasswordGuard("", 3)
        self.assertFalse(guard.configured)
        self.assertFalse(guard.check(1, ""))


if __name__ == "__main__":
    unittest.main()
