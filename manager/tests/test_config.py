import os
import unittest
from unittest import mock

from icarus_manager.config import Config


class ConfigTest(unittest.TestCase):
    def test_discord_ids_keep_every_digit(self):
        env = {"DISCORD_GUILD_ID": "738129277232611436", "DISCORD_ADMIN_USER_IDS": "738129277232611436, 1"}
        with mock.patch.dict(os.environ, env):
            cfg = Config.from_env()
        self.assertEqual(cfg.bot_guild_id, 738129277232611436)
        self.assertEqual(cfg.bot_admin_user_ids, {738129277232611436, 1})

    def test_numbers_accept_decimals_and_defaults(self):
        with mock.patch.dict(os.environ, {"LOCAL_KEEP_LAST": "24.0", "QUERY_PORT": ""}):
            cfg = Config.from_env()
        self.assertEqual(cfg.local_retention.last, 24)
        self.assertEqual(cfg.query_port, 27015)


if __name__ == "__main__":
    unittest.main()
