"""The manager: backups, server monitoring, scheduled restart and game updates."""

import logging
import queue
import shutil
import threading
import time
import urllib.request
from datetime import datetime, timedelta

from . import a2s, steam
from .backup import Backup, NotReady, human
from .control import Control
from .ddns import DuckDNS
from .discord_webhook import Discord
from .players import PlayerTracker
from .rclone import Rclone

log = logging.getLogger(__name__)

PERIODIC = "backup periódico"
HEALTH_LABELS = {
    "online": "🟢 Online",
    "starting": "🟡 A arrancar",
    "maintenance": "🛠️ Em manutenção (reinício/atualização)",
    "unresponsive": "🟠 Não responde",
    "crashed": "💥 Foi abaixo (a reiniciar)",
    "crash_loop": "🛑 Parado após várias falhas",
    "stopped": "⏹️ Parado",
    "offline": "🔴 Offline",
}


class Manager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.discord = Discord(cfg.webhook_url, cfg.discord_username)
        self.rclone = Rclone(cfg.rclone_remote, cfg.rclone_config, cfg.rclone_retention)
        self.backup = Backup(cfg, self.discord, self.rclone)
        self.control = Control(cfg.control_dir)
        self.events = queue.Queue()
        self.tracker = PlayerTracker(cfg, self.events)
        self.tracker_started = False

        self.pending = {}               # backup reason -> earliest time to run
        self.last_periodic_check = 0.0
        self.health = None
        self.health_since = time.time()
        self.last_started_at = None
        self.in_restart = threading.Event()
        self.latest_build = None
        self.pending_update = None
        self.update_announced = None
        self.update_failed_at = {}
        self.next_update_check = 0.0
        self.daily_done = None
        self.daily_next_try = 0.0
        self.daily_postponed = False
        self.next_ping = 0.0
        self.next_disk_check = 0.0
        self.disk_warned_on = None
        self.duckdns = DuckDNS(cfg.duckdns_domain, cfg.duckdns_token)
        self.next_ddns = 0.0
        self.ddns_failing = False
        self.next_restore_test = 0.0

    # ================================================================== helpers

    def notify(self, text):
        self.discord.try_send(text)

    def players(self):
        """Current player count (0 if unknown)."""
        if self.tracker_started:
            return self.tracker.count
        try:
            return a2s.query_info(self.cfg.server_host, self.cfg.query_port)["players"]
        except a2s.A2SError:
            return 0

    def backup_blocking(self, reason, force=False, wait_seconds=120):
        """Backup that waits for the save to settle. Returns (zip_path|None, message)."""
        deadline = time.time() + wait_seconds
        while True:
            try:
                path = self.backup.run(reason, force=force)
            except NotReady as err:
                if time.time() >= deadline:
                    return None, f"os saves continuam a ser escritos ({err})"
                time.sleep(10)
                continue
            except Exception as err:
                log.exception("Backup falhou")
                return None, f"falhou: {err}"
            if path is None:
                return None, "sem alterações desde o último backup"
            extra = f" ⚠️ {'; '.join(self.backup.last_errors)}" if self.backup.last_errors else ""
            return path, f"{path.name} ({human(path.stat().st_size)}){extra}"

    # ================================================================== actions (CLI / bot / scheduler)

    def manual_backup(self, reason="manual"):
        path, message = self.backup_blocking(reason, force=True)
        return f"📦 Backup criado: {message}" if path else f"⚠️ Backup não feito: {message}"

    def restart(self, reason, update=False, force=False):
        """Graceful restart (optionally with game update). Returns (ok, message)."""
        with self.control.lock():
            online = self.players()
            if online > 0 and not force:
                return False, f"há {online} jogador(es) online; não reiniciei"
            self.in_restart.set()
            try:
                what = "atualizar e reiniciar" if update else "reiniciar"
                self.notify(f"♻️ A {what} o servidor — {reason}")
                _, backup_msg = self.backup_blocking("antes de reiniciar")
                log.info("Backup antes do reinício: %s", backup_msg)
                ok, message = self.control.run("restart", timeout=45 * 60 if update else 10 * 60, update=update)
            finally:
                self.tracker.reset()
                self.in_restart.clear()
            self.notify(f"✅ {message}" if ok else f"⚠️ Reinício com problemas: {message}")
            return ok, message

    def update_now(self, force=False):
        try:
            latest = steam.latest_buildid(self.cfg.steam_branch)
        except Exception as err:
            latest = None
            log.warning("Não consegui ver a última versão na Steam: %s", err)
        installed = self.control.status().get("buildid")
        if latest and installed == latest and not force:
            return True, f"já está na última versão (build {installed})"
        return self.restart("atualização pedida manualmente", update=True, force=force)

    def restore(self, name, force=False):
        path = self.backup.prepare_restore(name)
        with self.control.lock():
            online = self.players()
            if online > 0 and not force:
                return False, f"há {online} jogador(es) online; usa --force para restaurar na mesma"
            self.in_restart.set()
            try:
                self.notify(f"⏪ A restaurar o backup **{path.name}**")
                self.backup_blocking("antes de restaurar")
                ok, message = self.control.run("restore", timeout=15 * 60, file=path.name)
            finally:
                self.tracker.reset()
                self.in_restart.clear()
            self.notify(f"✅ {message}" if ok else f"⚠️ Restauro falhou: {message}")
            return ok, message

    # ================================================================== status

    def compute_health(self, status):
        state = status.get("state")
        if state == "running":
            started = _parse_time(status.get("started_at"))
            up_for = time.time() - started if started else 0
            if not self.tracker.a2s_ever_ok:
                return "online"  # A2S unavailable: trust the process
            if self.tracker.a2s_responding:
                return "online"
            return "unresponsive" if up_for > 600 else "starting"
        if state in ("starting", "updating", "stopping", "restoring"):
            return "maintenance"
        if state == "stopped" and self.in_restart.is_set():
            return "maintenance"
        return state if state in ("crashed", "crash_loop", "stopped") else "offline"

    def status_text(self):
        status = self.control.status()
        health = self.compute_health(status)
        lines = [f"**Servidor Icarus** — {HEALTH_LABELS.get(health, health)}"]
        info = self.tracker.info if self.tracker.a2s_responding else None
        count = self.players()
        max_players = info["max_players"] if info else "?"
        names = ", ".join(self.tracker.names) if self.tracker.names else ""
        lines.append(f"👥 Jogadores: {count}/{max_players}{f' ({names})' if names else ''}")
        build = status.get("buildid") or "?"
        if self.pending_update:
            build += f" → nova versão {self.pending_update} pendente (aplica quando estiver vazio)"
        lines.append(f"🏷️ Build: {build}")
        started = _parse_time(status.get("started_at"))
        if started and health == "online":
            lines.append(f"⏱️ Online há {_ago(started)}")
        if self.backup.last_file:
            lines.append(f"📦 Último backup: {self.backup.last_file} (há {_ago(self.backup.last_backup_at)})")
        if self.cfg.daily_restart:
            lines.append(f"⏰ Reinício diário: {self.cfg.daily_restart_time:%H:%M} (só se estiver vazio)")
        if self.duckdns.enabled:
            lines.append(f"🌐 Endereço: `{self.duckdns.hostname}:{self.cfg.game_port}`")
        lines.append(f"☁️ rclone: {self.rclone.describe()}")
        lines.append(f"💽 Disco livre: {human(shutil.disk_usage(self.cfg.backup_dir).free)}")
        return "\n".join(lines)

    def connect_text(self):
        """Step-by-step instructions for a player to join, safe for a shared channel."""
        cfg = self.cfg
        health = self.compute_health(self.control.status())
        name = cfg.server_name
        if health == "online":
            state = f"🟢 Online agora ({self.players()} a jogar)."
        elif health in ("maintenance", "starting"):
            state = "🛠️ Está a reiniciar/atualizar; espera uns minutos e vê `/status`."
        else:
            state = f"{HEALTH_LABELS.get(health, health)} — de momento não dá para entrar; avisa o admin."
        lines = [
            f"## 🎮 Como entrar no servidor **{name}**",
            f"**Estado:** {state}",
            "",
            "**1. Atualiza o jogo**",
            "• O servidor está sempre na última versão do Icarus. Deixa a Steam atualizar o jogo antes de entrar.",
            "",
            "**2. Encontra o servidor**",
            "• Abre o Icarus → **Play** → separador **Dedicated Servers**.",
            f"• Pesquisa por **{name}**, seleciona-o e carrega em **Join**.",
        ]
        if self.duckdns.enabled:
            lines += [
                "• Se não aparecer na lista, usa a ligação direta (*Direct Connect*) com "
                f"`{self.duckdns.hostname}:{cfg.game_port}`, ou adiciona-o na Steam: *Ver* → *Servidores de jogo* "
                f"→ *Favoritos* → ➕ `{self.duckdns.hostname}:{cfg.query_port}`.",
            ]
        lines += [
            "",
            "**3. Password do servidor**",
            f"• O jogo pede-a ao entrar. {cfg.connect_password_hint}",
            "",
            "**4. Personagem e mundo**",
            "• Escolhe a tua personagem (a mesma de sempre).",
        ]
        if cfg.load_prospect:
            lines.append(f"• Se ficares no lobby, escolhe o mundo **{cfg.load_prospect}** e entra.")
        lines += [
            "",
            "**Problemas?**",
            "• *Versão diferente* / não entra → fecha o jogo, reinicia a Steam para forçar a atualização e tenta "
            "outra vez. Se for o servidor a atualizar, o canal avisa quando estiver 🟢 online.",
            "• Não aparece na lista → confirma o estado com `/status` e vê se os filtros da lista escondem "
            "servidores com password.",
            "• O mundo é guardado automaticamente (backups neste canal); não tens de fazer nada ao sair.",
        ]
        if cfg.connect_extra_info:
            lines += ["", cfg.connect_extra_info]
        return "\n".join(lines)

    # ================================================================== main loop

    def serve(self):
        cfg = self.cfg
        log.info("Manager iniciado. Discord: %s | bot: %s | rclone: %s | intervalo backup: %s min | "
                 "reinício diário: %s | auto-update: %s | DuckDNS: %s",
                 "sim" if self.discord.enabled else "não", "sim" if cfg.bot_token else "não",
                 self.rclone.describe(), cfg.interval_minutes,
                 cfg.daily_restart_time.strftime("%H:%M") if cfg.daily_restart else "não", cfg.auto_update,
                 self.duckdns.hostname if self.duckdns.enabled else "não")
        log.debug("Config: %s", cfg.summary())

        self.tracker.start()
        self.tracker_started = True
        if cfg.bot_token:
            from .bot import start_bot
            start_bot(self)
        if cfg.on_start:
            self.pending["arranque do manager"] = time.time() + 30

        while True:
            for step in (self._handle_events, self._monitor_server, self._schedule_backups,
                         self._daily_restart, self._check_updates, self._ping_healthcheck, self._check_disk,
                         self._update_ddns, self._restore_test):
                try:
                    step()
                except Exception:
                    log.exception("Erro em %s", step.__name__)

    # ------------------------------------------------------------------ players

    def _handle_events(self):
        try:
            kind, name = self.events.get(timeout=5)
        except queue.Empty:
            return
        who = name or "Um jogador"
        count = self.tracker.count
        if self.cfg.notify_players:
            icon, verb = ("🟢", "entrou") if kind == "join" else ("🔴", "saiu")
            self.notify(f"{icon} **{who}** {verb} no servidor ({count} online)")
        if kind == "leave" and self.cfg.on_player_leave:
            self.pending[f"{who} saiu"] = time.time() + self.cfg.leave_delay_seconds

    # ------------------------------------------------------------------ backups

    def _schedule_backups(self):
        cfg, now = self.cfg, time.time()
        interval = cfg.interval_minutes * 60
        if interval > 0 and not self.pending and now - max(self.backup.last_backup_at, self.last_periodic_check) >= interval:
            self.pending[PERIODIC] = now

        due = [r for r, t in self.pending.items() if t <= now]
        if not due:
            return
        reason = due[0]
        min_gap = cfg.min_gap_minutes * 60
        if reason != PERIODIC and now - self.backup.last_backup_at < min_gap:
            # Too soon after the last one: push it to the end of the gap
            self.pending[reason] = self.backup.last_backup_at + min_gap
            return
        try:
            self.backup.run(reason)
        except NotReady as err:
            log.info("Backup adiado: %s", err)
            self.pending[reason] = now + 30
            return
        except Exception as err:
            log.exception("Backup falhou")
            if cfg.notify_errors:
                self.notify(f"⚠️ **Backup falhou** ({reason}): `{err}`")
        else:
            if self.backup.last_errors and cfg.notify_errors:
                self.notify(f"⚠️ Backup feito mas com falhas no envio: {'; '.join(self.backup.last_errors)}")
        for r in due:
            self.pending.pop(r, None)
        if reason == PERIODIC:
            self.last_periodic_check = now

    # ------------------------------------------------------------------ server status

    def _monitor_server(self):
        status = self.control.status()
        if status.get("started_at") != self.last_started_at:
            if self.last_started_at is not None:
                self.tracker.reset()
            self.last_started_at = status.get("started_at")

        health = self.compute_health(status)
        if health == self.health:
            return
        # A container that is briefly unreachable is not worth a message
        if health == "offline" and time.time() - self.control_last_alive() < 120:
            return
        previous, self.health, self.health_since = self.health, health, time.time()
        log.info("Estado do servidor: %s -> %s", previous, health)
        if not self.cfg.notify_server_status or (previous is None and health == "online"):
            return
        messages = {
            "online": f"🟢 **Servidor online** (build {status.get('buildid') or '?'})",
            "unresponsive": "🟠 O servidor está a correr mas deixou de responder a queries há uns minutos.",
            "crashed": "💥 O servidor foi abaixo; a reiniciar automaticamente…",
            "crash_loop": "🛑 **O servidor foi abaixo várias vezes seguidas e ficou parado.** "
                          "Vê `docker compose logs icarus` e usa /restart quando estiver resolvido.",
            "stopped": "⏹️ Servidor parado.",
            "offline": self._offline_message(),
        }
        if health in messages:
            self.notify(messages[health])

    def _offline_message(self):
        last = self.control_last_alive()
        if not last:
            return ("🔴 **O servidor nunca deu sinal de vida.** O contentor `icarus` não arrancou ou está a "
                    "reiniciar em ciclo: vê `docker compose ps` e `docker compose logs icarus --tail 100`.")
        return (f"🔴 **O servidor não está a responder** (último sinal há {_ago(last)}). PC desligado, "
                "Docker parado ou contentor em baixo? Vê `docker compose ps` e `docker compose logs icarus`.")

    def control_last_alive(self):
        return self.control.status().get("heartbeat", 0)

    # ------------------------------------------------------------------ daily restart

    def _daily_restart(self):
        cfg = self.cfg
        if not cfg.daily_restart:
            return
        now = datetime.now()
        target = datetime.combine(now.date(), cfg.daily_restart_time)
        if now < target or self.daily_done == now.date() or time.time() < self.daily_next_try:
            return
        status = self.control.status()
        started = _parse_time(status.get("started_at"))
        if started and started >= target.timestamp():
            self._daily_finish(now)  # already (re)started after today's time, e.g. the PC rebooted
            return
        if not status.get("alive"):
            self._daily_finish(now)
            return
        give_up = target + timedelta(minutes=cfg.restart_give_up_minutes)
        online = self.players()
        if online > 0:
            if now >= give_up:
                self.notify(f"⏰ Reinício diário cancelado hoje: continua a haver jogadores online desde as "
                            f"{target:%H:%M}.")
                self._daily_finish(now)
                return
            if not self.daily_postponed:
                self.notify(f"⏰ Reinício diário adiado: {online} jogador(es) online. Volto a tentar a cada "
                            f"{cfg.restart_retry_minutes:g} min até às {give_up:%H:%M}.")
                self.daily_postponed = True
            self.daily_next_try = time.time() + cfg.restart_retry_minutes * 60
            return
        self.restart("reinício diário", update=cfg.daily_restart_update)
        self._daily_finish(now)

    def _daily_finish(self, now):
        self.daily_done = now.date()
        self.daily_postponed = False
        self.daily_next_try = 0.0

    # ------------------------------------------------------------------ updates

    def _check_updates(self):
        cfg = self.cfg
        if not cfg.auto_update:
            return
        now = time.time()
        if now >= self.next_update_check:
            self.next_update_check = now + cfg.update_check_minutes * 60
            try:
                self.latest_build = steam.latest_buildid(cfg.steam_branch)
            except Exception as err:
                log.warning("Não consegui ver a última versão na Steam: %s", err)
            installed = self.control.status().get("buildid")
            if self.latest_build and installed and self.latest_build.isdigit() and installed.isdigit() \
                    and int(self.latest_build) > int(installed):
                self.pending_update = self.latest_build
            else:
                self.pending_update = None

        if not self.pending_update or self.health not in ("online", "unresponsive"):
            return
        if self.update_announced != self.pending_update:
            self.update_announced = self.pending_update
            self.notify(f"🆕 Há uma nova versão do Icarus (build {self.pending_update}). "
                        "Atualizo o servidor assim que não houver ninguém a jogar.")
        if now - self.update_failed_at.get(self.pending_update, 0) < 2 * 3600:
            return
        if self.players() > 0:
            return
        build = self.pending_update
        ok, _ = self.restart(f"nova versão do jogo (build {build})", update=True)
        if ok and self.control.status().get("buildid") == build:
            self.pending_update = None
        else:
            self.update_failed_at[build] = time.time()
            self.notify(f"⚠️ A atualização para a build {build} não ficou aplicada; tento outra vez daqui a 2h.")

    # ------------------------------------------------------------------ monitoring

    def _ping_healthcheck(self):
        if not self.cfg.healthcheck_url or time.time() < self.next_ping:
            return
        self.next_ping = time.time() + self.cfg.healthcheck_interval_minutes * 60
        ok = self.health in ("online", "starting", "maintenance", "crashed")
        url = self.cfg.healthcheck_url.rstrip("/") + ("" if ok else "/fail")
        try:
            urllib.request.urlopen(url, timeout=15).read()
        except Exception as err:
            log.warning("Ping de healthcheck falhou: %s", err)

    def _check_disk(self):
        if time.time() < self.next_disk_check:
            return
        self.next_disk_check = time.time() + 3600
        free = shutil.disk_usage(self.cfg.backup_dir).free
        today = datetime.now().date()
        if free < self.cfg.disk_min_free_gb * 1024 ** 3 and self.disk_warned_on != today:
            self.disk_warned_on = today
            self.notify(f"💽 Pouco espaço em disco no servidor: {human(free)} livres.")

    # ------------------------------------------------------------------ dynamic DNS

    def _update_ddns(self):
        if not self.duckdns.enabled or time.time() < self.next_ddns:
            return
        self.next_ddns = time.time() + self.cfg.ddns_interval_minutes * 60
        try:
            changed = self.duckdns.update()
        except Exception as err:
            log.warning("DuckDNS falhou: %s", err)
            if not self.ddns_failing and self.cfg.notify_errors:
                self.notify(f"⚠️ Não consegui atualizar o DuckDNS: `{err}`")
            self.ddns_failing = True
            return
        self.ddns_failing = False
        if changed:
            # The IP itself is not posted: the channel is shared
            self.notify(f"🌐 O IP público de casa mudou; `{self.duckdns.hostname}` já foi atualizado.")

    # ------------------------------------------------------------------ restore test

    def _restore_test(self):
        days = self.cfg.restore_test_days
        if days <= 0 or time.time() < self.next_restore_test:
            return
        self.next_restore_test = time.time() + 3600
        if time.time() - self.backup.state.get("last_restore_test_at", 0) < days * 86400:
            return
        backups = self.backup.list_local()
        if not backups:
            return
        latest = backups[0]
        try:
            prospects = self.backup.verify(latest)
        except Exception as err:
            log.exception("Teste de restauro falhou")
            self.notify(f"🧪❌ **Teste de restauro falhou** para `{latest.name}`: {err}. "
                        "Confirma os backups o quanto antes!")
        else:
            log.info("Teste de restauro OK: %s (%s)", latest.name, ", ".join(prospects))
            self.notify(f"🧪 Teste de restauro OK: `{latest.name}` extrai sem erros "
                        f"(prospects: {', '.join(prospects)}).")
        self.backup.state["last_restore_test_at"] = time.time()
        self.backup.save_state()


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _ago(timestamp):
    seconds = int(time.time() - timestamp)
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    return f"{seconds // 86400} d {seconds % 86400 // 3600} h"
