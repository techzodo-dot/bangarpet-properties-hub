#!/usr/bin/env bash
# Back up the database, uploaded media and private verification documents.
# Usage: scripts/backup.sh [backup_dir]   (keeps the last 14 backups)
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$APP_DIR/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$BACKUP_DIR/$STAMP"
mkdir -p "$DEST"
cd "$APP_DIR"
set -a; [ -f .env ] && . ./.env; set +a

if [ -z "${DATABASE_URL:-}" ]; then
  # Online, consistent copy of SQLite (safe while the app is running)
  sqlite3 db.sqlite3 ".backup '$DEST/db.sqlite3'"
else
  pg_dump --no-owner --format=custom "$DATABASE_URL" > "$DEST/db.dump"
fi
tar -czf "$DEST/media.tar.gz" -C "$APP_DIR" media 2>/dev/null || true
# Private documents contain personal data: keep the archive permissions tight.
umask 077
tar -czf "$DEST/private_media.tar.gz" -C "$APP_DIR" private_media 2>/dev/null || true
chmod -R go-rwx "$DEST"
ls -1dt "$BACKUP_DIR"/*/ | tail -n +15 | xargs -r rm -rf
echo "Backup written to $DEST"
