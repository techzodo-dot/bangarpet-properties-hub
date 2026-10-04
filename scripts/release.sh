#!/usr/bin/env bash
# Run on every deploy, before (re)starting the app server:
#   bash scripts/release.sh
# Works on a VPS, cPanel terminal, Render/Railway "release"/"pre-deploy" commands and Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

echo "==> Applying database migrations"
"$PY" manage.py migrate --noinput
echo "==> Creating the cache table (used for rate limiting)"
"$PY" manage.py createcachetable
echo "==> Collecting static files"
"$PY" manage.py collectstatic --noinput --verbosity 0
echo "==> Creating the admin login if missing (ADMIN_USERNAME / ADMIN_PASSWORD)"
"$PY" manage.py ensure_admin --keep-password --skip-if-missing
echo "==> Running Django's deployment checks"
"$PY" manage.py check --deploy --fail-level ERROR
echo "Release steps finished."
