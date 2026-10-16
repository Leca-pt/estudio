# Estúdio

**English** · [Português](README.pt.md)

A web app for AI video pre-production and production — series, films and spots: visual bible, story and scripts,
assets (characters, locations, props) with generated sheets, 3D sets of the locations, storyboard, staging and
cameras, shot-by-shot video generation, editing, a Writers' room shared with AI agents (MCP server) and a work
agenda. The interface is in European Portuguese or English (Settings → Language).

**Stack:** FastAPI + SQLModel/SQLite + Alembic + Jinja2/HTMX (idiomorph) + three.js, in a Docker container. A single
user (the owner), with email and password login.

## Layout

```
app/             code (routes, models, templates, static files, MCP server)
design/          UI design (HANDOFF.md, reference HTML and PNG screens)
docs/            architecture, prompt rules and decision log (in Portuguese)
migrations/      Alembic migrations (run automatically on startup)
deploy/nginx/    nginx site template for a server with HTTPS
scripts/         deploy.sh (publish with the generation queue empty) and backup.sh
tests/           tests (python -m unittest)
data/            volume: database and files — not in the repository
.env             secrets — not in the repository (template in .env.example)
```

## Requirements

- Docker with the Compose plugin (Docker Desktop on Mac/Windows, or Docker Engine on Linux), on an x86-64 processor
  (Intel/AMD); ARM works under emulation (see "local network").
- ~4 GB of disk for the image (it includes headless Chromium, used for 3D set renders and PDFs).
- To generate images and videos: accounts with the providers you want to use (see "AI providers"). Without any, the
  app still works to organize the work, write, and upload files made elsewhere.

## Configuration (.env)

```sh
cp .env.example .env && chmod 600 .env
```

| Variable | What for |
| --- | --- |
| `SECRET_KEY` | signs the session and encrypts the stored API keys. Generate: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Don't change it later (stored keys would become unreadable). |
| `ADMIN_EMAIL` | the email you log in with (the only user). |
| `ADMIN_INITIAL_PASSWORD_HASH` | argon2 hash of the initial password, **in single quotes** (it contains `$`). Generate: `docker compose run --rm --no-deps app python -m app.cli hash-password`. You change the password on first login. |
| `COOKIE_SECURE` | `true` with HTTPS (server); `false` on `http://localhost`. |
| `PUBLIC_URL` | the address where the app is opened (e.g. `https://estudio.example.com` or `http://localhost:8010`). The MCP connector and its OAuth login use it. |
| `AUTHOR_NAME` | your name in the Writers' room (e.g. `Ana`); "Autor" by default. |
| `CLAUDE_CODE_OAUTH_TOKEN` | optional: connects the Assistant and the prompt writer to Claude (see below). Alternatively `ANTHROPIC_API_KEY`. |

## Install on a local machine (Mac, Windows, Linux)

```sh
git clone <this repository> estudio && cd estudio
cp .env.example .env
# in .env: SECRET_KEY, ADMIN_EMAIL, COOKIE_SECURE=false, PUBLIC_URL=http://localhost:8010
docker compose build
docker compose run --rm --no-deps app python -m app.cli hash-password   # type the password; paste the hash into .env
mkdir -p data
docker compose up -d
```

Open <http://localhost:8010> and log in with `ADMIN_EMAIL` and the initial password. On startup the app creates the
database and applies the migrations and the initial lists. Data lives in `./data` (SQLite database and media files).

On Linux, if the container can't write to `./data`: `sudo chown -R 10001:10001 data` (the container runs as an
unprivileged user, uid 10001).

The MCP connector only works with Claude/ChatGPT on the web if the app is reachable from the internet over HTTPS (see
below). Locally you can use it from MCP clients on the same machine (e.g. Claude Code) with
`PUBLIC_URL=http://localhost:8010`.

## Install on a local-network server (mini PC, NAS, always-on computer)

To use the app from several computers, tablets or phones on the same network, without exposing it to the internet:

1. On the computer that will serve the app, install Docker and follow the local install above, with these lines in
   `.env`:
   ```sh
   BIND_ADDRESS=0.0.0.0                  # accept connections from the whole local network (not only this computer)
   PORT=8010
   PUBLIC_URL=http://192.168.1.50:8010   # that computer's fixed IP on the network (or its name, e.g. http://estudio.local:8010)
   COOKIE_SECURE=false                   # no HTTPS on the local network
   ```
2. Give that computer a fixed IP (on the router: "DHCP reservation") so the address doesn't change.
3. If there's a firewall (Windows, `ufw` on Linux, NAS), open port `8010` to the local network only.
4. On the other devices, open `http://192.168.1.50:8010`.

Notes:
- Without HTTPS, the password and the session travel in clear text on the network: use this only on a network you
  own, never on public Wi-Fi, and **don't** forward the port on the router to the internet.
- For HTTPS at home: a proxy such as Caddy or nginx with your own certificate, then `COOKIE_SECURE=true` and
  `PUBLIC_URL=https://…`.
- The Claude and ChatGPT web MCP connector needs to reach the app from the internet over HTTPS. On a local network,
  without opening ports, the simplest way is a tunnel (e.g. Cloudflare Tunnel or Tailscale Funnel) to an `https://…`
  address, which becomes the `PUBLIC_URL` (with `COOKIE_SECURE=true`). Without a tunnel the rest of the app works the
  same.
- On ARM processors (Apple Silicon Macs, Raspberry Pi, some NAS) the image runs under x86-64 emulation: it works, but
  building and rendering are slower. 4 GB of RAM or more is recommended.

## Install on a server (VPS with HTTPS)

1. **DNS:** an `A` record (and `AAAA` if you have IPv6) for your domain, e.g. `estudio.example.com`, pointing at the
   server's IP.
2. **Code and configuration:**
   ```sh
   git clone <this repository> /opt/estudio && cd /opt/estudio
   cp .env.example .env && chmod 600 .env
   # in .env: SECRET_KEY, ADMIN_EMAIL, COOKIE_SECURE=true, PUBLIC_URL=https://estudio.example.com, AUTHOR_NAME
   docker compose build
   docker compose run --rm --no-deps app python -m app.cli hash-password   # → ADMIN_INITIAL_PASSWORD_HASH
   mkdir -p data && chown -R 10001:10001 data && chmod 700 data
   docker compose up -d
   ```
   The app listens only on `127.0.0.1:8010`; nginx is what exposes it.
3. **nginx and certificate (Let's Encrypt):**
   ```sh
   sed 's/estudio.example.com/YOUR_DOMAIN/g' deploy/nginx/estudio.conf.example > /etc/nginx/sites-available/estudio.conf
   ln -s /etc/nginx/sites-available/estudio.conf /etc/nginx/sites-enabled/estudio.conf
   # the first time the certificate doesn't exist yet: comment out the «listen 443» block and reload nginx, then:
   certbot certonly --webroot -w /var/www/html -d YOUR_DOMAIN
   # put the 443 block back
   nginx -t && systemctl reload nginx
   ```
   nginx accepts requests up to 200 MB (video clips) and the certificate renews with certbot's timer.
4. **Daily backup** (optional): `scripts/backup.sh` (database and media, a single file in `/var/backups/director/`);
   for example in `/etc/cron.d/estudio-backup`:
   `30 3 * * * root /opt/estudio/scripts/backup.sh >> /var/log/estudio-backup.log 2>&1`.
5. **Update:** `git pull && ./scripts/deploy.sh` (builds the image and only restarts with the generation queue empty,
   so paid requests aren't repeated).

## AI providers

None of these is required; each one enables part of the generation.

- **Claude (Assistant, prompt writer, Artlist quotes):** `claude setup-token` on a machine with Claude Code and your
  subscription; put the token in `CLAUDE_CODE_OAUTH_TOKEN` and run `docker compose up -d`.
- **Artlist (images and video):** Settings → Connections → connect the account (the app uses Artlist's MCP server
  through Claude Code in the container). Needs the Claude token above.
- **GPT Image (ChatGPT subscription, via the Codex CLI):** `docker compose exec app codex login --device-auth`.
- **fal.ai, BytePlus (Seedance) and others:** keys in Settings → API keys (encrypted in the database).

## MCP connector (Claude, ChatGPT)

The app is an MCP server at `<PUBLIC_URL>/mcp`, with OAuth (dynamic client registration) and your login as the
authorization. In Claude: Settings → Connectors → Add custom connector → `<PUBLIC_URL>/mcp`. In ChatGPT: developer
mode → Apps & connectors → Create, with the same address and OAuth. The tools cover projects, story and script,
assets, 3D sets, storyboard, video, the Writers' room and the Agenda; nothing is approved without you, and paid
generations ask for cost confirmation.

## Interface language

Settings → **Idioma · Language** switches the interface between Portuguese (default) and English. The app's pages are
written in Portuguese and translated on the way out by a dictionary (`app/i18n/en.json`, built from the code with the
glossary in `app/i18n/GLOSSARY.md`); project content (scripts, names, notes, prompts) is never translated. The
Assistant answers in the chosen language. When adding UI text, add its translation to `en.json` (crawling with
`I18N_COLLECT=<file>` lists what's missing).

## Tests

```sh
docker compose exec app python -m unittest discover -s tests -v
```

## Access

- Only the email set in `ADMIN_EMAIL` has access.
- **Changing the password on first login is mandatory.** The new password is stored (as an argon2 hash) in the
  database. `.env` only holds the initial password.
- To change the password later: **Change password** menu. All other sessions end.
- Forgot the password: `docker compose exec app python -m app.cli reset-password` restores the initial password from
  `.env`, which must be changed at the next login.
- At most 5 failed attempts per IP in 15 minutes.

## Files

- Files in `data/media/<PREFIX>/`: `assets/<CODE>/vN.ext` (sheets), `assets/<CODE>/retrato/vN.ext` (character
  portraits), `assets/<CODE>/maquete/` (location renders and views), `planos/<id>/clip|maquete|fotogramas/`. The
  database only stores the path.
- Download and zip names follow the code: `PRJ_ANA_sheet.png`, `AVT_C01_P02_v3.mp4`, and in the edit export
  `01_AVT_C01_P01.mp4`… in shot order.
- Videos: MP4 or MOV up to 200 MB; the duration is read from the file itself (`mvhd` box).
- Accepted uploads: PNG, JPG and WEBP up to 25 MB (type checked from the content with Pillow). nginx accepts requests
  up to 200 MB, for video clips.
- Rejecting a result removes it from where it was used. The record stays, marked as rejected, and can never be chosen
  as a reference.

## Prompt rules

Prompts (sheets, portraits, views, video, cover) are written by the prompt writer, following the rules in
`docs/PROMPT_RULES.md` and the guides in Settings → Prompts (see `docs/DECISOES.md`); the app doesn't assemble them.
Next to each prompt the warnings from section 5 appear — none of them blocks; limits and word lists are editable in
Settings → Prompt checks. Each edited prompt keeps versions that can be restored. The Assistant receives these rules
whenever it proposes or fixes a prompt.

## Assistant (Claude Code)

The Assistant panel calls the Claude Code CLI **inside the container**, as the app's unprivileged user, in
non-interactive mode (`claude -p`): no tools (`--tools ""`), `--restricted`, no third-party MCP or settings, no saved
sessions, in an empty temporary folder and with a time limit (Settings → Models and prompts → Assistant). The reply is
validated JSON (`--json-schema`); each proposal is checked against the fields allowed on the current screen and is
only saved with **Apply**.

Connect (once):
```sh
claude setup-token            # on the VPS, with your Claude account; copy the token it prints
nano .env                     # CLAUDE_CODE_OAUTH_TOKEN=<token>
docker compose up -d          # recreates the container with the token
```
Instead of the token, `ANTHROPIC_API_KEY=` in `.env`.

## GPT Image (Codex CLI)

Generates and edits images (asset sheets, portraits, 3D set views of locations and the project cover) with the GPT
Image of the ChatGPT subscription, through the Codex CLI installed in the container (version pinned in the
`Dockerfile`). The login is kept in `data/codex/` (outside the backup):
```sh
docker compose exec app codex login --device-auth    # open the link, enter the code
```
Requests go into a queue (one at a time) and the result appears in Results as "pending", with the GPT tag. "Edit with
GPT" redoes an image with a change described in English. Codex runs as an unprivileged user, in a temporary folder,
with a minimal environment (none of the app's secrets); its shell is blocked by the sandbox. Each image uses 3–5× more
of the Codex limit than a message. Enable/disable and time limit in Settings.

## Backup

`scripts/backup.sh` runs every day at 03:30 (`/etc/cron.d/director-backup`) and creates
`/var/backups/director/director-backup.tar.gz`, with the database and `data/media`. There is **only one backup**: the
new one replaces the previous one, and only once it's complete. Log: `/var/log/director-backup.log`.

Restore:
```sh
docker compose down
tar -xzf /var/backups/director/director-backup.tar.gz -C data/
chown -R 10001:10001 data
docker compose up -d
```

## Operation

```sh
docker compose logs -f                 # logs
docker compose up -d --build           # update after code changes
docker compose exec app alembic revision --autogenerate -m "..."   # new migration (dev)
```

Migrations run with `PRAGMA foreign_keys=OFF` (see `migrations/env.py`): in SQLite, batch mode recreates tables and,
with foreign keys on, the `DROP` would cascade-delete the child tables' data. Always run `scripts/backup.sh` before
publishing a migration.

## Look

`app/static/css/estudio.css` is the design CSS, copied unchanged. The app's adjustments (scroll, tablet/phone sizes,
extra components) are in `app/static/css/app.css`. The light theme is stored in the browser (Settings → Account).

## API keys

Settings → API keys stores the keys of model providers (Anthropic, OpenAI, Google, Mistral, Z.ai, Alibaba, DeepSeek,
MiniMax, xAI, BytePlus, Black Forest Labs, Kling, Azure, Meta, Runway, Luma, OpenRouter, fal.ai, Replicate). They're
encrypted (Fernet) in the database with a key derived from `SECRET_KEY`: the backup doesn't expose them, but changing
`SECRET_KEY` makes them unreadable (the page asks you to enter them again). They're never shown after being saved.
Having a key stored doesn't connect the provider to the app: each integration (Assistant, images, video) is separate.

## License

Copyright (C) 2026 Leca-pt

This program is free software: you can redistribute it and/or modify it under the terms of the **GNU Affero General
Public License** as published by the Free Software Foundation, either version 3 of the License, or (at your option)
any later version. It is distributed in the hope that it will be useful, but **without any warranty**; without even
the implied warranty of merchantability or fitness for a particular purpose. The full text is in [`LICENSE`](LICENSE).

In short: you can use, study, change and share the app. If you change it and run it for other people (for example,
on a server reachable from the internet), you must give them access to the source code of your version, also under
the AGPL-3.0.

Third-party components keep their own licenses (e.g. three.js, MIT, in `app/static/vendor/three/`).
