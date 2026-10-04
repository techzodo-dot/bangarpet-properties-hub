#!/usr/bin/env bash
# Vercel build step (set as the build command in vercel.json).
# Prepares the Neon database on every deploy: migrations, cache table and the
# admin login from ADMIN_USERNAME / ADMIN_PASSWORD. Static files are served by
# WhiteNoise straight from static/, so no collectstatic is needed.
set -euo pipefail
cd "$(dirname "$0")/.."
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.production}"

PY="$(command -v python3 || command -v python)"
if ! "$PY" -c "import django, PIL, psycopg, dj_database_url, whitenoise, requests" 2>/dev/null; then
  echo "==> Installing dependencies for the build step"
  DEPS="${TMPDIR:-/tmp}/bph-build-deps"
  "$PY" -m pip install --quiet --disable-pip-version-check --target "$DEPS" -r requirements.txt
  export PYTHONPATH="$DEPS${PYTHONPATH:+:$PYTHONPATH}"
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set."
  echo "Connect a PostgreSQL database (Supabase or Neon) to this project and redeploy."
  echo "See docs/DEPLOYMENT.md, option F."
  exit 1
fi

echo "==> Applying database migrations"
"$PY" manage.py migrate --noinput
echo "==> Creating the cache table (used for rate limiting)"
"$PY" manage.py createcachetable
echo "==> Creating the admin login if missing (ADMIN_USERNAME / ADMIN_PASSWORD)"
"$PY" manage.py ensure_admin --keep-password --skip-if-missing
echo "==> Running Django's deployment checks"
"$PY" manage.py check --deploy --fail-level ERROR
echo "Build steps finished."
