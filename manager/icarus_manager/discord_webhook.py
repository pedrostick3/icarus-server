"""Minimal Discord webhook client (stdlib only)."""

import json
import logging
import time
import urllib.error
import urllib.request
import uuid

log = logging.getLogger(__name__)


class Discord:
    def __init__(self, url, username):
        self.url = url
        self.username = username

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

    def try_send(self, content):
        """Notification that must never break the caller."""
        try:
            self.send(content)
        except Exception:
            log.exception("Não foi possível enviar mensagem para o Discord")

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
                "User-Agent": "icarus-manager (https://github.com/pedrostick3/icarus-server)",
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
