# icarus-server

Servidor dedicado de **Icarus** caseiro em Docker + backups automáticos dos saves para um canal do **Discord**.

O mundo passa a viver num PC sempre ligado, e qualquer um de nós pode entrar quando quiser — sem depender de quem criou o mundo estar online.

```
┌──────────────────── docker compose ────────────────────┐
│  icarus  (Debian + Wine + SteamCMD)                    │
│    IcarusServer.exe  ──escreve──►  ./data  (saves/log) │
│                                       │ (só leitura)    │
│  backup  (Python, stdlib)  ◄──────────┘                 │
│    zip ► ./backups (rotação)  ► webhook Discord         │
└────────────────────────────────────────────────────────┘
```

- `./data` — saves, configuração e logs. **É isto que importa.** Copia esta pasta e tens o mundo.
- `./backups` — últimos N backups `.zip` locais.
- `./import` — larga aqui um prospect `.json` para o importar no arranque.
- Volume `icarus-game` — ficheiros do jogo (vários GB, descarregados de novo se perderes o volume).

---

## 1. Preparar o PC servidor

### Windows (Docker Desktop)

1. Instalar o [Docker Desktop](https://www.docker.com/products/docker-desktop/) com backend WSL2.
2. **Dar RAM suficiente ao WSL.** Por omissão o WSL só usa metade da RAM (8 GB num PC de 16 GB) e o servidor de Icarus pode pedir 6–10 GB. Cria `%UserProfile%\.wslconfig`:
   ```ini
   [wsl2]
   memory=12GB
   swap=8GB
   ```
   e corre `wsl --shutdown`.
3. Docker Desktop → Settings → General → *Start Docker Desktop when you sign in*.
4. O portátil não pode adormecer: Definições de energia → *Nunca* suspender (ligado à corrente) e *Fechar a tampa → Não fazer nada*.
5. Clona o repositório **dentro do WSL** (ex.: `\\wsl$\Ubuntu\home\<user>\icarus-server`) em vez de `C:\...` — os bind mounts ficam muito mais rápidos.

### Linux (recomendado se o PC for só para isto)

Ubuntu Server/Debian + Docker Engine. Gasta menos RAM que Windows + WSL e arranca sozinho sem login. Define `PUID`/`PGID` no `.env` com o resultado de `id`.

---

## 2. Arrancar

```bash
cp .env.example .env      # e edita: nome, passwords, webhook...
docker compose up -d --build
docker compose logs -f icarus
```

O primeiro arranque demora (descarrega o jogo via SteamCMD e inicializa o Wine). Quando aparecer atividade do `Icarus.log`, o servidor está pronto.

---

## 3. Trazer o mundo do teu amigo (P2P → dedicado)

1. No PC dele, com o jogo fechado: `%LocalAppData%\Icarus\Saved\PlayerData\<SteamID64>\Prospects\`
   (há uma pasta com um número comprido — é o SteamID dele).
2. Ele envia-te o `.json` do prospect (ex.: `MundoDoPedro.json`).
3. Coloca-o em `./import/` e no `.env` mete `LOAD_PROSPECT=MundoDoPedro` (nome sem `.json`).
4. `docker compose up -d --force-recreate icarus`

O ficheiro é copiado para `data/Saved/PlayerData/DedicatedServer/Prospects/`. Se já existir lá um com o mesmo nome **não é substituído**, a não ser com `IMPORT_OVERWRITE=true` (nesse caso fica uma cópia `.before-import.<data>`).

> As personagens (nível, talentos, inventário de órbita) ficam guardadas do lado de cada jogador, por isso cada um entra com a sua.

---

## 4. Rede: deixar o amigo entrar

Reencaminha no router, para o IP local do PC servidor (fixa-o por DHCP):

| Porta | Protocolo | Para quê |
|------:|:---------:|----------|
| 17777 | UDP | jogo (`GAME_PORT`) |
| 27015 | UDP | query / browser da Steam (`QUERY_PORT`) |

Não abras mais nada. No jogo: *Dedicated Servers* → procura pelo `SERVER_NAME` ou liga diretamente por `IP:17777`.

**CGNAT:** se o IP WAN que aparece no router for diferente do que mostra [ifconfig.me](https://ifconfig.me) (ou começar por `100.64–100.127`), o operador está a usar CGNAT e o port forwarding não funciona. Pede IP público ao operador, ou usa uma VPN mesh como [Tailscale](https://tailscale.com) (grátis; o amigo liga-se pelo IP Tailscale do servidor).

---

## 5. Backups para o Discord

1. No Discord: canal → ⚙️ *Editar canal* → *Integrações* → *Webhooks* → *Novo webhook* → *Copiar URL*.
2. Cola em `DISCORD_WEBHOOK_URL` no `.env` e `docker compose up -d backup`.
3. Testa: `docker compose run --rm backup --test-discord`

### Quando é feito um backup

| Gatilho | Chave | Por omissão |
|---------|-------|-------------|
| Periódico | `BACKUP_INTERVAL_MINUTES` (0 = desliga) | 60 min |
| Um jogador sai | `BACKUP_ON_PLAYER_LEAVE` + `BACKUP_LEAVE_DELAY_SECONDS` | sim, 120 s depois |
| Serviço arranca | `BACKUP_ON_START` | sim |
| Manual | `docker compose exec backup python /app/backup.py --now` | — |

Regras de proteção:

- `BACKUP_ONLY_IF_CHANGED=true` — só envia se o conteúdo mudou (hash SHA-256), por isso o canal não enche quando ninguém joga.
- `BACKUP_MIN_GAP_MINUTES` — mínimo entre backups disparados por eventos (vários jogadores a sair seguidos = 1 backup).
- `BACKUP_FILE_STABLE_SECONDS` — só copia quando os ficheiros não mudam há X segundos; os `.json` são validados antes, e se mudarem durante a compressão o backup é repetido. Nunca se envia um save cortado a meio.
- `LOCAL_BACKUP_KEEP` — quantos `.zip` ficam em `./backups`.
- `DISCORD_MAX_FILE_MB` — o limite de upload do Discord sem Nitro/boost é **10 MB**. Backups maiores são enviados em partes `.zip.001`, `.zip.002`… (abre o `.001` com 7-Zip, ou `copy /b nome.zip.0* nome.zip`).
- `DISCORD_NOTIFY_PLAYERS` / `DISCORD_NOTIFY_ERRORS` — mensagens "🟢 X entrou / 🔴 X saiu" e avisos de falha.

Todas as chaves estão documentadas em [`.env.example`](.env.example).

### Deteção de entradas/saídas

É feita a ler o `Icarus.log`. Os padrões por omissão são:

- entrada: `Join succeeded: (?P<name>.+?)\s*$`
- saída: `UNetConnection::Close.*GameNetDriver|Player disconnected`

O formato do log pode mudar entre versões do jogo. Se não detetar, procura as linhas certas com
`grep -iE "join|close|disconnect" data/Saved/Logs/Icarus.log` e ajusta `PLAYER_JOIN_REGEX` / `PLAYER_LEAVE_REGEX` (o grupo `(?P<name>...)` dá o nome na mensagem). Mesmo sem deteção, o backup periódico continua a funcionar.

### Restaurar

```bash
docker compose stop icarus
# descarrega o .zip do Discord (ou usa um de ./backups) e:
unzip -o icarus-AAAAMMDD-HHMMSS-*.zip -d data/
docker compose start icarus
```

---

## Comandos úteis

```bash
docker compose ps                      # estado
docker compose logs -f icarus          # log do servidor
docker compose logs -f backup          # log dos backups
docker compose restart icarus          # reinicia (e atualiza o jogo, se UPDATE_ON_START=true)
docker compose down                    # pára tudo (os dados em ./data ficam)
```

### Mudar de PC

Copia a pasta do projeto (com `data/`, `.env` e, se quiseres, `backups/`) para o novo PC e `docker compose up -d --build`. O jogo volta a ser descarregado; o mundo é o mesmo.

---

## Notas e limitações

- O servidor de Icarus só existe para Windows; aqui corre com **Wine**. É o mesmo método das imagens comunitárias conhecidas, mas uma atualização do jogo pode ocasionalmente partir algo até o Wine acompanhar.
- Ao parar o contentor é enviado Ctrl+C ao servidor e há `SHUTDOWN_TIMEOUT` segundos para fechar. O Icarus grava periodicamente e quando os jogadores saem, por isso o ideal é parar o servidor com toda a gente fora.
- O `ServerSettings.ini` é reescrito a partir do `.env` em cada arranque para as chaves geridas (nome, passwords, max players, shutdown, load/resume prospect). Outras chaves que lá metas à mão são mantidas.
- Não metas o `ServerSettings.ini` em `BACKUP_PATHS` se o canal do Discord for partilhado: tem as passwords.
