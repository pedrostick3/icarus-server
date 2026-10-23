import unittest
from unittest import mock

from icarus_manager.bot import PasswordGuard, access_hint


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


class AccessHintTest(unittest.TestCase):
    def test_bot_not_in_guild(self):
        text = access_hint(111, 738129277232611456, {999: "Outro servidor"})
        self.assertIn("NÃO está no servidor Discord 738129277232611456", text)
        self.assertIn("Outro servidor (999)", text)
        self.assertIn("client_id=111&scope=bot+applications.commands&permissions=3072", text)
        self.assertIn("guild_id=738129277232611456", text)

    def test_bot_in_guild_without_commands_scope(self):
        text = access_hint(111, 5, {5: "Louriceira"})
        self.assertIn("sem o scope 'applications.commands'", text)


if __name__ == "__main__":
    unittest.main()
