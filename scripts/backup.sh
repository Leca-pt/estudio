#!/bin/sh
# Backup diário da base de dados e de /data/media.
# Mantém apenas UM backup: o novo substitui o anterior (só depois de estar completo).
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/director}"
DEST="$BACKUP_DIR/director-backup.tar.gz"
TMP="$BACKUP_DIR/.director-backup.tar.gz.tmp"
SNAPSHOT="$APP_DIR/data/db/backup-snapshot.sqlite3"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
trap 'rm -f "$TMP" "$SNAPSHOT"' EXIT

# Cópia consistente da BD feita pela própria app (seguro com a app a correr).
docker compose -f "$APP_DIR/docker-compose.yml" exec -T app \
    python -m app.cli backup-db /data/db/backup-snapshot.sqlite3 >/dev/null

tar -czf "$TMP" -C "$APP_DIR/data" \
    --transform 's|^db/backup-snapshot.sqlite3|db/director.sqlite3|' \
    db/backup-snapshot.sqlite3 media

mv -f "$TMP" "$DEST"
echo "$(date -Is) backup ok: $DEST ($(du -h "$DEST" | cut -f1))"
