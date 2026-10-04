#!/usr/bin/env bash
# Restore a backup created by backup.sh. STOP the application first.
# Usage: scripts/restore.sh backups/20250101-020000
set -euo pipefail
SRC="${1:?Usage: restore.sh <backup_dir>}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
set -a; [ -f .env ] && . ./.env; set +a
read -r -p "This overwrites the current database and media. Type RESTORE to continue: " ok
[ "$ok" = "RESTORE" ] || { echo "Aborted."; exit 1; }
if [ -f "$SRC/db.sqlite3" ]; then
  cp db.sqlite3 "db.sqlite3.before-restore.$(date +%s)" 2>/dev/null || true
  cp "$SRC/db.sqlite3" db.sqlite3
elif [ -f "$SRC/db.dump" ]; then
  pg_restore --clean --if-exists --no-owner --dbname "$DATABASE_URL" "$SRC/db.dump"
fi
[ -f "$SRC/media.tar.gz" ] && tar -xzf "$SRC/media.tar.gz" -C "$APP_DIR"
[ -f "$SRC/private_media.tar.gz" ] && tar -xzf "$SRC/private_media.tar.gz" -C "$APP_DIR"
echo "Restore complete. Run: python manage.py migrate && restart the app."
