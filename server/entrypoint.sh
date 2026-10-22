#!/usr/bin/env bash
set -euo pipefail

APP_ID=2089300
SERVER_EXE="$GAME_DIR/Icarus/Binaries/Win64/IcarusServer-Win64-Shipping.exe"
INI_FILE="$DATA_DIR/Saved/Config/WindowsServer/ServerSettings.ini"
PROSPECTS_DIR="$DATA_DIR/Saved/PlayerData/DedicatedServer/Prospects"
LOG_FILE="$DATA_DIR/Saved/Logs/Icarus.log"

log() { echo "[entrypoint] $(date '+%F %T') $*"; }

# --- Root phase: fix ownership, then re-run this script as the steam user ---
if [[ "$(id -u)" == "0" ]]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  groupmod -o -g "$PGID" steam
  usermod -o -u "$PUID" steam
  for dir in "$DATA_DIR" "$GAME_DIR" /home/steam "$STEAMCMD_DIR"; do
    mkdir -p "$dir"
    if [[ "$(stat -c %u "$dir")" != "$PUID" ]]; then
      log "A corrigir permissões de $dir"
      chown -R steam:steam "$dir"
    fi
  done
  exec gosu steam "$0" "$@"
fi

# --- Game install / update ---
steamcmd_update() {
  local branch_args=()
  [[ -n "${STEAM_BRANCH:-}" ]] && branch_args=(-beta "$STEAM_BRANCH")
  local attempt
  for attempt in 1 2 3; do
    log "SteamCMD: a instalar/atualizar Icarus (tentativa $attempt)"
    if "$STEAMCMD_DIR/steamcmd.sh" \
        +@sSteamCmdForcePlatformType windows \
        +force_install_dir "$GAME_DIR" \
        +login anonymous \
        +app_update "$APP_ID" "${branch_args[@]}" validate \
        +quit; then
      [[ -f "$SERVER_EXE" ]] && return 0
    fi
    sleep 5
  done
  log "ERRO: SteamCMD falhou 3 vezes"
  return 1
}

if [[ ! -f "$SERVER_EXE" || "${UPDATE_ON_START:-true}" == "true" ]]; then
  steamcmd_update
fi

# --- Wine prefix (not persisted; recreated quickly on a new container) ---
if [[ ! -f "$WINEPREFIX/system.reg" ]]; then
  log "A inicializar o Wine prefix"
  wineboot --init > /dev/null 2>&1 || true
  wineserver -w
fi
ln -sfn "$DATA_DIR" "$WINEPREFIX/drive_c/icarus"

# --- Import prospects dropped in ./import (never overwrites unless asked) ---
mkdir -p "$PROSPECTS_DIR"
shopt -s nullglob
for src in "$IMPORT_DIR"/*.json; do
  dest="$PROSPECTS_DIR/$(basename "$src")"
  if [[ -f "$dest" && "${IMPORT_OVERWRITE:-false}" != "true" ]]; then
    log "Import: $(basename "$src") já existe no servidor, ignorado (IMPORT_OVERWRITE=false)"
    continue
  fi
  [[ -f "$dest" ]] && cp -p "$dest" "$dest.before-import.$(date +%Y%m%d-%H%M%S)"
  cp "$src" "$dest"
  log "Import: $(basename "$src") copiado para os prospects do servidor"
done
shopt -u nullglob

# --- ServerSettings.ini: .env is the source of truth for the keys we manage ---
set_ini() {
  local key="$1" value="$2"
  K="$key" V="$value" awk '
    BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"] }
    index($0, k "=") == 1 { print k "=" v; found = 1; next }
    { print }
    END { if (!found) print k "=" v }
  ' "$INI_FILE" > "$INI_FILE.tmp"
  mv "$INI_FILE.tmp" "$INI_FILE"
}

mkdir -p "$(dirname "$INI_FILE")"
[[ -f "$INI_FILE" ]] || echo "[/Script/Icarus.DedicatedServerSettings]" > "$INI_FILE"
set_ini SessionName                      "${SERVER_NAME:-Icarus Caseiro}"
set_ini JoinPassword                     "${JOIN_PASSWORD:-}"
set_ini AdminPassword                    "${ADMIN_PASSWORD:-}"
set_ini MaxPlayers                       "${MAX_PLAYERS:-4}"
set_ini ShutdownIfNotJoinedFor           "${SHUTDOWN_IF_NOT_JOINED_FOR:--1}"
set_ini ShutdownIfEmptyFor               "${SHUTDOWN_IF_EMPTY_FOR:--1}"
set_ini AllowNonAdminsToLaunchProspects  "${ALLOW_NON_ADMINS_LAUNCH:-True}"
set_ini AllowNonAdminsToDeleteProspects  "${ALLOW_NON_ADMINS_DELETE:-False}"
set_ini LoadProspect                     "${LOAD_PROSPECT:-}"
set_ini ResumeProspect                   "${RESUME_PROSPECT:-True}"

# --- Run the server ---
server_pid=""
shutdown() {
  log "Sinal de paragem recebido, a pedir ao servidor para fechar"
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -INT "$server_pid" 2>/dev/null || true
    for _ in $(seq 1 "${SHUTDOWN_TIMEOUT:-60}"); do
      kill -0 "$server_pid" 2>/dev/null || break
      sleep 1
    done
  fi
  wineserver -k 2>/dev/null || true
  log "Servidor parado"
  exit 0
}
trap shutdown TERM INT

if [[ "${LOG_TO_STDOUT:-true}" == "true" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  tail -n 0 -F "$LOG_FILE" 2>/dev/null &
fi

log "A arrancar '${SERVER_NAME:-Icarus Caseiro}' (jogo UDP ${GAME_PORT:-17777}, query UDP ${QUERY_PORT:-27015})"
# shellcheck disable=SC2086  # EXTRA_ARGS is intentionally word-split
wine "$SERVER_EXE" \
  -Log \
  -UserDir='C:\icarus' \
  -SteamServerName="${SERVER_NAME:-Icarus Caseiro}" \
  -PORT="${GAME_PORT:-17777}" \
  -QueryPort="${QUERY_PORT:-27015}" \
  ${EXTRA_ARGS:-} &
server_pid=$!

wait "$server_pid" || true
log "O processo do servidor terminou"
wineserver -k 2>/dev/null || true
exit 1
