"""All settings come from environment variables (see .env.example)."""

import os
import re
from dataclasses import dataclass, fields
from datetime import time as dtime
from pathlib import Path


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


def env_int(name, default):
    return int(env_float(name, default))


def env_ids(name):
    return {int(x) for x in re.split(r"[,\s]+", env_str(name)) if x.isdigit()}


def env_time(name, default):
    hours, minutes = env_str(name, default).split(":")
    return dtime(int(hours), int(minutes))


@dataclass
class Retention:
    last: int
    daily: int
    weekly: int
    monthly: int

    @classmethod
    def from_env(cls, prefix, last, daily, weekly, monthly):
        return cls(
            last=env_int(f"{prefix}_KEEP_LAST", last),
            daily=env_int(f"{prefix}_KEEP_DAILY", daily),
            weekly=env_int(f"{prefix}_KEEP_WEEKLY", weekly),
            monthly=env_int(f"{prefix}_KEEP_MONTHLY", monthly),
        )


@dataclass
class Config:
    # Paths
    data_dir: Path
    backup_dir: Path
    control_dir: Path

    # Game server
    server_host: str
    query_port: int
    steam_branch: str

    # Discord webhook (backups + notifications)
    webhook_url: str
    discord_username: str
    discord_max_file_mb: float
    discord_upload_backups: bool
    notify_players: bool
    notify_errors: bool
    notify_server_status: bool

    # Discord bot (optional)
    bot_token: str
    bot_guild_id: int
    bot_admin_user_ids: set
    bot_admin_role_ids: set

    # Backup triggers
    interval_minutes: float
    only_if_changed: bool
    on_start: bool
    on_player_leave: bool
    leave_delay_seconds: float
    min_gap_minutes: float
    file_stable_seconds: float
    paths: list

    # Retention
    local_retention: Retention
    rclone_retention: Retention

    # rclone (optional second destination, e.g. Google Drive)
    rclone_remote: str
    rclone_config: Path

    # Player detection
    player_detection: str
    a2s_interval_seconds: float
    log_file: Path
    join_regex: re.Pattern
    leave_regex: re.Pattern

    # Scheduled restart / updates
    daily_restart: bool
    daily_restart_time: dtime
    daily_restart_update: bool
    restart_retry_minutes: float
    restart_give_up_minutes: float
    auto_update: bool
    update_check_minutes: float

    # Monitoring
    healthcheck_url: str
    healthcheck_interval_minutes: float
    disk_min_free_gb: float

    @classmethod
    def from_env(cls):
        data_dir = Path(env_str("DATA_DIR", "/data"))
        return cls(
            data_dir=data_dir,
            backup_dir=Path(env_str("BACKUP_DIR", "/backups")),
            control_dir=Path(env_str("CONTROL_DIR", "/control")),
            server_host=env_str("SERVER_HOST", "icarus"),
            query_port=env_int("QUERY_PORT", 27015),
            steam_branch=env_str("STEAM_BRANCH", "public"),
            webhook_url=env_str("DISCORD_WEBHOOK_URL"),
            discord_username=env_str("DISCORD_USERNAME", "Icarus"),
            discord_max_file_mb=env_float("DISCORD_MAX_FILE_MB", 9.5),
            discord_upload_backups=env_bool("DISCORD_UPLOAD_BACKUPS", True),
            notify_players=env_bool("DISCORD_NOTIFY_PLAYERS", True),
            notify_errors=env_bool("DISCORD_NOTIFY_ERRORS", True),
            notify_server_status=env_bool("DISCORD_NOTIFY_SERVER_STATUS", True),
            bot_token=env_str("DISCORD_BOT_TOKEN"),
            bot_guild_id=env_int("DISCORD_GUILD_ID", 0),
            bot_admin_user_ids=env_ids("DISCORD_ADMIN_USER_IDS"),
            bot_admin_role_ids=env_ids("DISCORD_ADMIN_ROLE_IDS"),
            interval_minutes=env_float("BACKUP_INTERVAL_MINUTES", 60),
            only_if_changed=env_bool("BACKUP_ONLY_IF_CHANGED", True),
            on_start=env_bool("BACKUP_ON_START", True),
            on_player_leave=env_bool("BACKUP_ON_PLAYER_LEAVE", True),
            leave_delay_seconds=env_float("BACKUP_LEAVE_DELAY_SECONDS", 120),
            min_gap_minutes=env_float("BACKUP_MIN_GAP_MINUTES", 10),
            file_stable_seconds=env_float("BACKUP_FILE_STABLE_SECONDS", 15),
            paths=[p.strip() for p in env_str("BACKUP_PATHS", "Saved/PlayerData/DedicatedServer").split(",") if p.strip()],
            local_retention=Retention.from_env("LOCAL", last=24, daily=7, weekly=4, monthly=6),
            rclone_retention=Retention.from_env("RCLONE", last=48, daily=30, weekly=12, monthly=12),
            rclone_remote=env_str("RCLONE_REMOTE"),
            rclone_config=Path(env_str("RCLONE_CONFIG", "/config/rclone/rclone.conf")),
            player_detection=env_str("PLAYER_DETECTION", "auto").lower(),
            a2s_interval_seconds=env_float("A2S_INTERVAL_SECONDS", 20),
            log_file=data_dir / env_str("SERVER_LOG_FILE", "Saved/Logs/Icarus.log"),
            join_regex=re.compile(env_str("PLAYER_JOIN_REGEX", r"Join succeeded: (?P<name>.+?)\s*$")),
            leave_regex=re.compile(env_str(
                "PLAYER_LEAVE_REGEX",
                r"UNetConnection::Close.*GameNetDriver|Player disconnected",
            )),
            daily_restart=env_bool("DAILY_RESTART_ENABLED", True),
            daily_restart_time=env_time("DAILY_RESTART_TIME", "07:00"),
            daily_restart_update=env_bool("DAILY_RESTART_UPDATE", True),
            restart_retry_minutes=env_float("DAILY_RESTART_RETRY_MINUTES", 15),
            restart_give_up_minutes=env_float("DAILY_RESTART_GIVE_UP_MINUTES", 240),
            auto_update=env_bool("AUTO_UPDATE_ENABLED", True),
            update_check_minutes=env_float("UPDATE_CHECK_MINUTES", 30),
            healthcheck_url=env_str("HEALTHCHECK_PING_URL"),
            healthcheck_interval_minutes=env_float("HEALTHCHECK_INTERVAL_MINUTES", 5),
            disk_min_free_gb=env_float("DISK_MIN_FREE_GB", 5),
        )

    def summary(self):
        hidden = {"webhook_url", "bot_token", "healthcheck_url"}
        out = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in hidden:
                value = "definido" if value else "—"
            elif isinstance(value, re.Pattern):
                value = value.pattern
            out.append(f"{f.name}={value}")
        return ", ".join(out)
