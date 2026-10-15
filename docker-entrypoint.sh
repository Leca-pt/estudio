#!/bin/sh
set -e
mkdir -p /data/db /data/media /data/codex /data/artlist /data/cache
chmod 700 /data/codex /data/artlist
alembic upgrade head
python -m app.cli init
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'
