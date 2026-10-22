#!/usr/bin/env bash
# Fake SteamCMD: "installs" build $(cat $FAKE_BUILD_FILE)
[[ -f "${FAKE_STEAM_FAIL_FILE:-/nonexistent}" ]] && exit 1
mkdir -p "$GAME_DIR/Icarus/Binaries/Win64" "$GAME_DIR/steamapps"
touch "$GAME_DIR/Icarus/Binaries/Win64/IcarusServer-Win64-Shipping.exe"
printf '"AppState"\n{\n\t"appid"\t\t"2089300"\n\t"buildid"\t\t"%s"\n}\n' "$(cat "$FAKE_BUILD_FILE")" \
  > "$GAME_DIR/steamapps/appmanifest_2089300.acf"
