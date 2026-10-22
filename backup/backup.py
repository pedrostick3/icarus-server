#!/usr/bin/env python3
"""Icarus save backup service.

Zips the dedicated server save data, keeps a local rotation and uploads each
backup to a Discord channel through a webhook. Backups are triggered by:
  * a fixed interval (BACKUP_INTERVAL_MINUTES),
  * a player leaving the server (detected in the server log),
  * the service starting, or manually with `--now`.
Only stdlib is used so the image stays tiny.
"""

import argparse
import hashlib
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("backup")


def env_str(name, default=""):
    # Compose passes unset keys as empty strings, so empty means "use the default"
    return os.environ.get(name, "").strip() or default


def env_bool(name, default):
    value = env_str(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on", "sim")


def env_float(name, default):
    value = env_str(name)
    return float(value) if value else default


@dataclass
class Config:
    data_dir: Path
    backup_dir: Path
    paths: list
    webhook_url: str
    discord_username: str
    discord_max_file_mb: float
    notify_players: bool
    notify_errors: bool
    interval_minutes: float
    only_if_changed: bool
    on_start: bool
    on_player_leave: bool
    leave_delay_seconds: float
    min_gap_minutes: float
    file_stable_seconds: float
    keep_local: int
    log_file: Path
    join_regex: re.Pattern
    leave_regex: re.Pattern

    @classmethod
    def from_env(cls):
        data_dir = Path(env_str("DATA_DIR", "/data"))
        paths = [p.strip() for p in env_str("BACKUP_PATHS", "Saved/PlayerData/DedicatedServer").split(",") if p.strip()]
        return cls(
            data_dir=data_dir,
            backup_dir=Path(env_str("BACKUP_DIR", "/backups")),
            paths=paths,
            webhook_url=env_str("DISCORD_WEBHOOK_URL"),
            discord_username=env_str("DISCORD_USERNAME", "Icarus Backup"),
            discord_max_file_mb=env_float("DISCORD_MAX_FILE_MB", 9.5),
            notify_players=env_bool("DISCORD_NOTIFY_PLAYERS", True),
            notify_errors=env_bool("DISCORD_NOTIFY_ERRORS", True),
            interval_minutes=env_float("BACKUP_INTERVAL_MINUTES", 60),
            only_if_changed=env_bool("BACKUP_ONLY_IF_CHANGED", True),
            on_start=env_bool("BACKUP_ON_START", True),
            on_player_leave=env_bool("BACKUP_ON_PLAYER_LEAVE", True),
            leave_delay_seconds=env_float("BACKUP_LEAVE_DELAY_SECONDS", 120),
            min_gap_minutes=env_float("BACKUP_MIN_GAP_MINUTES", 10),
            file_stable_seconds=env_float("BACKUP_FILE_STABLE_SECONDS", 15),
            keep_local=int(env_float("LOCAL_BACKUP_KEEP", 72)),
            log_file=data_dir / env_str("SERVER_LOG_FILE", "Saved/Logs/Icarus.log"),
            join_regex=re.compile(env_str("PLAYER_JOIN_REGEX", r"Join succeeded: (?P<name>.+?)\s*$")),
            leave_regex=re.compile(env_str(
                "PLAYER_LEAVE_REGEX",
                r"UNetConnection::Close.*GameNetDriver|Player disconnected",
            )),
        )


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #

class Discord:
    def __init__(self, cfg):
        self.url = cfg.webhook_url
        self.username = cfg.discord_username

    @property
    def enabled(self):
        return bool(self.url)

    def send(self, content, file_name=None, file_bytes=None):
        if not self.enabled:
            return
        payload = {"content": content[:2000], "username": self.username, "allowed_mentions": {"parse": []}}
        if file_bytes is None:
            body = json.dumps(payload).encode()
            content_type = "application/json"
        else:
            body, content_type = self._multipart(payload, file_name, file_bytes)
        self._post(body, content_type)

    @staticmethod
    def _multipart(payload, file_name, file_bytes):
        boundary = uuid.uuid4().hex
        parts = [
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="payload_json"\r\n'
            "Content-Type: application/json\r\n\r\n".encode(),
            json.dumps(payload).encode(),
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="files[0]"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode(),
            file_bytes,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _post(self, body, content_type):
        sep = "&" if "?" in self.url else "?"
        url = f"{self.url}{sep}wait=true"
        for attempt in range(1, 6):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": content_type,
                "User-Agent": "icarus-backup (https://github.com/pedrostick3/icarus-server)",
            })
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    resp.read()
                    return
            except urllib.error.HTTPError as err:
                if err.code == 429:
                    retry_after = 5.0
                    try:
                        retry_after = float(json.loads(err.read()).get("retry_after", retry_after))
                    except Exception:
                        pass
                    log.warning("Discord rate limit, a aguardar %.1fs", retry_after)
                    time.sleep(retry_after + 0.5)
                    continue
                if err.code >= 500:
                    log.warning("Discord respondeu %s (tentativa %d)", err.code, attempt)
                    time.sleep(2 ** attempt)
                    continue
                detail = err.read()[:300].decode(errors="replace")
                raise RuntimeError(f"Discord recusou o pedido ({err.code}): {detail}") from err
            except urllib.error.URLError as err:
                log.warning("Erro de rede ao contactar o Discord: %s (tentativa %d)", err.reason, attempt)
                time.sleep(2 ** attempt)
        raise RuntimeError("Não foi possível enviar para o Discord após 5 tentativas")


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #

class NotReady(Exception):
    """Save files are being written or are not valid yet; try again later."""


class Backup:
    def __init__(self, cfg, discord):
        self.cfg = cfg
        self.discord = discord
        self.state_file = cfg.backup_dir / ".state.json"
        self.state = self._load_state()
        self.lock = threading.Lock()

    def _load_state(self):
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.state_file)

    @property
    def last_backup_at(self):
        return self.state.get("last_backup_at", 0)

    def _collect_files(self):
        files = []
        for rel in self.cfg.paths:
            path = self.cfg.data_dir / rel
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(p for p in path.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
        return sorted(set(files))

    def _snapshot(self, files):
        return {str(f): (f.stat().st_mtime, f.stat().st_size) for f in files}

    def _validate(self, files):
        now = time.time()
        newest = max(f.stat().st_mtime for f in files)
        if now - newest < self.cfg.file_stable_seconds:
            raise NotReady("ficheiros alterados há instantes, o servidor pode estar a gravar")
        for f in files:
            if f.suffix.lower() == ".json":
                try:
                    with f.open("rb") as fh:
                        json.load(fh)
                except ValueError as err:
                    raise NotReady(f"{f.name} não é JSON válido ({err})") from err

    def _hash(self, files):
        digest = hashlib.sha256()
        for f in files:
            digest.update(str(f.relative_to(self.cfg.data_dir)).encode())
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def run(self, reason, force=False):
        """Create a backup. Returns True when one was made, False when skipped.
        Raises NotReady if the save is mid-write."""
        with self.lock:
            files = self._collect_files()
            if not files:
                log.info("Sem ficheiros de save para guardar (%s)", ", ".join(self.cfg.paths))
                return False
            self._validate(files)
            before = self._snapshot(files)
            content_hash = self._hash(files)
            if not force and self.cfg.only_if_changed and content_hash == self.state.get("last_hash"):
                log.info("Save sem alterações desde o último backup, ignorado (%s)", reason)
                return False

            self.cfg.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            ascii_reason = unicodedata.normalize("NFKD", reason).encode("ascii", "ignore").decode()
            slug = re.sub(r"[^a-z0-9]+", "-", ascii_reason.lower()).strip("-")[:30]
            zip_path = self.cfg.backup_dir / f"icarus-{stamp}-{slug}.zip"
            tmp_path = zip_path.with_suffix(".zip.tmp")
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for f in files:
                    zf.write(f, f.relative_to(self.cfg.data_dir))
            if self._snapshot(self._collect_files()) != before:
                tmp_path.unlink(missing_ok=True)
                raise NotReady("os ficheiros mudaram durante a compressão")
            tmp_path.replace(zip_path)

            raw_size = sum(f.stat().st_size for f in files)
            log.info("Backup criado: %s (%s → %s)", zip_path.name, human(raw_size), human(zip_path.stat().st_size))
            self._rotate()

            self.state.update(last_hash=content_hash, last_backup_at=time.time(), last_file=zip_path.name)
            self._save_state()

        self._upload(zip_path, reason, files, raw_size)
        return True

    def _rotate(self):
        if self.cfg.keep_local <= 0:
            return
        backups = sorted(self.cfg.backup_dir.glob("icarus-*.zip"))
        for old in backups[:-self.cfg.keep_local]:
            old.unlink()
            log.info("Backup local antigo apagado: %s", old.name)

    def _upload(self, zip_path, reason, files, raw_size):
        if not self.discord.enabled:
            return
        prospects = sorted(f.stem for f in files if f.parent.name == "Prospects" and f.suffix == ".json")
        header = (
            f"📦 **Backup do mundo Icarus** — {reason}\n"
            f"🕒 {datetime.now():%d/%m/%Y %H:%M}  •  💾 {human(raw_size)} → {human(zip_path.stat().st_size)}\n"
            f"🌍 Prospects: {', '.join(prospects) if prospects else '—'}"
        )
        data = zip_path.read_bytes()
        limit = int(self.cfg.discord_max_file_mb * 1024 * 1024)
        if len(data) <= limit:
            self.discord.send(header, zip_path.name, data)
        else:
            chunks = [data[i:i + limit] for i in range(0, len(data), limit)]
            for n, chunk in enumerate(chunks, 1):
                name = f"{zip_path.name}.{n:03d}"
                text = header if n == 1 else ""
                text += (f"\n🧩 Parte {n}/{len(chunks)} — junta as partes com 7-Zip (abre o .001) "
                         f"ou `copy /b {zip_path.name}.0* {zip_path.name}`")
                self.discord.send(text.strip(), name, chunk)
        log.info("Backup enviado para o Discord: %s", zip_path.name)


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


# --------------------------------------------------------------------------- #
# Server log watcher
# --------------------------------------------------------------------------- #

class LogWatcher(threading.Thread):
    """Follows the server log (handling restarts/rotation) and emits player events."""

    def __init__(self, cfg, events):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.events = events

    def run(self):
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
        for kind, regex in (("join", self.cfg.join_regex), ("leave", self.cfg.leave_regex)):
            m = regex.search(line)
            if m:
                name = (m.groupdict().get("name") or "").strip() or None
                log.info("Evento '%s' detetado no log: %s", kind, line.strip()[:200])
                self.events.put((kind, name))
                return


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def try_backup(backup, discord, cfg, reason, force=False):
    """Returns True if the attempt is finished (done or skipped), False to retry later."""
    try:
        backup.run(reason, force=force)
        return True
    except NotReady as err:
        log.info("Backup adiado: %s", err)
        return False
    except Exception as err:
        log.exception("Backup falhou")
        if cfg.notify_errors and discord.enabled:
            try:
                discord.send(f"⚠️ **Backup falhou** ({reason}): `{err}`")
            except Exception:
                log.exception("Não foi possível avisar no Discord")
        return True


def serve(cfg):
    discord = Discord(cfg)
    backup = Backup(cfg, discord)
    events = queue.Queue()

    log.info("Serviço de backup iniciado. Discord: %s | intervalo: %s min | ao sair jogador: %s",
             "sim" if discord.enabled else "NÃO (só local)", cfg.interval_minutes, cfg.on_player_leave)

    if cfg.on_player_leave or cfg.notify_players:
        LogWatcher(cfg, events).start()

    pending = {}  # reason -> earliest time to run
    if cfg.on_start:
        pending["arranque do serviço"] = time.time() + 30
    min_gap = cfg.min_gap_minutes * 60
    interval = cfg.interval_minutes * 60
    last_periodic_check = 0.0

    while True:
        try:
            kind, name = events.get(timeout=5)
            who = name or "um jogador"
            if kind == "join" and cfg.notify_players and discord.enabled and name:
                discord.send(f"🟢 **{name}** entrou no servidor")
            elif kind == "leave":
                if cfg.notify_players and discord.enabled and name:
                    discord.send(f"🔴 **{name}** saiu do servidor")
                if cfg.on_player_leave:
                    pending[f"{who} saiu"] = time.time() + cfg.leave_delay_seconds
        except queue.Empty:
            pass
        except Exception:
            log.exception("Erro a processar evento")

        now = time.time()
        if interval > 0 and not pending and now - max(backup.last_backup_at, last_periodic_check) >= interval:
            pending["backup periódico"] = now

        due = [r for r, t in pending.items() if t <= now]
        if not due:
            continue
        reason = due[0]
        is_periodic = reason == "backup periódico"
        if not is_periodic and now - backup.last_backup_at < min_gap:
            # Too soon after the last one: push it to the end of the gap
            pending[reason] = backup.last_backup_at + min_gap
            continue
        if try_backup(backup, discord, cfg, reason):
            for r in due:
                pending.pop(r, None)
            if is_periodic:
                last_periodic_check = now
        else:
            pending[reason] = now + 30


def main():
    parser = argparse.ArgumentParser(description="Backups dos saves do servidor de Icarus")
    parser.add_argument("--now", action="store_true", help="faz um backup imediato (mesmo sem alterações) e sai")
    parser.add_argument("--test-discord", action="store_true", help="envia uma mensagem de teste para o webhook e sai")
    args = parser.parse_args()

    logging.basicConfig(level=env_str("LOG_LEVEL", "INFO").upper(),
                        format="[backup] %(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    cfg = Config.from_env()

    if args.test_discord:
        discord = Discord(cfg)
        if not discord.enabled:
            sys.exit("DISCORD_WEBHOOK_URL não está definido")
        discord.send("✅ Teste do webhook de backups do Icarus: está a funcionar!")
        print("Mensagem enviada.")
        return
    if args.now:
        discord = Discord(cfg)
        for _ in range(10):
            if try_backup(Backup(cfg, discord), discord, cfg, "manual", force=True):
                return
            time.sleep(10)
        sys.exit("Os saves continuam a ser escritos; tenta outra vez daqui a pouco")
    serve(cfg)


if __name__ == "__main__":
    main()
