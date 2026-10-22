"""Talks to server/supervisor.sh through files in the shared control volume."""

import fcntl
import json
import time
import uuid
from contextlib import contextmanager

STALE_SECONDS = 60


class Control:
    def __init__(self, control_dir):
        self.dir = control_dir

    def status(self):
        """Supervisor status, with `alive` False if the heartbeat is stale or missing."""
        try:
            status = json.loads((self.dir / "status.json").read_text())
        except (OSError, ValueError):
            status = {"state": "unknown"}
        try:
            status["heartbeat"] = float((self.dir / "heartbeat").read_text())
        except (OSError, ValueError):
            pass
        status["alive"] = time.time() - status.get("heartbeat", 0) < STALE_SECONDS
        if not status["alive"]:
            status["state"] = "offline"
        return status

    @contextmanager
    def lock(self):
        """Cross-process lock so the scheduler, the bot and the CLI never overlap."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".manager.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def run(self, action, timeout, **params):
        """Send a command and wait for its result. Returns (ok, message)."""
        if not self.status()["alive"]:
            return False, "o supervisor do servidor não está a responder (contentor parado?)"
        command_id = uuid.uuid4().hex
        command = {"id": command_id, "action": action, **params}
        tmp = self.dir / "command.json.tmp"
        tmp.write_text(json.dumps(command))
        tmp.replace(self.dir / "command.json")

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            try:
                result = json.loads((self.dir / "result.json").read_text())
            except (OSError, ValueError):
                continue
            if result.get("id") == command_id:
                return bool(result.get("ok")), result.get("message", "")
        return False, f"sem resposta do servidor após {timeout // 60} min"
