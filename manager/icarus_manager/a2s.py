"""Steam server query (A2S_INFO / A2S_PLAYER) over UDP, stdlib only.

Used to know whether the server answers and how many players are online,
without depending on the format of the game log.
"""

import socket
import struct

HEADER = b"\xff\xff\xff\xff"
A2S_INFO = HEADER + b"TSource Engine Query\x00"
A2S_PLAYER = HEADER + b"U"
CHALLENGE = 0x41


class A2SError(Exception):
    pass


class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def byte(self):
        value = self.data[self.pos]
        self.pos += 1
        return value

    def short(self):
        (value,) = struct.unpack_from("<h", self.data, self.pos)
        self.pos += 2
        return value

    def long(self):
        (value,) = struct.unpack_from("<l", self.data, self.pos)
        self.pos += 4
        return value

    def float(self):
        (value,) = struct.unpack_from("<f", self.data, self.pos)
        self.pos += 4
        return value

    def string(self):
        end = self.data.index(b"\x00", self.pos)
        value = self.data[self.pos:end].decode("utf-8", errors="replace")
        self.pos = end + 1
        return value


def _request(host, port, payload, timeout, append_challenge):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(payload, (host, port))
            data, _ = sock.recvfrom(4096)
            # Servers may answer with a challenge that must be echoed back
            for _ in range(3):
                if len(data) < 5 or data[:4] != HEADER:
                    raise A2SError("resposta inválida")
                if data[4] != CHALLENGE:
                    return data[4], data[5:]
                challenge = data[5:9]
                sock.sendto(append_challenge(challenge), (host, port))
                data, _ = sock.recvfrom(4096)
        except (OSError, socket.timeout) as err:
            raise A2SError(str(err)) from err
    raise A2SError("demasiados challenges")


def query_info(host, port, timeout=3.0):
    kind, body = _request(host, port, A2S_INFO, timeout, lambda c: A2S_INFO + c)
    if kind != 0x49:
        raise A2SError(f"tipo de resposta inesperado: {kind:#x}")
    r = _Reader(body)
    try:
        info = {"protocol": r.byte(), "name": r.string(), "map": r.string(),
                "folder": r.string(), "game": r.string(), "app_id": r.short(),
                "players": r.byte(), "max_players": r.byte(), "bots": r.byte()}
        r.pos += 4  # server type, environment, visibility, vac
        info["version"] = r.string()
    except (IndexError, ValueError, struct.error) as err:
        raise A2SError(f"resposta truncada: {err}") from err
    return info


def query_players(host, port, timeout=3.0):
    kind, body = _request(host, port, A2S_PLAYER + b"\xff\xff\xff\xff", timeout, lambda c: A2S_PLAYER + c)
    if kind != 0x44:
        raise A2SError(f"tipo de resposta inesperado: {kind:#x}")
    r = _Reader(body)
    players = []
    try:
        for _ in range(r.byte()):
            r.byte()  # index
            name = r.string()
            r.long()  # score
            duration = r.float()
            players.append({"name": name, "duration": duration})
    except (IndexError, ValueError, struct.error):
        pass  # some servers truncate the list; keep what we got
    return players
