# Estúdio

App web de pré-produção e produção de vídeo com IA, para séries, filmes e spots: bíblia visual, história e guiões,
assets (personagens, locais, adereços) com folhas geradas, maquetes 3D dos locais, storyboard, encenação e câmaras,
geração de vídeo plano a plano, montagem, uma Sala de argumentistas partilhada com agentes de IA (servidor MCP) e uma
agenda de trabalho. Interface em português europeu.

**Stack:** FastAPI + SQLModel/SQLite + Alembic + Jinja2/HTMX (idiomorph) + three.js, num contentor Docker. Um só
utilizador (o dono), com login por email e password.

## Estrutura

```
app/             código (rotas, modelos, templates, estáticos, servidor MCP)
design/          design da UI (HANDOFF.md, ecrãs HTML e PNG de referência)
docs/            arquitetura, regras dos prompts e registo de decisões
migrations/      migrações Alembic (correm sozinhas no arranque)
deploy/nginx/    modelo do site nginx para um servidor com HTTPS
scripts/         deploy.sh (publicar com a fila de geração vazia) e backup.sh
tests/           testes (python -m unittest)
data/            volume: base de dados e ficheiros — fora do repositório
.env             segredos — fora do repositório (modelo em .env.example)
```

## Requisitos

- Docker com o plugin Compose (Docker Desktop no Mac/Windows, ou Docker Engine no Linux), num processador x86-64
  (Intel/AMD); ARM funciona em emulação (ver «rede local»).
- ~4 GB de disco para a imagem (inclui o Chromium headless, usado para os renders das maquetes e para os PDFs).
- Para gerar imagens e vídeos: contas nos fornecedores que quiseres usar (ver «Fornecedores de IA»). Sem nenhum, a app
  funciona para organizar o trabalho, escrever e carregar ficheiros feitos noutro lado.

## Configuração (.env)

```sh
cp .env.example .env && chmod 600 .env
```

| Variável | Para quê |
| --- | --- |
| `SECRET_KEY` | assina a sessão e cifra as chaves de API guardadas. Gerar: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Não a mudes depois (as chaves guardadas ficariam ilegíveis). |
| `ADMIN_EMAIL` | o email com que entras (o único utilizador). |
| `ADMIN_INITIAL_PASSWORD_HASH` | hash argon2 da password inicial, **entre aspas simples** (tem `$`). Gerar: `docker compose run --rm --no-deps app python -m app.cli hash-password`. Mudas a password no primeiro login. |
| `COOKIE_SECURE` | `true` com HTTPS (servidor); `false` em `http://localhost`. |
| `PUBLIC_URL` | o endereço onde a app é aberta (ex.: `https://estudio.example.com` ou `http://localhost:8010`). O conector MCP e o login OAuth dele usam-no. |
| `AUTHOR_NAME` | o teu nome na Sala de argumentistas (ex.: `Ana`); por omissão «Autor». |
| `CLAUDE_CODE_OAUTH_TOKEN` | opcional: liga o Assistente e o escritor de prompts ao Claude (ver abaixo). Em alternativa `ANTHROPIC_API_KEY`. |

## Instalação numa máquina local (Mac, Windows, Linux)

```sh
git clone <este repositório> estudio && cd estudio
cp .env.example .env
# no .env: SECRET_KEY, ADMIN_EMAIL, COOKIE_SECURE=false, PUBLIC_URL=http://localhost:8010
docker compose build
docker compose run --rm --no-deps app python -m app.cli hash-password   # escreve a password; cola o hash no .env
mkdir -p data
docker compose up -d
```

Abre <http://localhost:8010> e entra com o `ADMIN_EMAIL` e a password inicial. No arranque, a app cria a base de dados,
aplica as migrações e as listas iniciais. Os dados ficam em `./data` (base de dados SQLite e ficheiros de media).

No Linux, se o contentor não conseguir escrever em `./data`: `sudo chown -R 10001:10001 data` (o contentor corre como
utilizador sem privilégios, uid 10001).

O conector MCP só funciona com o Claude/ChatGPT na web se a app estiver acessível pela internet com HTTPS (ver abaixo).
Em local, podes usá-lo a partir de clientes MCP na mesma máquina (ex.: Claude Code) com `PUBLIC_URL=http://localhost:8010`.

## Instalação num servidor da rede local (mini PC, NAS, computador sempre ligado)

Para usar a app a partir de vários computadores, tablets ou telemóveis da mesma rede, sem a pôr na internet:

1. No computador que vai servir a app, instala o Docker e segue a instalação local acima, com estas linhas no `.env`:
   ```sh
   BIND_ADDRESS=0.0.0.0                  # aceita ligações de toda a rede local (e não só do próprio computador)
   PORT=8010
   PUBLIC_URL=http://192.168.1.50:8010   # o IP fixo desse computador na rede (ou o nome, ex. http://estudio.local:8010)
   COOKIE_SECURE=false                   # sem HTTPS na rede local
   ```
2. Dá a esse computador um IP fixo (no router: «reserva de DHCP») para o endereço não mudar.
3. Se houver firewall (Windows, `ufw` no Linux, NAS), abre a porta `8010` só para a rede local.
4. Nos outros aparelhos, abre `http://192.168.1.50:8010`.

Notas:
- Sem HTTPS, a password e a sessão passam em claro na rede: usa isto só numa rede tua, nunca em Wi-Fi público, e
  **não** faças reencaminhamento da porta no router para a internet.
- Querendo HTTPS dentro de casa: um proxy como Caddy ou o nginx com um certificado próprio, e aí `COOKIE_SECURE=true` e
  `PUBLIC_URL=https://…`.
- O conector MCP do Claude e do ChatGPT na web precisa de chegar à app pela internet com HTTPS. Numa rede local, sem
  abrir portas, a forma mais simples é um túnel (ex.: Cloudflare Tunnel ou Tailscale Funnel) para um endereço
  `https://…`, que passa a ser o `PUBLIC_URL` (com `COOKIE_SECURE=true`). Sem túnel, o resto da app funciona na mesma.
- Em processadores ARM (Mac com Apple Silicon, Raspberry Pi, alguns NAS) a imagem corre em emulação x86-64: funciona,
  mas a construção e os renders são mais lentos. Recomenda-se 4 GB de RAM ou mais.

## Instalação num servidor (VPS com HTTPS)

1. **DNS:** um registo `A` (e `AAAA`, se tiveres IPv6) do teu domínio, ex. `estudio.example.com`, para o IP do servidor.
2. **Código e configuração:**
   ```sh
   git clone <este repositório> /opt/estudio && cd /opt/estudio
   cp .env.example .env && chmod 600 .env
   # no .env: SECRET_KEY, ADMIN_EMAIL, COOKIE_SECURE=true, PUBLIC_URL=https://estudio.example.com, AUTHOR_NAME
   docker compose build
   docker compose run --rm --no-deps app python -m app.cli hash-password   # → ADMIN_INITIAL_PASSWORD_HASH
   mkdir -p data && chown -R 10001:10001 data && chmod 700 data
   docker compose up -d
   ```
   A app fica a ouvir só em `127.0.0.1:8010`; quem a expõe é o nginx.
3. **nginx e certificado (Let's Encrypt):**
   ```sh
   sed 's/estudio.example.com/O_TEU_DOMINIO/g' deploy/nginx/estudio.conf.example > /etc/nginx/sites-available/estudio.conf
   ln -s /etc/nginx/sites-available/estudio.conf /etc/nginx/sites-enabled/estudio.conf
   # na primeira vez o certificado ainda não existe: comenta o bloco «listen 443» e recarrega o nginx, depois:
   certbot certonly --webroot -w /var/www/html -d O_TEU_DOMINIO
   # volta a pôr o bloco 443
   nginx -t && systemctl reload nginx
   ```
   O nginx aceita pedidos até 200 MB (clips de vídeo) e faz a renovação do certificado com o timer do certbot.
4. **Backup diário** (opcional): `scripts/backup.sh` (base de dados e media, um só ficheiro em
   `/var/backups/director/`); por exemplo em `/etc/cron.d/estudio-backup`:
   `30 3 * * * root /opt/estudio/scripts/backup.sh >> /var/log/estudio-backup.log 2>&1`.
5. **Atualizar:** `git pull && ./scripts/deploy.sh` (constrói a imagem e só reinicia com a fila de geração vazia, para
   não repetir pedidos pagos).

## Fornecedores de IA

Nada disto é obrigatório; cada um liga uma parte da geração.

- **Claude (Assistente, escritor de prompts, cotações do Artlist):** `claude setup-token` numa máquina com o Claude
  Code e a tua subscrição; põe o token em `CLAUDE_CODE_OAUTH_TOKEN` e `docker compose up -d`.
- **Artlist (imagens e vídeo):** Configuração → Ligações → ligar a conta (a app usa o servidor MCP do Artlist através
  do Claude Code no contentor). Precisa do token do Claude acima.
- **GPT Image (subscrição ChatGPT, pelo Codex CLI):** `docker compose exec app codex login --device-auth`.
- **fal.ai, BytePlus (Seedance) e outros:** chaves em Configuração → Chaves de API (cifradas na base de dados).

## Conector MCP (Claude, ChatGPT)

A app é um servidor MCP em `<PUBLIC_URL>/mcp`, com OAuth (registo dinâmico de clientes) e o teu login como
autorização. No Claude: Definições → Conectores → Adicionar conector personalizado → `<PUBLIC_URL>/mcp`. No ChatGPT:
modo de programador → Apps e conectores → Criar, com o mesmo endereço e OAuth. As ferramentas cobrem projetos,
história e guião, assets, maquetes, storyboard, vídeo, Sala de argumentistas e Agenda; nada é aprovado sem ti e
gerações pagas pedem a confirmação do custo.

## Testes

```sh
docker compose exec app python -m unittest discover -s tests -v
```

## Acesso

- Só o email definido em `ADMIN_EMAIL` tem acesso.
- **No primeiro login é obrigatório mudar a password.** A nova password fica guardada (em hash
  argon2) na base de dados. O `.env` guarda apenas a password inicial.
- Mudar a password mais tarde: menu **Alterar password**. Todas as outras sessões terminam.
- Esqueci-me da password: `docker compose exec app python -m app.cli reset-password` repõe
  a password inicial do `.env`, que terá de ser mudada no login seguinte.
- Máximo de 5 tentativas falhadas por IP em 15 minutos.

## Ficheiros

- Ficheiros em `data/media/<PREFIXO>/`: `assets/<CÓDIGO>/vN.ext` (folhas), `assets/<CÓDIGO>/retrato/vN.ext` (retratos das personagens), `assets/<CÓDIGO>/maquete/` (renders e vistas dos locais),
  `planos/<id>/clip|maquete|fotogramas/`. A base de dados guarda só o caminho.
- Os nomes ao descarregar e no zip seguem o código: `PRJ_ANA_sheet.png`, `AVT_C01_P02_v3.mp4`, e na exportação da montagem `01_AVT_C01_P01.mp4`… pela ordem dos planos.
- Vídeos: MP4 ou MOV até 200 MB; a duração é lida do próprio ficheiro (caixa `mvhd`).
- Uploads aceites: PNG, JPG e WEBP até 25 MB (tipo verificado pelo conteúdo com Pillow). O nginx
  aceita pedidos até 200 MB, a pensar nos clips de vídeo.
- Rejeitar um resultado apaga o ficheiro do disco e retira-o de onde era usado. O registo fica,
  marcado como rejeitado, e nunca pode ser escolhido como referência.

## Regras de prompts

Os prompts (folhas, retratos, vistas, vídeo, capa) são escritos pelo escritor de prompts, com as regras de
`docs/PROMPT_RULES.md` e as guias de Configuração → Prompts (ver `docs/DECISOES.md`); a app não os monta. Junto a cada prompt aparecem os avisos da secção 5 — nenhum bloqueia; limites e
listas de palavras editáveis em Configuração → Validações de prompts. Cada prompt editado guarda
versões que se podem repor. O Assistente recebe estas regras sempre que propõe ou corrige um prompt.

## Assistente (Claude Code)

O painel do Assistente chama o Claude Code CLI **dentro do contentor**, com o utilizador sem
privilégios da app, em modo não interativo (`claude -p`): sem ferramentas (`--tools ""`),
`--restricted`, sem MCP nem definições de terceiros, sem sessões gravadas, numa pasta temporária
vazia e com tempo limite (Configuração → Modelos e prompts → Assistente). A resposta é JSON
validado (`--json-schema`); cada proposta é verificada contra os campos permitidos no ecrã atual
e só é gravada com **Aplicar**.

Ligar (uma vez):
```sh
claude setup-token            # no VPS, com a tua conta Claude; copia o token que aparece
nano .env                     # CLAUDE_CODE_OAUTH_TOKEN=<token>
docker compose up -d          # recria o contentor com o token
```
Em alternativa ao token, `ANTHROPIC_API_KEY=` no `.env`.

## GPT Image (Codex CLI)

Gera e edita imagens (folhas de asset, retratos, vistas da maquete dos locais e capa do projeto) com o
GPT Image da subscrição ChatGPT, através do Codex CLI instalado no contentor (versão fixa no
`Dockerfile`). O login fica em `data/codex/` (fora do backup):
```sh
docker compose exec app codex login --device-auth    # abre o link, introduz o código
```
Os pedidos entram numa fila (um de cada vez) e o resultado aparece nos Resultados como "por
decidir", com a etiqueta GPT. "Editar com GPT" refaz uma imagem com uma alteração descrita em
inglês. O Codex corre como utilizador sem privilégios, numa pasta temporária, com um ambiente
mínimo (sem os segredos da app); a shell dele fica bloqueada pela sandbox. Cada imagem gasta o
limite do Codex 3–5× mais do que uma mensagem. Ligar/desligar e tempo limite em Configuração.

## Backup

`scripts/backup.sh` corre todos os dias às 03:30 (`/etc/cron.d/director-backup`) e cria
`/var/backups/director/director-backup.tar.gz`, com a base de dados e `data/media`. Existe
**apenas um backup**: o novo substitui o anterior, e só depois de estar completo.
Log: `/var/log/director-backup.log`.

Restaurar:
```sh
docker compose down
tar -xzf /var/backups/director/director-backup.tar.gz -C data/
chown -R 10001:10001 data
docker compose up -d
```

## Operação

```sh
docker compose logs -f                 # logs
docker compose up -d --build           # atualizar após alterações ao código
docker compose exec app alembic revision --autogenerate -m "..."   # nova migração (dev)
```

As migrações correm com `PRAGMA foreign_keys=OFF` (ver `migrations/env.py`): no SQLite, o modo
batch recria tabelas e, com as chaves estrangeiras ativas, o `DROP` apagaria em cascata os dados
das tabelas filhas. Fazer sempre `scripts/backup.sh` antes de publicar uma migração.

## Visual

`app/static/css/estudio.css` é o CSS do design, copiado sem alterações. Os ajustes da app (scroll,
tamanhos em tablet/telemóvel, componentes extra) estão em `app/static/css/app.css`. O tema claro
guarda-se no browser (Configuração → Conta).

## Chaves de API

Configuração → Chaves de API guarda as chaves dos fornecedores de modelos (Anthropic, OpenAI, Google, Mistral, Z.ai,
Alibaba, DeepSeek, MiniMax, xAI, BytePlus, Black Forest Labs, Kling, Azure, Meta, Runway, Luma, OpenRouter, fal.ai,
Replicate). Ficam cifradas (Fernet) na base de dados com uma chave derivada de `SECRET_KEY`: o backup não as expõe,
mas mudar o `SECRET_KEY` torna-as ilegíveis (a página pede para as introduzir de novo). Nunca são mostradas depois de
gravadas. Ter uma chave guardada não liga o fornecedor à app: cada integração (Assistente, imagens, vídeo) é à parte.
