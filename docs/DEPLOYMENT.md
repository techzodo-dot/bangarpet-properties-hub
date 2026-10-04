# Deployment guide (Serverbyt)

Serverbyt offers both VPS/cloud servers and cPanel shared hosting. Option A (VPS) is recommended;
Option B covers cPanel's "Setup Python App".

## 1. Prepare environment variables

Copy `.env.example` to `.env` on the server and set at least:

```
DJANGO_SETTINGS_MODULE=config.settings.production
SECRET_KEY=<50+ random characters>
DEBUG=False
ALLOWED_HOSTS=example.com,www.example.com
CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
SITE_URL=https://example.com
```

Generate a key: `python -c "from django.core.management.utils import get_random_secret_key as g; print(g()+g())"`.
Production settings refuse to start without a strong `SECRET_KEY` and `ALLOWED_HOSTS`.
`chmod 600 .env` — never commit it.

## 2. Option A — VPS (Ubuntu) with Gunicorn + Nginx

```bash
sudo apt install python3-venv python3-dev nginx sqlite3 certbot python3-certbot-nginx
sudo adduser --system --group --home /srv/bph bph
sudo -u bph git clone <repo-url> /srv/bph/app
cd /srv/bph/app
sudo -u bph python3 -m venv /srv/bph/venv
sudo -u bph /srv/bph/venv/bin/pip install -r requirements.txt
sudo -u bph cp .env.example .env && sudo -u bph nano .env        # fill in values
sudo -u bph /srv/bph/venv/bin/python manage.py migrate
sudo -u bph /srv/bph/venv/bin/python manage.py createcachetable   # shared cache for rate limits
sudo -u bph /srv/bph/venv/bin/python manage.py collectstatic --noinput
sudo -u bph /srv/bph/venv/bin/python manage.py createsuperuser
sudo -u bph /srv/bph/venv/bin/python manage.py check --deploy
```

* Gunicorn: `sudo cp deploy/bph.service /etc/systemd/system/ && sudo systemctl enable --now bph`
* Nginx: copy `deploy/nginx.conf.example` to `/etc/nginx/sites-available/bph`, replace `example.com`,
  link it into `sites-enabled`, then `sudo nginx -t && sudo systemctl reload nginx`.
* HTTPS: `sudo certbot --nginx -d example.com -d www.example.com` (auto-renews).
* DNS: point an `A` record for `example.com` and `www` to the server IP before running certbot.
* Cron: install `deploy/crontab.example` for the `bph` user (`crontab -u bph -e`).
* Permissions: `media/` must be writable by the app user; `private_media/` should be `700` and must
  **never** be exposed by the web server.

### Updating

```bash
cd /srv/bph/app && sudo -u bph git pull
sudo -u bph /srv/bph/venv/bin/pip install -r requirements.txt
sudo -u bph /srv/bph/venv/bin/python manage.py migrate
sudo -u bph /srv/bph/venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart bph
```

## 3. Option B — cPanel "Setup Python App"

1. cPanel → Setup Python App → Create application: Python 3.11+, application root e.g. `bph`,
   application URL your domain, startup file `passenger_wsgi.py`, entry point `application`.
2. Upload the code (Git Version Control or File Manager) into the application root.
3. Enter the virtual environment (command shown in cPanel) and run
   `pip install -r requirements.txt`, then `migrate`, `createcachetable`, `collectstatic --noinput`,
   `createsuperuser`.
4. Create `.env` in the application root (see step 1). Set `USE_X_FORWARDED_PROTO=True`.
5. Media: if you cannot add a web-server alias for `/media/`, set `SERVE_MEDIA=True` (Django then serves
   public media; verification documents are never served this way).
6. SSL: cPanel → SSL/TLS Status → AutoSSL for the domain.
7. Cron: cPanel → Cron Jobs → hourly
   `cd ~/bph && ~/virtualenv/bph/3.11/bin/python manage.py run_scheduled_tasks`.
8. Restart the app from the Python App page after every change.

## 4. Static and media files

* Static files: `collectstatic` → `STATIC_ROOT` (default `staticfiles/`), served by WhiteNoise with
  compressed, fingerprinted filenames (or directly by Nginx).
* Public media (`MEDIA_ROOT`, default `media/`): property photos (re-encoded, EXIF stripped), banners, avatars.
* Private documents (`PRIVATE_MEDIA_ROOT`, default `private_media/`): served only through
  `/accounts/documents/<id>/` after a permission check.
* Cloud storage: switch `STORAGES["default"]` to `django-storages` (S3-compatible) for public media.
  Keep verification documents in a private bucket and serve them via short-lived signed URLs from the
  same permission-checked view.

## 5. SQLite vs PostgreSQL

SQLite is fine for development and a small launch **on a server with persistent disk**, but:

* only one write can happen at a time (we set a 20-second lock timeout); heavy concurrent writes
  (many enquiries/uploads per second) will queue or fail,
* keep Gunicorn workers low (default ≤ 3),
* backups must use `sqlite3 .backup` (as `scripts/backup.sh` does), not a plain file copy while running.

Move to PostgreSQL when traffic grows:

```bash
pip install "psycopg[binary]"
python manage.py dumpdata --natural-foreign --natural-primary \
    --exclude contenttypes --exclude auth.permission --exclude sessions --indent 2 > data.json
# set DATABASE_URL=postgres://user:pass@host:5432/bph in .env
python manage.py migrate
python manage.py loaddata data.json
```

No code changes are required; all queries use the ORM and the partial unique constraints are supported by PostgreSQL.

## 6. Backups and restore

* `scripts/backup.sh [dir]` — consistent SQLite (or `pg_dump`) backup plus `media` and `private_media`
  archives; keeps the last 14. Schedule nightly (see crontab example) and copy backups off-server
  (they contain personal data — store encrypted).
* `scripts/restore.sh backups/<timestamp>` — stop the app, restore, run `migrate`, restart.
* Test a restore on a staging copy at least once before launch.

## 7. Logging and monitoring

* Production logs go to stdout (journald / cPanel logs) and `logs/bph.log` (rotated, 5 × 5 MB).
* Set `ADMIN_ERROR_EMAILS=you@example.com` to receive emails for server errors (needs SMTP).
* Watch `logs/cron.log` for scheduled task output.

## 8. Go-live checklist

- [ ] `.env` set, `DEBUG=False`, `python manage.py check --deploy` clean (HSTS subdomain/preload warnings are opt-in)
- [ ] HTTPS working, HTTP redirects to HTTPS
- [ ] Superuser created with a strong password; Management → Settings filled in (contact, WhatsApp, address, socials)
- [ ] Locations, categories and plan prices reviewed
- [ ] Email working (try password reset); Razorpay test payment and webhook verified; then live keys
- [ ] WhatsApp templates approved before enabling WhatsApp notifications
- [ ] Cron jobs installed; first backup taken and restore tested
- [ ] Legal review of privacy policy, terms and listing policy
- [ ] No demo data in production (`seed_demo --clear` if ever used on staging)
