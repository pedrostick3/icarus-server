"""Stands in for IcarusServer.exe in tests: answers A2S queries and writes a log.

Players are read from $FAKE_PLAYERS_FILE (one name per line).
"""
import os
import signal
import socket
import struct
import sys
import time

port = int(os.environ["QUERY_PORT"])
players_file = os.environ["FAKE_PLAYERS_FILE"]
log_path = os.path.join(os.environ["DATA_DIR"], "Saved", "Logs", "Icarus.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
log = open(log_path, "a", buffering=1)
log.write("LogInit: fake Icarus server starting\n")


def stop(*_):
    log.write("LogExit: shutting down\n")
    sys.exit(0)


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


def players():
    try:
        return [l.strip() for l in open(players_file) if l.strip()]
    except OSError:
        return []


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
CHALLENGE = b"\x11\x22\x33\x44"
H = b"\xff\xff\xff\xff"
while True:
    data, addr = sock.recvfrom(1400)
    if data.startswith(H + b"TSource Engine Query\x00"):
        if not data.endswith(CHALLENGE):
            sock.sendto(H + b"A" + CHALLENGE, addr)
            continue
        names = players()
        body = (b"\x11" + b"Fake Icarus\x00" + b"Olympus\x00" + b"Icarus\x00" + b"Icarus\x00"
                + struct.pack("<h", 0) + bytes([len(names), 4, 0]) + b"dwv" + b"\x00\x00" + b"1.0\x00")
        sock.sendto(H + b"I" + body, addr)
    elif data.startswith(H + b"U"):
        if data[5:9] != CHALLENGE:
            sock.sendto(H + b"A" + CHALLENGE, addr)
            continue
        names = players()
        body = bytes([len(names)])
        for i, n in enumerate(names):
            body += bytes([i]) + n.encode() + b"\x00" + struct.pack("<lf", 0, 12.5)
        sock.sendto(H + b"D" + body, addr)
