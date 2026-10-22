# icarus-server

Servidor dedicado de **Icarus** caseiro em Docker, com backups automáticos para o **Discord** (e, opcionalmente, Google Drive), reinício diário, atualizações automáticas quando ninguém está a jogar, avisos de online/offline e um bot com comandos.

O mundo vive num PC sempre ligado e qualquer um de nós pode entrar quando quiser, sem depender de quem criou o mundo.

```
┌─────────────────────────────── docker compose ───────────────────────────────┐
│ icarus  (Debian + Wine + SteamCMD)                                           │
│   supervisor.sh ── arranca/vigia ──► IcarusServer.exe ──escreve──► ./data     │
│        ▲  executa comandos (restart, update, restore)                │       │
│        │  volume "control"                                           │ (ro)  │
│ manager (Python)                                                     ▼       │
│   • backups ► ./backups (rotação) ► Discord ► rclone/Google Drive (opcional) │
│   • jogadores online (query Steam A2S, ou log)                               │
│   • reinício diário 07:00 só se vazio • atualiza quando sai versão nova      │
│   • avisos online/offline • healthchecks.io • DuckDNS • bot com password     │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Pasta | O quê |
|---|---|
| `./data` | saves, configuração e logs do servidor. **É isto que importa.** |
| `./backups` | backups `.zip` locais (com rotação) |
| `./import` | larga aqui um prospect `.json` para o importar no arranque |
| `./config/rclone` | `rclone.conf` (só se usares Google Drive) |
| `windows/` | plano B: correr o servidor nativo no Windows com a mesma `./data` |
| volume `icarus-game` | ficheiros do jogo (vários GB, re-descarregáveis) |

---

## ✅ Checklist: o que tens de fazer tu

O código trata do resto, mas estes passos são no teu hardware ou nas tuas contas. Cada um está explicado na secção indicada.

**No PC servidor**
- [ ] Escolher Linux ou Windows e instalar o Docker ([§1](#1-preparar-o-pc-servidor))
- [ ] Windows: `.wslconfig` com 12 GB, Docker a arrancar no login, login automático (Autologon), horas de atividade do Windows Update ([§1](#opção-b-windows-docker-desktop))
- [ ] Energia: nunca suspender; fechar a tampa → não fazer nada ([§1](#em-ambos-é-um-portátil))
- [ ] BIOS: *Power On with AC Attach* ativado ([§1](#em-ambos-é-um-portátil))
- [ ] Lenovo Vantage: *Modo de conservação* da bateria ativado ([§1](#em-ambos-é-um-portátil))
- [ ] IP local fixo para o PC servidor (reserva DHCP no router) ([§4](#4-rede-deixar-o-amigo-entrar))

**No router / rede**
- [x] Confirmar que não há CGNAT comparando os IPv4 ([§4](#como-ver-se-tens-cgnat-vodafone)): feito, há IP público ✅
- [ ] Reencaminhar as portas UDP 17777 e 27015 ([§4](#4-rede-deixar-o-amigo-entrar))
- [ ] (opcional) Conta DuckDNS para um nome fixo ([§4](#nome-fixo-com-duckdns-opcional))

**Contas e `.env`**
- [ ] `JOIN_PASSWORD` e `ADMIN_PASSWORD` do jogo
- [ ] Webhook do Discord ([§5](#5-discord))
- [ ] (opcional) Bot do Discord + `DISCORD_BOT_COMMAND_PASSWORD` ([§5](#bot-com-comandos-opcional))
- [ ] (opcional) healthchecks.io para saber se o PC morre ([§8](#8-monitorização-saber-quando-o-pc-morre))
- [ ] (mais tarde) Google Drive via rclone ([§6](#google-drive-pronto-mas-desligado))

**Depois de arrancar**
- [ ] Importar o mundo do teu amigo ([§3](#3-trazer-o-mundo-do-teu-amigo-p2p--dedicado))
- [ ] Ver se `/status` (ou `$M status`) mostra os jogadores; se mostrar, a query Steam funciona
- [ ] Desligar o PC da corrente e voltar a ligar (ou reiniciá-lo) e confirmar que o servidor volta sozinho
- [ ] Uma vez por mês, um restauro a sério ([§6](#ensaio-de-restauro-a-sério-manual))

---

## Docker + Wine: é seguro com as atualizações?

O servidor de Icarus só existe para Windows; no Docker corre com Wine. É assim que funcionam as imagens da comunidade há anos, e o Icarus recebe atualizações quase todas as semanas sem as partir, mas **ninguém garante que uma atualização futura não traga problemas ao Wine**. Atenção a um ponto: **não atualizar não é opção**. A Steam atualiza o jogo de cada um, e um servidor numa versão antiga deixa de aceitar os clientes.

Por isso este projeto assenta em redes de segurança:

1. **A imagem só muda quando a reconstróis.** Uma atualização do jogo só traz ficheiros novos do jogo; a versão do Wine fica a mesma. Só há uma variável de cada vez.
2. **Atualizações controladas.** Só quando ninguém está a jogar, sempre com backup antes. Se a atualização falhar, o servidor volta a arrancar com a versão anterior e recebes um aviso no Discord.
3. **Deteção de problemas.** Se o servidor for abaixo é reiniciado. Se cair várias vezes seguidas, fica parado e recebes um aviso, em vez de ficar em ciclo.
4. **Plano B em 5 minutos** ([abaixo](#plano-b-servidor-nativo-no-windows)). Os saves são exatamente os mesmos no Docker e no Windows nativo. Se um dia o Wine falhar, corres `windows/run-server.ps1` com a mesma pasta `data` até haver solução.

---

## 1. Preparar o PC servidor

### Opção A: Linux (recomendado se o PC for só para isto)

Ubuntu Server 24.04 + [Docker Engine](https://docs.docker.com/engine/install/ubuntu/). Gasta menos RAM que Windows + WSL, não tem reinícios do Windows Update e **arranca sozinho sem ninguém fazer login**: o serviço do Docker liga no boot e os contentores (`restart: unless-stopped`) voltam a arrancar.

```bash
sudo systemctl enable docker
id   # mete o uid/gid em PUID/PGID no .env
```

### Opção B: Windows (Docker Desktop)

1. Instala o [Docker Desktop](https://www.docker.com/products/docker-desktop/) (backend WSL2).
2. **RAM para o WSL**: por omissão só usa metade (8 GB de 16 GB). Cria `%UserProfile%\.wslconfig` e corre `wsl --shutdown`:
   ```ini
   [wsl2]
   memory=12GB
   swap=8GB
   ```
3. Docker Desktop → Settings → General → ✅ *Start Docker Desktop when you sign in*.
4. **Arranque automático após reinício**: o Docker Desktop só arranca depois de alguém fazer login. Ativa o login automático com o [Autologon da Sysinternals](https://learn.microsoft.com/sysinternals/downloads/autologon) (guarda a password encriptada) ou `netplwiz`.
5. Clona o projeto **dentro do WSL** (`\\wsl$\Ubuntu\home\<user>\icarus-server`) em vez de `C:\…`: os volumes ficam muito mais rápidos.
6. Windows Update → *Horas de atividade*: põe um intervalo largo (ex. 08:00–02:00) para os reinícios forçados caírem de madrugada.

### Em ambos (é um portátil)

- **Nunca suspender** quando ligado à corrente e *Fechar a tampa → Não fazer nada*.
- **Ligar sozinho após falha de luz**: na BIOS da Lenovo procura *Power On with AC Attach* (ou semelhante) e ativa-o. A bateria já funciona como UPS para falhas curtas.
- **Proteger a bateria**: ligado à corrente 24/7, ativa o *Modo de conservação* no Lenovo Vantage (carga limitada a ~60–80%) para a bateria não inchar.

---

## 2. Arrancar

```bash
cp .env.example .env      # edita: nome, passwords, webhook...
docker compose up -d --build
docker compose logs -f icarus
```

O primeiro arranque demora: descarrega o jogo via SteamCMD e inicializa o Wine.

---

## 3. Trazer o mundo do teu amigo (P2P → dedicado)

1. No PC dele, com o jogo fechado: `%LocalAppData%\Icarus\Saved\PlayerData\<SteamID64>\Prospects\`
   (a pasta com um número comprido é o SteamID dele).
2. Ele envia-te o `.json` do prospect (ex.: `MundoDoPedro.json`).
3. Põe-no em `./import/` e no `.env` mete `LOAD_PROSPECT=MundoDoPedro`.
4. `docker compose up -d --force-recreate icarus`

O ficheiro vai para `data/Saved/PlayerData/DedicatedServer/Prospects/`. Se já existir um com o mesmo nome, só é substituído com `IMPORT_OVERWRITE=true`; nesse caso fica antes uma cópia `.before-import.<data>`.

> As personagens (nível, talentos, inventário de órbita) ficam do lado de cada jogador, por isso cada um entra com a sua.

---

## 4. Rede: deixar o amigo entrar

No router, reencaminha para o IP local do PC servidor (e fixa esse IP por DHCP):

| Porta | Protocolo | Para quê |
|------:|:---------:|----------|
| 17777 | UDP | jogo (`GAME_PORT`) |
| 27015 | UDP | query / lista de servidores da Steam (`QUERY_PORT`) |

Não abras mais nada. No jogo: *Dedicated Servers* → procura pelo `SERVER_NAME`.

Na Vodafone o router está em `http://vodafonegw` (ou `http://192.168.1.1`). O utilizador e a password estão no autocolante do router. O reencaminhamento de portas costuma estar em *Avançado* → *NAT* → *Mapeamento de portas* (o nome exato varia com o firmware). Cria duas regras, UDP 17777 e UDP 27015, para o IP local do PC servidor.

### Como ver se tens CGNAT (Vodafone)

Com CGNAT o operador partilha um IP público por vários clientes, e o port forwarding **não funciona**.

1. No router, vê o **endereço IPv4 da WAN**.
2. Vê o teu IPv4 público visto de fora. **Cuidado:** a Vodafone dá IPv6, e o ifconfig.me mostra o IPv6 se o houver. Usa um site só de IPv4: [api.ipify.org](https://api.ipify.org) ou [ipv4.icanhazip.com](https://ipv4.icanhazip.com).
3. Compara:
   - **Iguais** → tens IP público, o port forwarding funciona. ✅
   - **Diferentes**, ou o da WAN começa por `100.64.` a `100.127.` (ou `10.`) → **CGNAT**. ❌

> ✅ Verificado: o router e o api.ipify.org mostram o mesmo IPv4 público, por isso **não há CGNAT** e o port forwarding funciona.

Se tiveres CGNAT: liga para o apoio da Vodafone e pede **IP público** / desativação do CGNAT (normalmente fazem-no sem custo nas fibras, mas confirma). A alternativa sem depender da Vodafone é o [Tailscale](https://tailscale.com) (grátis): instalas no servidor e no PC do teu amigo, e ele liga-se ao IP Tailscale do servidor (`100.x.y.z:17777`).

> O IP público residencial costuma mudar de vez em quando (o router mostra a "duração da ligação"). Pela lista da Steam encontram o servidor pelo nome na mesma; para um endereço fixo usa o DuckDNS.

### Nome fixo com DuckDNS (opcional)

Para terem sempre o mesmo endereço (ex.: `icarus-casa.duckdns.org:17777`), mesmo quando o IP de casa muda:

1. Entra em [duckdns.org](https://www.duckdns.org) (login com Google/GitHub) e cria um subdomínio, ex.: `icarus-casa`.
2. No `.env`: `DUCKDNS_DOMAIN=icarus-casa` e `DUCKDNS_TOKEN=<token da página>`.
3. `docker compose up -d manager`

O manager verifica o IPv4 público a cada 5 min e atualiza o DuckDNS quando muda. O `/status` mostra o endereço. Quando o IP muda, o canal recebe um aviso sem o IP (o canal é partilhado).

---

## 5. Discord

### Webhook (backups e avisos)

Canal → ⚙️ *Editar canal* → *Integrações* → *Webhooks* → *Novo webhook* → *Copiar URL* → `DISCORD_WEBHOOK_URL`.
Testa com `docker compose run --rm --no-deps manager test-discord`.

O que aparece no canal:
- 📦 backups (`.zip`, partidos em `.001/.002` acima de 10 MB);
- 🟢/🔴 quem entra e sai;
- 🟢 online / 💥 foi abaixo / 🛑 parado após várias falhas / 🔴 offline;
- ♻️ reinícios, 🆕 versões novas, ⚠️ falhas, 💽 pouco disco.

Cada tipo de aviso liga/desliga no `.env`.

### Bot com comandos (opcional)

| Comando | Quem | O quê |
|---|---|---|
| `/ligar` | todos | passo-a-passo para entrar no servidor (só quem pede vê a resposta) |
| `/status` | todos | estado, jogadores, build, último backup, disco |
| `/backups` | todos | últimos 10 backups locais |
| `/backup` | admins + password | backup imediato |
| `/restart [atualizar] [forcar]` | admins + password | reinício gracioso com backup antes (recusa com jogadores, exceto se `forcar`) |
| `/update [forcar]` | admins + password | atualiza se houver versão nova |

**Password dos comandos:** quando um admin usa `/backup`, `/restart` ou `/update`, o bot abre um **popup privado** a pedir a `DISCORD_BOT_COMMAND_PASSWORD`. A password nunca aparece no canal e só quem a escreveu vê a resposta "password errada".
- Ao fim de `DISCORD_BOT_MAX_FAILED_ATTEMPTS` (5) tentativas erradas, o utilizador fica bloqueado 15 min e o canal recebe um aviso.
- Sem password definida, estes três comandos ficam **desativados**.
- Usa uma password própria, diferente das do jogo.

Para o criar:

1. [discord.com/developers/applications](https://discord.com/developers/applications) → *New Application* → *Bot* → *Reset Token* → copia para `DISCORD_BOT_TOKEN`.
2. *OAuth2* → *URL Generator* → scopes `bot` + `applications.commands`, permissões *View Channels* + *Send Messages* → abre o link e adiciona ao teu servidor.
3. No Discord: *Definições* → *Avançado* → ✅ *Modo de programador*. Botão direito no servidor → *Copiar ID* → `DISCORD_GUILD_ID`.
4. Quem pode usar os comandos de admin: `DISCORD_ADMIN_USER_IDS` e/ou `DISCORD_ADMIN_ROLE_IDS` (botão direito → *Copiar ID*). Se ambos estiverem vazios, só os administradores do servidor Discord.
5. Define `DISCORD_BOT_COMMAND_PASSWORD` no `.env`.
6. `docker compose up -d manager`

### Termos de Serviço e Política de Privacidade

O portal do Discord pede links para os termos e a privacidade do bot. Estão em `docs/` (PT + EN), prontos para o GitHub Pages:

- `https://pedrostick3.github.io/icarus-server/terms.html`
- `https://pedrostick3.github.io/icarus-server/privacy.html`

Para os publicar: GitHub → *Settings* → *Pages* → *Source: Deploy from a branch* → `main` / `/docs` → *Save*. Ao fim de 1–2 min os links acima funcionam; cola-os no portal do Discord (*General Information*).

> O GitHub Pages só funciona em repositórios **públicos**, a não ser que tenhas GitHub Pro. O repositório não tem segredos (o `.env` está no `.gitignore`), por isso pode ficar público. Se preferires mantê-lo privado, cria um repositório público só com a pasta `docs/`.

### Instruções para os jogadores (`/ligar`)

Qualquer pessoa no canal escreve `/ligar` e recebe, só para si, o passo-a-passo para entrar:
- o estado atual do servidor (online, a atualizar ou em baixo);
- atualizar o jogo e encontrar o servidor pelo nome;
- a ligação direta pelo DuckDNS, se estiver configurado;
- a password (só a dica, nunca a password);
- o mundo a escolher (`LOAD_PROSPECT`);
- o que fazer se der "versão diferente" ou o servidor não aparecer.

Personaliza com `CONNECT_PASSWORD_HINT` (ex.: "Está na mensagem afixada do canal privado") e `CONNECT_EXTRA_INFO`.

Sem bot, ou para deixar as instruções **afixadas** no canal:

```bash
docker compose exec manager python -m icarus_manager connect --post   # publica pelo webhook
```

Depois, no Discord: ⋯ na mensagem → *Afixar mensagem*. Atenção que o estado ("online agora") fica congelado nessa mensagem; o `/ligar` está sempre atualizado.

### Canal partilhado: o que fica protegido

- O `ServerSettings.ini` (que tem a `JoinPassword` e a `AdminPassword`) **nunca entra nos backups**, mesmo que alguém o meta em `BACKUP_PATHS`. É regenerado a partir do `.env` em cada arranque.
- O IP de casa não é publicado; os avisos do DuckDNS só dizem que mudou.
- Quem estiver no canal pode descarregar os backups do mundo. Se isso não for desejável, usa um canal só para vocês os dois ou `DISCORD_UPLOAD_BACKUPS=false`.
- O URL do webhook, o token do bot e a password dos comandos só existem no `.env`, que não vai para o git. Se algum deles for parar ao chat, apaga-o e gera outro.


---

## 6. Backups

| Gatilho | Chave | Por omissão |
|---|---|---|
| Periódico | `BACKUP_INTERVAL_MINUTES` (0 = desliga) | 60 min |
| Jogador sai | `BACKUP_ON_PLAYER_LEAVE` + `BACKUP_LEAVE_DELAY_SECONDS` | sim, 120 s depois |
| Antes de reiniciar/atualizar/restaurar | sempre | — |
| Manager arranca | `BACKUP_ON_START` | sim |
| Manual | `/backup` ou `docker compose exec manager python -m icarus_manager backup` | — |

Proteções:
- **`BACKUP_ONLY_IF_CHANGED`**: só faz backup se o conteúdo mudou (hash SHA-256), por isso o canal não enche quando ninguém joga.
- **`BACKUP_MIN_GAP_MINUTES`**: vários jogadores a sair seguidos dão um só backup.
- **`BACKUP_FILE_STABLE_SECONDS`**: só copia quando os ficheiros param de mudar. Os `.json` são validados e, se mudarem durante a compressão, o backup repete. **Nunca se guarda um save cortado a meio.**

**Quantos se guardam** (rotação tipo avô-pai-filho): os últimos `LOCAL_KEEP_LAST`, mais o mais recente de cada um dos últimos `LOCAL_KEEP_DAILY` dias, `…_WEEKLY` semanas e `…_MONTHLY` meses. Por omissão são no máximo 24 + 7 + 4 + 6 ficheiros, cobrindo meio ano.

### Google Drive (pronto mas desligado)

Só fica ativo quando **as duas coisas** existirem: `RCLONE_REMOTE` no `.env` e `config/rclone/rclone.conf`. Até lá, o manager mostra `rclone: desativado` e ignora-o. Quando quiseres ativar:

```bash
# 1. no teu PC normal (tem browser): instala o rclone (winget install Rclone.Rclone)
# 2. no servidor:
docker compose run --rm --no-deps --entrypoint rclone manager config --config /config/rclone/rclone.conf
#    n (new remote) → nome: gdrive → storage: drive → client_id/secret: vazio
#    scope: drive.file (só vê os ficheiros que ele próprio cria)
#    "Use web browser to automatically authenticate?" → n
#    → corre no PC normal o comando "rclone authorize ..." que ele mostra e cola o resultado
# 3. no .env: RCLONE_REMOTE=gdrive:Icarus-Backups
docker compose up -d manager
docker compose run --rm --no-deps manager test-rclone
```

Funciona igual para OneDrive, Dropbox, S3, etc. (qualquer destino suportado pelo [rclone](https://rclone.org/overview/)). A pasta remota tem a sua própria rotação (`RCLONE_KEEP_*`, mais generosa por omissão).

### Restaurar

```bash
# põe o .zip em ./backups (ou os .001/.002… de um backup partido; o restore junta-os)
docker compose exec manager python -m icarus_manager restore icarus-20261022-070000-manual.zip
```

Faz backup do estado atual, para o servidor, guarda uma cópia da pasta atual em `data/restore-snapshots/`, extrai o backup e volta a arrancar. Recusa se houver jogadores online, exceto com `--force`.

### Teste de restauro automático

A cada `RESTORE_TEST_DAYS` (7) dias, o manager pega no último backup, extrai todos os ficheiros e valida cada save. O resultado vai para o canal (🧪 OK ou 🧪❌ falhou). Isto apanha zips corrompidos e saves cortados, sem tocar no servidor.

### Ensaio de restauro a sério (manual)

O teste automático não consegue abrir o mundo no jogo. Uma vez por mês, num momento em que ninguém está a jogar:

1. `$M restore <último backup>` (vê os nomes com `/backups`).
2. Entra no jogo e confirma que a base e o inventário estão como esperado.
3. Se algo estiver mal, a pasta anterior está em `data/restore-snapshots/`.

---

## 7. Reinício diário e atualizações

- **Reinício diário às `DAILY_RESTART_TIME` (07:00, hora de Lisboa), só se não houver ninguém a jogar.** Se houver, adia: tenta outra vez a cada 15 min até às 11:00 e depois desiste nesse dia. Antes de reiniciar faz backup, e aproveita para atualizar o jogo (`DAILY_RESTART_UPDATE`). Se o servidor já tiver arrancado depois das 07:00 nesse dia (ex.: o PC reiniciou), não reinicia outra vez.
- **Atualizações** (`AUTO_UPDATE_ENABLED`): a cada 30 min compara a build instalada com a última na Steam. Se houver versão nova, avisa no Discord e atualiza **assim que o servidor estiver vazio**, porque quem atualizar o jogo deixa de conseguir entrar num servidor desatualizado.
- **Reinício do PC**: o contentor tem `restart: unless-stopped` e o arranque do contentor também atualiza o jogo (`UPDATE_ON_START`). Para o Docker arrancar sozinho, vê a [secção 1](#1-preparar-o-pc-servidor).

Quem está online: o manager pergunta ao servidor pelo protocolo de query da Steam (A2S, porta 27015). Se o servidor não responder a isso, usa o `Icarus.log` com os padrões `PLAYER_JOIN_REGEX` / `PLAYER_LEAVE_REGEX`. `/status` mostra o que está a ser usado.

---

## 8. Monitorização: saber quando o PC morre

Os avisos no Discord vêm do próprio PC; se o PC desligar ou a internet cair, ninguém avisa. Para isso:

1. Cria conta grátis em [healthchecks.io](https://healthchecks.io) → *Add Check* (period 5 min, grace 10 min).
2. Em *Integrations*, liga o Discord ou o email.
3. Copia o *ping URL* para `HEALTHCHECK_PING_URL`.

O manager faz ping a cada 5 min enquanto o servidor está bem e envia `/fail` se estiver em baixo. Se os pings pararem, o healthchecks.io avisa-te.

---

## Plano B: servidor nativo no Windows

Se uma atualização do jogo deixar de funcionar com o Wine:

```powershell
docker compose stop icarus                     # NUNCA os dois ao mesmo tempo
powershell -ExecutionPolicy Bypass -File .\windows\run-server.ps1
```

O script instala o SteamCMD e o jogo em `C:\IcarusServer`, lê o mesmo `.env`, usa a **mesma pasta `data`** e reinicia o servidor se ele cair. O Windows pede para permitir o servidor na firewall: aceita.

O manager continua a fazer backups. Os comandos de reiniciar/atualizar e o reinício diário não funcionam neste modo, por isso põe `DAILY_RESTART_ENABLED=false`, `AUTO_UPDATE_ENABLED=false` e `DISCORD_NOTIFY_SERVER_STATUS=false` enquanto o usares. Para voltar ao Docker: fecha o script e `docker compose start icarus`.

---

## Comandos úteis

```bash
docker compose ps                                     # estado (inclui healthcheck)
docker compose logs -f icarus                         # log do servidor
docker compose logs -f manager                        # backups, avisos, agendamentos
M="docker compose exec manager python -m icarus_manager"
$M status
$M connect [--post]
$M backup
$M restart [--update] [--force]
$M update [--force]
$M restore <ficheiro>
docker compose down                                   # pára tudo (./data fica)
```

### Mudar de PC

Copia a pasta do projeto (com `data/`, `.env`, `config/` e, se quiseres, `backups/`) e corre `docker compose up -d --build`. O jogo volta a ser descarregado; o mundo é o mesmo.

---

## Resolução de problemas

### `icarus-server` em `Restarting (127)` com `'bash\r': No such file or directory`

Os scripts ficaram com fins de linha do Windows (CRLF). A imagem já os corrige ao ser construída, por isso isto quer dizer que **a imagem é antiga**:

```powershell
git pull                                   # tem de trazer o .gitattributes
git rm --cached -r . ; git reset --hard    # volta a escrever os ficheiros com os fins de linha certos
docker compose build --no-cache icarus
docker compose up -d
```

> ⚠️ O `git reset --hard` descarta alterações locais a ficheiros do git. O `.env`, o `data/` e o `backups/` não são afetados, porque não estão no git.

### `Failed to load prospect file correctly`: o servidor fica no lobby

O mundo indicado em `LOAD_PROSPECT` não foi encontrado ou não abriu. O log do `icarus` mostra uma linha `AVISO:` com os prospects que existem. Confirma:

1. O ficheiro está em `data/Saved/PlayerData/DedicatedServer/Prospects/` (se o puseste em `./import`, o contentor tem de ser recriado: `docker compose up -d --force-recreate icarus`).
2. `LOAD_PROSPECT` é o nome do ficheiro **sem** `.json`, com as maiúsculas e minúsculas exatas.
3. Se o nome está certo e continua a falhar, o `.json` pode estar incompleto (por exemplo, copiado com o jogo aberto): pede ao teu amigo uma cópia nova, com o jogo fechado.

Entretanto, quem entrar no servidor pode escolher o mundo no lobby (`ALLOW_NON_ADMINS_LAUNCH=True`).

### Bot: `403 Forbidden (error code: 50001): Missing Access`

O Discord não deixa o bot registar os comandos no servidor indicado. Confirma:

1. **`DISCORD_GUILD_ID` é o ID do servidor**, não de um canal: botão direito no **ícone do servidor** (barra da esquerda) → *Copiar ID do servidor*.
2. **O bot foi convidado com os dois scopes**, `bot` **e** `applications.commands`: no portal, *OAuth2* → *URL Generator*, marca os dois, abre o link gerado e adiciona o bot outra vez ao mesmo servidor (não é preciso removê-lo antes).
3. `docker compose restart manager` e procura `Bot do Discord ligado como …` em `docker compose logs manager`.

## Desenvolvimento

```bash
cd manager
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Os testes incluem uma integração que corre o `server/supervisor.sh` verdadeiro com Wine/SteamCMD/servidor falsos (precisa de `jq` e `unzip`). Testa o arranque, o import, o ini, atualizações bem e mal sucedidas, o restauro, as quedas e a paragem graciosa. O CI (GitHub Actions) corre os testes e o ShellCheck, e faz o build das duas imagens.

## Notas e limitações

- Ao parar ou reiniciar é enviado Ctrl+C ao servidor, com `SHUTDOWN_TIMEOUT` segundos para fechar. O Icarus grava periodicamente e quando os jogadores saem, e os reinícios automáticos só acontecem com o servidor vazio.
- Em cada arranque, o `ServerSettings.ini` é reescrito a partir do `.env`, mas só nas chaves geridas. Outras chaves que lá metas à mão mantêm-se.
- O `ServerSettings.ini` nunca entra nos backups (tem as passwords). Para mudar de PC, copia o `.env`.
