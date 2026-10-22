"""Optional Discord bot with slash commands. Runs in its own thread/event loop.

Only enabled when DISCORD_BOT_TOKEN is set. Needs no privileged intents.
Commands that change the server need an admin AND the password from
DISCORD_BOT_COMMAND_PASSWORD, typed in a private pop-up (never in the channel).
"""

import asyncio
import hmac
import logging
import threading
import time

import discord
from discord import app_commands

log = logging.getLogger(__name__)

LOCKOUT_SECONDS = 15 * 60


# The bot never uses voice: silence the "PyNaCl is not installed" warning
discord.VoiceClient.warn_nacl = False


def start_bot(manager):
    threading.Thread(target=lambda: asyncio.run(_run(manager)), daemon=True, name="discord-bot").start()


def _is_admin(cfg, interaction):
    user = interaction.user
    if user.id in cfg.bot_admin_user_ids:
        return True
    roles = {role.id for role in getattr(user, "roles", [])}
    if cfg.bot_admin_role_ids:
        return bool(roles & cfg.bot_admin_role_ids)
    if cfg.bot_admin_user_ids:
        return False
    # Nothing configured: fall back to the Discord server's administrators
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.administrator)


class PasswordGuard:
    """Checks the command password and locks a user out after repeated failures."""

    def __init__(self, password, max_failures):
        self.password = password
        self.max_failures = max(1, max_failures)
        self.failures = {}  # user id -> timestamps of recent failures

    @property
    def configured(self):
        return bool(self.password)

    def _recent(self, user_id):
        now = time.time()
        recent = [t for t in self.failures.get(user_id, []) if now - t < LOCKOUT_SECONDS]
        self.failures[user_id] = recent
        return recent

    def locked(self, user_id):
        return len(self._recent(user_id)) >= self.max_failures

    def check(self, user_id, attempt):
        """Returns True if accepted. Failures are counted; success clears them."""
        if not self.configured or self.locked(user_id):
            return False
        if hmac.compare_digest(attempt.encode(), self.password.encode()):
            self.failures.pop(user_id, None)
            return True
        self._recent(user_id).append(time.time())
        return False


class PasswordModal(discord.ui.Modal):
    def __init__(self, title, on_success):
        super().__init__(title=title[:45], timeout=120)
        self.on_success = on_success
        self.password = discord.ui.TextInput(label="Password dos comandos", placeholder="definida no .env",
                                             min_length=1, max_length=200)
        self.add_item(self.password)

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_success(interaction, self.password.value)


async def _reply_later(interaction, text):
    """Follow-ups expire after 15 min; long updates fall back to a channel message."""
    try:
        await interaction.followup.send(text)
    except discord.HTTPException:
        if interaction.channel:
            await interaction.channel.send(text)


async def _run(manager):
    cfg = manager.cfg
    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)
    guild = discord.Object(id=cfg.bot_guild_id) if cfg.bot_guild_id else None
    guard = PasswordGuard(cfg.bot_command_password, cfg.bot_max_failed_attempts)

    async def protected(interaction, title, action):
        """Admin check -> password pop-up -> run `action(interaction)` (already deferred)."""
        user = interaction.user
        if not _is_admin(cfg, interaction):
            await interaction.response.send_message("⛔ Não tens permissão para este comando.", ephemeral=True)
            return
        if not guard.configured:
            await interaction.response.send_message(
                "🔒 Comando desativado: falta definir `DISCORD_BOT_COMMAND_PASSWORD` no `.env`.", ephemeral=True)
            return
        if guard.locked(user.id):
            await interaction.response.send_message(
                "🔒 Demasiadas passwords erradas. Tenta outra vez daqui a 15 min.", ephemeral=True)
            return

        async def on_password(modal_interaction, attempt):
            if not guard.check(user.id, attempt):
                log.warning("Password errada no bot (utilizador %s, %s)", user, user.id)
                if guard.locked(user.id):
                    manager.notify(f"🔒 **{user.display_name}** errou a password dos comandos "
                                   f"{guard.max_failures} vezes; bloqueado durante 15 min.")
                await modal_interaction.response.send_message("❌ Password errada.", ephemeral=True)
                return
            await modal_interaction.response.defer(thinking=True)
            await action(modal_interaction)

        await interaction.response.send_modal(PasswordModal(title, on_password))

    @tree.command(name="status", description="Estado do servidor de Icarus", guild=guild)
    async def status(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(await asyncio.to_thread(manager.status_text))

    @tree.command(name="ligar", description="Instruções para entrar no servidor de Icarus", guild=guild)
    async def ligar(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        text = await asyncio.to_thread(manager.connect_text)
        await interaction.followup.send(text[:2000], ephemeral=True)

    @tree.command(name="backups", description="Lista os últimos backups locais", guild=guild)
    async def backups(interaction: discord.Interaction):
        files = manager.backup.list_local()[:10]
        text = "\n".join(f"• `{f.name}`" for f in files) or "Ainda não há backups."
        await interaction.response.send_message(f"**Últimos backups**\n{text}", ephemeral=True)

    @tree.command(name="backup", description="Faz um backup do mundo agora (pede password)", guild=guild)
    async def backup(interaction: discord.Interaction):
        async def action(i):
            reason = f"pedido por {i.user.display_name}"
            await _reply_later(i, await asyncio.to_thread(manager.manual_backup, reason))
        await protected(interaction, "Backup do mundo", action)

    @tree.command(name="restart", description="Reinicia o servidor com backup antes (pede password)", guild=guild)
    @app_commands.describe(atualizar="Atualiza o jogo antes de arrancar",
                           forcar="Reinicia mesmo que haja jogadores online")
    async def restart(interaction: discord.Interaction, atualizar: bool = False, forcar: bool = False):
        async def action(i):
            reason = f"pedido por {i.user.display_name}"
            ok, message = await asyncio.to_thread(manager.restart, reason, atualizar, forcar)
            hint = " Usa `forcar: True` para reiniciar na mesma." if not ok and "jogador" in message else ""
            await _reply_later(i, f"{'✅' if ok else '⚠️'} {message}.{hint}")
        await protected(interaction, "Reiniciar o servidor", action)

    @tree.command(name="update", description="Atualiza o jogo se houver versão nova (pede password)", guild=guild)
    @app_commands.describe(forcar="Atualiza mesmo que haja jogadores online")
    async def update(interaction: discord.Interaction, forcar: bool = False):
        async def action(i):
            ok, message = await asyncio.to_thread(manager.update_now, forcar)
            await _reply_later(i, f"{'✅' if ok else '⚠️'} {message}")
        await protected(interaction, "Atualizar o jogo", action)

    @client.event
    async def on_ready():
        try:
            synced = await tree.sync(guild=guild)
        except discord.Forbidden:
            log.error("O Discord recusou registar os comandos no servidor %s (Missing Access). Confirma que "
                      "DISCORD_GUILD_ID é o ID do servidor Discord (não de um canal) e que o bot foi convidado "
                      "com os scopes 'bot' e 'applications.commands' (ver README, secção do bot).",
                      cfg.bot_guild_id or "global")
            return
        log.info("Bot do Discord ligado como %s (%d comandos%s%s)", client.user, len(synced),
                 "" if guild else "; sem DISCORD_GUILD_ID os comandos podem demorar até 1h a aparecer",
                 "" if guard.configured else "; SEM password: /backup /restart /update desativados")

    try:
        await client.start(cfg.bot_token)
    except Exception:
        log.exception("O bot do Discord parou")
