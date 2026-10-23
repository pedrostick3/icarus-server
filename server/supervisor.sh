#!/usr/bin/env bash
# Keeps the Icarus server running and executes commands sent by the manager
# through files in $CONTROL_DIR (restart, update, stop, start, restore).
#
#   command.json  <- written by the manager  {"id","action","update","file"}
#   result.json   -> outcome of the last command
#   status.json   -> state, build and start time
#   heartbeat     -> epoch seconds, refreshed every 10s while the supervisor lives
set -uo pipefail

APP_ID=2089300
SERVER_EXE="$GAME_DIR/Icarus/Binaries/Win64/IcarusServer-Win64-Shipping.exe"
INI_FILE="$DATA_DIR/Saved/Config/WindowsServer/ServerSettings.ini"
SAVES_DIR="$DATA_DIR/Saved/PlayerData/DedicatedServer"
PROSPECTS_DIR="$SAVES_DIR/Prospects"
LOG_FILE="$DATA_DIR/Saved/Logs/Icarus.log"
BACKUPS_DIR="${BACKUPS_DIR:-/backups}"
STATUS_FILE="$CONTROL_DIR/status.json"
CMD_FILE="$CONTROL_DIR/command.json"
RESULT_FILE="$CONTROL_DIR/result.json"
CRASH_LIMIT="${CRASH_LIMIT:-5}"
CRASH_WINDOW_SECONDS=1800
# Accept "Mundo" or "Mundo.json"
LOAD_PROSPECT="${LOAD_PROSPECT:-}"
LOAD_PROSPECT="${LOAD_PROSPECT%.json}"

state="stopped"
server_pid=""
started_at=""
crash_times=()

log() { echo "[supervisor] $(date '+%F %T') $*"; }

installed_buildid() {
  sed -n 's/.*"buildid"[[:space:]]*"\([0-9]*\)".*/\1/p' \
    "$GAME_DIR/steamapps/appmanifest_$APP_ID.acf" 2>/dev/null | head -n 1
}

write_status() {
  jq -n \
    --arg state "$state" \
    --arg pid "$server_pid" \
    --arg started_at "$started_at" \
    --arg buildid "$(installed_buildid)" \
    --arg branch "${STEAM_BRANCH:-public}" \
    --argjson heartbeat "$(date +%s)" \
    '{state: $state, pid: $pid, started_at: $started_at, buildid: $buildid,
      branch: (if $branch == "" then "public" else $branch end), heartbeat: $heartbeat}' \
    > "$STATUS_FILE.tmp" && mv "$STATUS_FILE.tmp" "$STATUS_FILE"
}

set_state() {
  state="$1"
  log "Estado: $state"
  write_status
}

write_result() {
  local id="$1" ok="$2" message="$3"
  jq -n --arg id "$id" --argjson ok "$ok" --arg message "$message" \
    --argjson finished_at "$(date +%s)" \
    '{id: $id, ok: $ok, message: $message, finished_at: $finished_at}' \
    > "$RESULT_FILE.tmp" && mv "$RESULT_FILE.tmp" "$RESULT_FILE"
}

# --------------------------------------------------------------------------- #
# Install / update / setup
# --------------------------------------------------------------------------- #

steamcmd_update() {
  local branch_args=()
  [[ -n "${STEAM_BRANCH:-}" ]] && branch_args=(-beta "$STEAM_BRANCH")
  local before attempt
  before="$(installed_buildid)"
  set_state updating
  for attempt in 1 2 3; do
    log "SteamCMD: a instalar/atualizar Icarus (tentativa $attempt)"
    if "$STEAMCMD_DIR/steamcmd.sh" \
        +@sSteamCmdForcePlatformType windows \
        +force_install_dir "$GAME_DIR" \
        +login anonymous \
        +app_update "$APP_ID" "${branch_args[@]}" validate \
        +quit && [[ -f "$SERVER_EXE" ]]; then
      log "SteamCMD: build ${before:-nenhuma} -> $(installed_buildid)"
      return 0
    fi
    sleep 5
  done
  log "ERRO: SteamCMD falhou 3 vezes"
  return 1
}

ensure_wine_prefix() {
  if [[ ! -f "$WINEPREFIX/system.reg" ]]; then
    log "A inicializar o Wine prefix"
    wineboot --init > /dev/null 2>&1 || true
    wineserver -w
  fi
  ln -sfn "$DATA_DIR" "$WINEPREFIX/drive_c/icarus"
}

import_prospects() {
  mkdir -p "$PROSPECTS_DIR"
  local src dest
  for src in "$IMPORT_DIR"/*.json; do
    [[ -f "$src" ]] || continue
    dest="$PROSPECTS_DIR/$(basename "$src")"
    if [[ -f "$dest" && "${IMPORT_OVERWRITE:-false}" != "true" ]]; then
      log "Import: $(basename "$src") já existe no servidor, ignorado (IMPORT_OVERWRITE=false)"
      continue
    fi
    [[ -f "$dest" ]] && cp -p "$dest" "$dest.before-import.$(date +%Y%m%d-%H%M%S)"
    cp "$src" "$dest"
    log "Import: $(basename "$src") copiado para os prospects do servidor"
  done
}

# .env is the source of truth for the keys we manage; anything else is kept
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

# LOAD_PROSPECT is the file name without .json; warn early when it does not exist
check_load_prospect() {
  local name="$LOAD_PROSPECT" available
  [[ -z "$name" ]] && return 0
  [[ -f "$PROSPECTS_DIR/$name.json" ]] && return 0
  available="$(find "$PROSPECTS_DIR" -maxdepth 1 -name '*.json' -printf '%f\n' 2>/dev/null | sed 's/\.json$//' | paste -sd ',' -)"
  log "AVISO: LOAD_PROSPECT=$name mas não existe $name.json em Saved/PlayerData/DedicatedServer/Prospects."
  log "AVISO: prospects disponíveis: ${available:-nenhum (põe o .json em ./import e recria o contentor)}"
}

configure_ini() {
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
  set_ini LoadProspect                     "$LOAD_PROSPECT"
  set_ini ResumeProspect                   "${RESUME_PROSPECT:-True}"
}

# --------------------------------------------------------------------------- #
# Server process
# --------------------------------------------------------------------------- #

server_alive() { [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; }

start_server() {
  if server_alive; then
    return 0
  fi
  if [[ ! -f "$SERVER_EXE" ]]; then
    log "ERRO: o jogo não está instalado"
    set_state stopped
    return 1
  fi
  set_state starting
  configure_ini
  check_load_prospect
  log "A arrancar '${SERVER_NAME:-Icarus Caseiro}' (jogo UDP ${GAME_PORT:-17777}, query UDP ${QUERY_PORT:-27015}, build $(installed_buildid))"
  # shellcheck disable=SC2086  # EXTRA_ARGS is intentionally word-split
  wine "$SERVER_EXE" \
    -Log \
    -UserDir='C:\icarus' \
    -SteamServerName="${SERVER_NAME:-Icarus Caseiro}" \
    -PORT="${GAME_PORT:-17777}" \
    -QueryPort="${QUERY_PORT:-27015}" \
    ${EXTRA_ARGS:-} &
  server_pid=$!
  started_at="$(date -Iseconds)"
  set_state running
}

stop_server() {
  if server_alive; then
    set_state stopping
    log "A pedir ao servidor para fechar (Ctrl+C, até ${SHUTDOWN_TIMEOUT:-60}s)"
    kill -INT "$server_pid" 2>/dev/null || true
    local _
    for _ in $(seq 1 "${SHUTDOWN_TIMEOUT:-60}"); do
      server_alive || break
      sleep 1
    done
    if server_alive; then
      log "O servidor não fechou a tempo, a forçar"
      kill -KILL "$server_pid" 2>/dev/null || true
    fi
    wait "$server_pid" 2>/dev/null || true
  fi
  wineserver -k 2>/dev/null || true
  server_pid=""
  started_at=""
  set_state stopped
}

# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #

# Sets restore_message; returns non-zero on failure
restore_message=""
restore_backup() {
  local file="$1" zip snapshot
  zip="$BACKUPS_DIR/$(basename "$file")"
  if [[ ! -f "$zip" ]]; then
    restore_message="backup não encontrado: $(basename "$file")"
    return 1
  fi
  if ! unzip -tq "$zip" > /dev/null; then
    restore_message="o zip está corrompido: $(basename "$zip")"
    return 1
  fi
  stop_server
  set_state restoring
  snapshot="$DATA_DIR/restore-snapshots/before-restore-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$snapshot"
  if [[ -d "$SAVES_DIR" ]]; then
    cp -a "$SAVES_DIR" "$snapshot/"
    # Replace the saves dir only if the backup contains it (otherwise just overlay)
    if unzip -Z1 "$zip" | grep -q '^Saved/PlayerData/DedicatedServer/'; then
      rm -rf "$SAVES_DIR"
    fi
  fi
  if ! unzip -oq "$zip" -d "$DATA_DIR"; then
    restore_message="falhou a extração; o estado anterior está em $snapshot"
    return 1
  fi
  log "Restaurado $(basename "$zip"); estado anterior guardado em $snapshot"
  restore_message="restaurado $(basename "$zip") (estado anterior em data/restore-snapshots/$(basename "$snapshot"))"
}

# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

handle_command() {
  [[ -f "$CMD_FILE" ]] || return 0
  local processing="$CONTROL_DIR/command.processing.json"
  mv "$CMD_FILE" "$processing" || return 0

  local id action update file message ok=true
  id="$(jq -r '.id // ""' "$processing" 2>/dev/null)"
  action="$(jq -r '.action // ""' "$processing" 2>/dev/null)"
  update="$(jq -r '.update // false' "$processing" 2>/dev/null)"
  file="$(jq -r '.file // ""' "$processing" 2>/dev/null)"
  log "Comando recebido: $action (update=$update) id=$id"

  case "$action" in
    restart|update)
      [[ "$action" == "update" ]] && update=true
      stop_server
      if [[ "$update" == "true" ]]; then
        if ! steamcmd_update; then
          ok=false
          message="a atualização falhou; a arrancar com a build $(installed_buildid)"
        fi
      fi
      if start_server; then
        message="${message:-reiniciado com a build $(installed_buildid)}"
      else
        ok=false
        message="${message:-} não foi possível arrancar o servidor"
      fi
      ;;
    stop)
      stop_server
      message="servidor parado (usa start/restart para voltar a ligar)"
      ;;
    start)
      crash_times=()
      if start_server; then message="servidor a arrancar"; else ok=false; message="não foi possível arrancar"; fi
      ;;
    restore)
      if restore_backup "$file"; then
        message="$restore_message"
        start_server || { ok=false; message="$message; mas o servidor não arrancou"; }
      else
        ok=false
        message="$restore_message"
        start_server || true
      fi
      ;;
    *)
      ok=false
      message="comando desconhecido: $action"
      ;;
  esac

  rm -f "$processing"
  write_result "$id" "$ok" "$message"
  log "Comando $action terminado: ok=$ok — $message"
}

handle_crash() {
  local now kept=() t
  now="$(date +%s)"
  for t in "${crash_times[@]}"; do
    (( now - t < CRASH_WINDOW_SECONDS )) && kept+=("$t")
  done
  crash_times=("${kept[@]}" "$now")
  server_pid=""
  started_at=""
  wineserver -k 2>/dev/null || true
  if (( ${#crash_times[@]} >= CRASH_LIMIT )); then
    set_state crash_loop
    log "O servidor foi abaixo ${#crash_times[@]} vezes em 30 min; à espera de um comando (restart/start)"
    return
  fi
  set_state crashed
  log "O servidor foi abaixo inesperadamente; a reiniciar em 10s"
  sleep 10
  start_server
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

on_term() {
  log "Sinal de paragem recebido"
  stop_server
  exit 0
}
trap on_term TERM INT

mkdir -p "$CONTROL_DIR"
rm -f "$CMD_FILE" "$CONTROL_DIR/command.processing.json"
write_status

# Heartbeat from a separate process: the main loop blocks during SteamCMD updates
(
  while true; do
    date +%s > "$CONTROL_DIR/heartbeat.tmp" && mv "$CONTROL_DIR/heartbeat.tmp" "$CONTROL_DIR/heartbeat"
    sleep 10
  done
) &

if [[ ! -f "$SERVER_EXE" || "${UPDATE_ON_START:-true}" == "true" ]]; then
  if ! steamcmd_update && [[ ! -f "$SERVER_EXE" ]]; then
    exit 1
  fi
fi
ensure_wine_prefix
import_prospects

if [[ "${LOG_TO_STDOUT:-true}" == "true" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  tail -n 0 -F "$LOG_FILE" 2>/dev/null &
fi

start_server
last_status=0
while true; do
  sleep 2 &
  wait $! 2>/dev/null
  handle_command
  if [[ "$state" == "running" ]] && ! server_alive; then
    handle_crash
  fi
  if (( $(date +%s) - last_status >= 10 )); then
    write_status
    last_status="$(date +%s)"
  fi
done
