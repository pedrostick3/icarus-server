"""DuckDNS: keeps a fixed name (e.g. icarus-casa.duckdns.org) pointing at the home IPv4."""

import logging
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

IPV4_URL = "https://api.ipify.org"  # IPv4-only endpoint
DUCKDNS_URL = "https://www.duckdns.org/update"


def public_ipv4(timeout=15):
    with urllib.request.urlopen(IPV4_URL, timeout=timeout) as resp:
        return resp.read().decode().strip()


class DuckDNS:
    def __init__(self, domain, token):
        # Accept "nome" or "nome.duckdns.org"
        self.domain = domain.removesuffix(".duckdns.org")
        self.token = token
        self.current_ip = None

    @property
    def enabled(self):
        return bool(self.domain and self.token)

    @property
    def hostname(self):
        return f"{self.domain}.duckdns.org"

    def update(self, force=False):
        """Returns the new IP if the record changed, None otherwise."""
        ip = public_ipv4()
        if ip == self.current_ip and not force:
            return None
        query = urllib.parse.urlencode({"domains": self.domain, "token": self.token, "ip": ip})
        with urllib.request.urlopen(f"{DUCKDNS_URL}?{query}", timeout=15) as resp:
            answer = resp.read().decode().strip()
        if answer != "OK":
            raise RuntimeError(f"DuckDNS respondeu '{answer}' (domínio/token errados?)")
        changed = self.current_ip is not None and ip != self.current_ip
        log.info("DuckDNS: %s -> %s", self.hostname, ip)
        self.current_ip = ip
        return ip if changed else None
