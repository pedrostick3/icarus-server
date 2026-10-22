"""Optional second backup destination through rclone (Google Drive, OneDrive, S3...).

Ready to use but off until both RCLONE_REMOTE and the rclone config file exist.
"""

import logging
import subprocess

from .retention import to_delete

log = logging.getLogger(__name__)


class Rclone:
    def __init__(self, remote, config_path, retention):
        self.remote = remote.rstrip("/")
        self.config_path = config_path
        self.retention = retention

    @property
    def enabled(self):
        return bool(self.remote) and self.config_path.is_file()

    def describe(self):
        if not self.remote:
            return "desativado (RCLONE_REMOTE vazio)"
        if not self.config_path.is_file():
            return f"desativado (falta {self.config_path})"
        return self.remote

    def _run(self, *args, timeout=1800):
        cmd = ["rclone", "--config", str(self.config_path), *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"rclone {args[0]} falhou: {result.stderr.strip()[-500:]}")
        return result.stdout

    def upload(self, path):
        self._run("copyto", str(path), f"{self.remote}/{path.name}")
        log.info("Backup enviado via rclone: %s/%s", self.remote, path.name)
        self._apply_retention()

    def _apply_retention(self):
        names = self._run("lsf", "--files-only", "--include", "icarus-*.zip", self.remote, timeout=300).split()
        for name in to_delete(names, self.retention):
            self._run("deletefile", f"{self.remote}/{name}", timeout=300)
            log.info("Backup remoto antigo apagado: %s", name)

    def check(self):
        """Lists the remote to prove the configuration works."""
        self._run("lsf", "--max-depth", "1", self.remote, timeout=120)
