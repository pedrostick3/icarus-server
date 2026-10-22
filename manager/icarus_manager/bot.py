"""Optional Discord bot with slash commands. Runs in its own thread/event loop.

Only enabled when DISCORD_BOT_TOKEN is set. Needs no privileged intents.
"""

import asyncio
import logging
import threading

import discord
from discord import app_commands

log = logging.getLogger(__name__)


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

    async def deny_if_not_admin(interaction):
        if _is_admin(cfg, interaction):
            return False
        await interaction.response.send_message("⛔ Não tens permissão para este comando.", ephemeral=True)
        return True

    @tree.command(name="status", description="Estado do servidor de Icarus", guild=guild)
    async def status(interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(await asyncio.to_thread(manager.status_text))

    @tree.command(name="backup", description="Faz um backup do mundo agora", guild=guild)
    async def backup(interaction: discord.Interaction):
        if await deny_if_not_admin(interaction):
            return
        await interaction.response.defer(thinking=True)
        reason = f"pedido por {interaction.user.display_name}"
        await _reply_later(interaction, await asyncio.to_thread(manager.manual_backup, reason))

    @tree.command(name="backups", description="Lista os últimos backups locais", guild=guild)
    async def backups(interaction: discord.Interaction):
        files = manager.backup.list_local()[:10]
        text = "\n".join(f"• `{f.name}`" for f in files) or "Ainda não há backups."
        await interaction.response.send_message(f"**Últimos backups**\n{text}", ephemeral=True)

    @tree.command(name="restart", description="Reinicia o servidor (com backup antes)", guild=guild)
    @app_commands.describe(atualizar="Atualiza o jogo antes de arrancar",
                           forcar="Reinicia mesmo que haja jogadores online")
    async def restart(interaction: discord.Interaction, atualizar: bool = False, forcar: bool = False):
        if await deny_if_not_admin(interaction):
            return
        await interaction.response.defer(thinking=True)
        reason = f"pedido por {interaction.user.display_name}"
        ok, message = await asyncio.to_thread(manager.restart, reason, atualizar, forcar)
        hint = " Usa `forcar: True` para reiniciar na mesma." if not ok and "jogador" in message else ""
        await _reply_later(interaction, f"{'✅' if ok else '⚠️'} {message}.{hint}")

    @tree.command(name="update", description="Atualiza o jogo agora (se houver versão nova)", guild=guild)
    @app_commands.describe(forcar="Atualiza mesmo que haja jogadores online")
    async def update(interaction: discord.Interaction, forcar: bool = False):
        if await deny_if_not_admin(interaction):
            return
        await interaction.response.defer(thinking=True)
        ok, message = await asyncio.to_thread(manager.update_now, forcar)
        await _reply_later(interaction, f"{'✅' if ok else '⚠️'} {message}")

    @client.event
    async def on_ready():
        synced = await tree.sync(guild=guild)
        log.info("Bot do Discord ligado como %s (%d comandos%s)", client.user, len(synced),
                 "" if guild else "; sem DISCORD_GUILD_ID os comandos podem demorar até 1h a aparecer")

    try:
        await client.start(cfg.bot_token)
    except Exception:
        log.exception("O bot do Discord parou")
