#!/usr/bin/env bash
# Root phase: fix ownership of mounted dirs, then run the supervisor as the steam user.
set -euo pipefail

log() { echo "[entrypoint] $(date '+%F %T') $*"; }

if [[ "$(id -u)" == "0" ]]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  groupmod -o -g "$PGID" steam
  usermod -o -u "$PUID" steam
  for dir in "$DATA_DIR" "$GAME_DIR" "$CONTROL_DIR" /home/steam "$STEAMCMD_DIR"; do
    mkdir -p "$dir"
    if [[ "$(stat -c %u "$dir")" != "$PUID" ]]; then
      log "A corrigir permissões de $dir"
      chown -R steam:steam "$dir"
    fi
  done
  exec gosu steam /usr/local/bin/supervisor.sh "$@"
fi

exec /usr/local/bin/supervisor.sh "$@"
