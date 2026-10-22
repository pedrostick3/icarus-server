"""Zips the save data, keeps a local rotation and ships it to Discord / rclone."""

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
import zipfile
from datetime import datetime

from .retention import to_delete

log = logging.getLogger(__name__)

SECRET_FILES = {"ServerSettings.ini"}


class NotReady(Exception):
    """Save files are being written or are not valid yet; try again later."""


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


class Backup:
    def __init__(self, cfg, discord, rclone):
        self.cfg = cfg
        self.discord = discord
        self.rclone = rclone
        self.state_file = cfg.backup_dir / ".state.json"
        self.state = self._load_state()
        self.lock = threading.Lock()
        self.last_errors = []

    # ------------------------------------------------------------------ state

    def _load_state(self):
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self):
        self.cfg.backup_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2))
        tmp.replace(self.state_file)

    @property
    def last_backup_at(self):
        return self.state.get("last_backup_at", 0)

    @property
    def last_file(self):
        return self.state.get("last_file")

    # ------------------------------------------------------------------ files

    def _collect_files(self):
        files = []
        for rel in self.cfg.paths:
            path = self.cfg.data_dir / rel
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(p for p in path.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))
        # Holds the passwords and is regenerated from .env anyway: never ship it
        return sorted({f for f in files if f.name not in SECRET_FILES})

    @staticmethod
    def _snapshot(files):
        return {str(f): (f.stat().st_mtime, f.stat().st_size) for f in files}

    def _validate(self, files):
        newest = max(f.stat().st_mtime for f in files)
        if time.time() - newest < self.cfg.file_stable_seconds:
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

    # ------------------------------------------------------------------ backup

    def run(self, reason, force=False):
        """Create a backup and ship it. Returns the zip path, or None if skipped.

        Raises NotReady if the save is mid-write. Upload failures do not raise:
        they are collected in `last_errors`.
        """
        self.last_errors = []
        with self.lock:
            files = self._collect_files()
            if not files:
                log.info("Sem ficheiros de save para guardar (%s)", ", ".join(self.cfg.paths))
                return None
            self._validate(files)
            before = self._snapshot(files)
            content_hash = self._hash(files)
            if not force and self.cfg.only_if_changed and content_hash == self.state.get("last_hash"):
                log.info("Save sem alterações desde o último backup, ignorado (%s)", reason)
                return None

            self.cfg.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            ascii_reason = unicodedata.normalize("NFKD", reason).encode("ascii", "ignore").decode()
            slug = re.sub(r"[^a-z0-9]+", "-", ascii_reason.lower()).strip("-")[:30] or "backup"
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
            self._rotate_local()
            self.state.update(last_hash=content_hash, last_backup_at=time.time(), last_file=zip_path.name)
            self.save_state()

        self.last_errors = self._ship(zip_path, reason, files, raw_size)
        return zip_path

    def _rotate_local(self):
        names = [p.name for p in self.cfg.backup_dir.glob("icarus-*.zip")]
        for name in to_delete(names, self.cfg.local_retention):
            (self.cfg.backup_dir / name).unlink(missing_ok=True)
            log.info("Backup local antigo apagado: %s", name)

    def _ship(self, zip_path, reason, files, raw_size):
        errors = []
        if self.discord.enabled and self.cfg.discord_upload_backups:
            try:
                self._upload_discord(zip_path, reason, files, raw_size)
            except Exception as err:
                log.exception("Envio para o Discord falhou")
                errors.append(f"Discord: {err}")
        if self.rclone.enabled:
            try:
                self.rclone.upload(zip_path)
            except Exception as err:
                log.exception("Envio via rclone falhou")
                errors.append(f"rclone: {err}")
        return errors

    def _upload_discord(self, zip_path, reason, files, raw_size):
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
                text = header if n == 1 else ""
                text += (f"\n🧩 Parte {n}/{len(chunks)} — junta as partes com 7-Zip (abre o .001) "
                         f"ou `copy /b {zip_path.name}.0* {zip_path.name}`")
                self.discord.send(text.strip(), f"{zip_path.name}.{n:03d}", chunk)
        log.info("Backup enviado para o Discord: %s", zip_path.name)

    # ------------------------------------------------------------------ listing / restore

    def list_local(self):
        return sorted(self.cfg.backup_dir.glob("icarus-*.zip"), reverse=True)

    def verify(self, path):
        """Restore test without touching the server: every file must extract and
        every save must parse. Returns the list of prospect names in the backup."""
        prospects = []
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"ficheiro corrompido dentro do zip: {bad}")
            for name in zf.namelist():
                if name.lower().endswith(".json"):
                    with zf.open(name) as fh:
                        try:
                            json.load(fh)
                        except ValueError as err:
                            raise ValueError(f"{name} não é JSON válido ({err})") from err
                    if "/Prospects/" in name:
                        prospects.append(name.rsplit("/", 1)[-1][:-5])
        if not prospects:
            raise ValueError("o backup não tem nenhum prospect")
        return sorted(prospects)

    def prepare_restore(self, name):
        """Resolve a backup name (joining .001/.002 parts if needed) and check the zip."""
        path = self.cfg.backup_dir / name
        if name.endswith(".001"):
            base = name[:-4]
            parts = sorted(self.cfg.backup_dir.glob(f"{base}.[0-9][0-9][0-9]"))
            path = self.cfg.backup_dir / base
            with open(path, "wb") as out:
                for part in parts:
                    out.write(part.read_bytes())
            log.info("Juntadas %d partes em %s", len(parts), path.name)
        if not path.is_file():
            raise FileNotFoundError(f"não existe {path.name} em {self.cfg.backup_dir}")
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"zip corrompido ({bad})")
        return path
