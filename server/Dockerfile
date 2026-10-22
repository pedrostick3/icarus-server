# Icarus dedicated server (Windows binary) running on Linux through Wine.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    STEAMCMD_DIR=/opt/steamcmd \
    GAME_DIR=/opt/icarus \
    DATA_DIR=/data \
    IMPORT_DIR=/import \
    WINEPREFIX=/home/steam/.wine \
    WINEARCH=win64 \
    WINEDEBUG=-all

# Wine from the official WineHQ repo (Debian's wine is too old for recent UE builds)
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg gosu procps tini tzdata \
      lib32gcc-s1 lib32stdc++6 \
 && mkdir -pm755 /etc/apt/keyrings \
 && curl -fsSL https://dl.winehq.org/wine-builds/winehq.key \
      | gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key \
 && curl -fsSL -o /etc/apt/sources.list.d/winehq-bookworm.sources \
      https://dl.winehq.org/wine-builds/debian/dists/bookworm/winehq-bookworm.sources \
 && apt-get update \
 && apt-get install -y --install-recommends winehq-stable \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash steam \
 && mkdir -p "$STEAMCMD_DIR" "$GAME_DIR" "$DATA_DIR" "$IMPORT_DIR" \
 && curl -fsSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
      | tar -xz -C "$STEAMCMD_DIR" \
 && chown -R steam:steam "$STEAMCMD_DIR" "$GAME_DIR" "$DATA_DIR"

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=15m --retries=3 \
  CMD pgrep -f IcarusServer-Win64-Shipping.exe > /dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
