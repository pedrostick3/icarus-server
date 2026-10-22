# Icarus dedicated server (Windows binary) running on Linux through Wine.
# The image only changes when you rebuild it: a game update never changes the
# Wine version, it only downloads new game files into the icarus-game volume.
FROM debian:bookworm-slim

ARG WINE_BRANCH=stable

ENV DEBIAN_FRONTEND=noninteractive \
    STEAMCMD_DIR=/opt/steamcmd \
    GAME_DIR=/opt/icarus \
    DATA_DIR=/data \
    IMPORT_DIR=/import \
    CONTROL_DIR=/control \
    BACKUPS_DIR=/backups \
    WINEPREFIX=/home/steam/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all

# Wine from the official WineHQ repo (Debian's wine is too old for recent UE builds)
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg gosu jq procps tini tzdata unzip \
      lib32gcc-s1 lib32stdc++6 \
 && mkdir -pm755 /etc/apt/keyrings \
 && curl -fsSL https://dl.winehq.org/wine-builds/winehq.key \
      | gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key \
 && curl -fsSL -o /etc/apt/sources.list.d/winehq-bookworm.sources \
      https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources \
 && apt-get update \
 && apt-get install -y --install-recommends "winehq-${WINE_BRANCH}" \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash steam \
 && mkdir -p "$STEAMCMD_DIR" "$GAME_DIR" "$DATA_DIR" "$IMPORT_DIR" "$CONTROL_DIR" \
 && curl -fsSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
      | tar -xz -C "$STEAMCMD_DIR" \
 && chown -R steam:steam "$STEAMCMD_DIR" "$GAME_DIR" "$DATA_DIR" "$CONTROL_DIR"

COPY --chmod=755 entrypoint.sh supervisor.sh /usr/local/bin/
# Safety net for clones made on Windows with CRLF line endings
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh /usr/local/bin/supervisor.sh

# Healthy while the supervisor is alive and the server is running (or being
# restarted/updated on purpose)
HEALTHCHECK --interval=60s --timeout=10s --start-period=20m --retries=3 \
  CMD test $(( $(date +%s) - $(cat "$CONTROL_DIR/heartbeat") )) -lt 60 \
   && jq -e '.state | IN("running","starting","updating","stopping","restoring")' "$CONTROL_DIR/status.json" > /dev/null \
   || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
