import io
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from icarus_manager.ddns import DuckDNS


class FakeWeb:
    def __init__(self):
        self.ip = "46.189.146.168"
        self.answer = "OK"
        self.updates = []

    def __call__(self, url, timeout=None):
        if "ipify" in url:
            return io.BytesIO(self.ip.encode())
        self.updates.append(parse_qs(urlparse(url).query))
        return io.BytesIO(self.answer.encode())


class DuckDNSTest(unittest.TestCase):
    def test_updates_only_when_ip_changes(self):
        web = FakeWeb()
        dns = DuckDNS("icarus-casa.duckdns.org", "tok")
        with mock.patch("icarus_manager.ddns.urllib.request.urlopen", web):
            self.assertIsNone(dns.update(), "first update is not a change")
            self.assertIsNone(dns.update())
            web.ip = "46.189.1.2"
            self.assertEqual(dns.update(), "46.189.1.2")
        self.assertEqual(len(web.updates), 2)
        self.assertEqual(web.updates[0]["domains"], ["icarus-casa"])
        self.assertEqual(web.updates[1]["ip"], ["46.189.1.2"])

    def test_bad_token_raises(self):
        web = FakeWeb()
        web.answer = "KO"
        with mock.patch("icarus_manager.ddns.urllib.request.urlopen", web):
            with self.assertRaises(RuntimeError):
                DuckDNS("x", "bad").update()

    def test_disabled_without_token(self):
        self.assertFalse(DuckDNS("x", "").enabled)


if __name__ == "__main__":
    unittest.main()
