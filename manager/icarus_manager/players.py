"""Who is online: Steam query (A2S) first, game log as a fallback."""

import logging
import os
import threading
import time

from . import a2s

log = logging.getLogger(__name__)


class PlayerTracker:
    """Emits ("join"|"leave", name or None) events into a queue and keeps a count.

    PLAYER_DETECTION:
      auto - A2S once the server has answered at least once, log until then
      a2s  - only A2S
      log  - only the game log (regex)
    """

    def __init__(self, cfg, events):
        self.cfg = cfg
        self.events = events
        self.mode = cfg.player_detection if cfg.player_detection in ("auto", "a2s", "log") else "auto"
        self.a2s_ever_ok = False
        self.a2s_ok_at = 0.0
        self.info = None
        self.names = []
        self._a2s_baseline = None  # list of names (or "" placeholders) from the last good query
        self._log_count = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def start(self):
        if self.mode in ("auto", "a2s"):
            threading.Thread(target=self._a2s_loop, daemon=True, name="a2s").start()
        if self.mode in ("auto", "log"):
            threading.Thread(target=self._log_loop, daemon=True, name="log-watcher").start()

    @property
    def using_a2s(self):
        return self.mode == "a2s" or (self.mode == "auto" and self.a2s_ever_ok)

    @property
    def a2s_responding(self):
        return time.time() - self.a2s_ok_at < max(90, self.cfg.a2s_interval_seconds * 3)

    @property
    def count(self):
        with self._lock:
            if self.using_a2s:
                return len(self._a2s_baseline or [])
            return self._log_count

    def reset(self):
        """Server restarted: nobody is connected any more."""
        with self._lock:
            self._log_count = 0
            if self._a2s_baseline is not None:
                self._a2s_baseline = []
            self.names = []

    # ------------------------------------------------------------------ A2S

    def _a2s_loop(self):
        host, port = self.cfg.server_host, self.cfg.query_port
        while True:
            try:
                info = a2s.query_info(host, port)
                try:
                    names = [p["name"] for p in a2s.query_players(host, port)]
                except a2s.A2SError:
                    names = None
                # Fall back to anonymous slots when the player list is empty/unavailable
                if not names or len(names) != info["players"]:
                    names = [""] * info["players"]
                if not self.a2s_ever_ok:
                    log.info("O servidor responde a queries Steam (A2S): %s", info.get("name"))
                self.info, self.a2s_ok_at, self.a2s_ever_ok = info, time.time(), True
                self._a2s_diff(names)
            except a2s.A2SError as err:
                log.debug("A2S sem resposta: %s", err)
            except Exception:
                log.exception("Erro no query A2S")
            time.sleep(self.cfg.a2s_interval_seconds)

    def _a2s_diff(self, names):
        with self._lock:
            previous = self._a2s_baseline
            self._a2s_baseline = names
            self.names = [n for n in names if n]
        if previous is None or not self.using_a2s:
            return
        remaining = list(previous)
        joined = []
        for name in names:
            if name in remaining:
                remaining.remove(name)
            else:
                joined.append(name)
        for name in joined:
            self.events.put(("join", name or None))
        for name in remaining:
            self.events.put(("leave", name or None))

    # ------------------------------------------------------------------ log

    def _log_loop(self):
        path = self.cfg.log_file
        fh, inode, partial = None, None, b""
        while True:
            try:
                st = path.stat()
                if fh is None or st.st_ino != inode or st.st_size < fh.tell():
                    first_open = fh is None
                    if fh:
                        fh.close()
                    fh = open(path, "rb")
                    if first_open:
                        # Skip history so old lines don't fire events
                        fh.seek(0, os.SEEK_END)
                    else:
                        self.reset()  # new log file = server restarted
                    inode, partial = st.st_ino, b""
                    log.info("A seguir o log do servidor: %s", path)
                partial += fh.read()
                *lines, partial = partial.split(b"\n")
                for line in lines:
                    self._process_line(line.decode("utf-8", errors="replace"))
            except FileNotFoundError:
                pass
            except Exception:
                log.exception("Erro ao ler o log do servidor")
            time.sleep(2)

    def _process_line(self, line):
        if "Failed to load prospect file" in line:
            self.events.put(("alert", "prospect"))
            return
        for kind, regex in (("join", self.cfg.join_regex), ("leave", self.cfg.leave_regex)):
            m = regex.search(line)
            if not m:
                continue
            name = (m.groupdict().get("name") or "").strip() or None
            log.info("Evento '%s' no log: %s", kind, line.strip()[:200])
            if self.using_a2s:
                return  # A2S is the source of truth once it works
            with self._lock:
                self._log_count = max(0, self._log_count + (1 if kind == "join" else -1))
            self.events.put((kind, name))
            return
