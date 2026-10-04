# Deployment guide

Bangarpet Property Hub is a standard Django app, so it runs on most hosts. Pick one option below:

| Option | Good for | Database | Effort |
|---|---|---|---|
| **A. Linux VPS** (Serverbyt, DigitalOcean, Hostinger VPS, AWS Lightsail…) | Full control, lowest monthly cost | SQLite to start, PostgreSQL later | Medium |
| **B. cPanel "Setup Python App"** (shared hosting, e.g. Serverbyt cPanel) | You already have cPanel hosting | SQLite | Low |
| **C. Render** (`render.yaml` included) | Push-to-deploy, managed database | PostgreSQL (managed) | Low |
| **D. Railway** (`Procfile` included) | Push-to-deploy, simple pricing | PostgreSQL (plugin) | Low |
| **E. Docker** (`Dockerfile`, `docker-compose.yml`) | Any server with Docker | PostgreSQL container | Low–medium |

Whatever you choose, the steps are the same: **set environment variables → run `scripts/release.sh` →
start Gunicorn → add HTTPS → add the hourly cron job.**

## 1. Environment variables

Set these in `.env` (VPS, cPanel, Docker) or in the host's dashboard (Render, Railway):

```
DJANGO_SETTINGS_MODULE=config.settings.production
SECRET_KEY=<50+ random characters>
DEBUG=False
ALLOWED_HOSTS=example.com,www.example.com
CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
SITE_URL=https://example.com

ADMIN_USERNAME=bph@admin
ADMIN_PASSWORD=<the admin password>
```

* Generate a key: `python -c "from django.core.management.utils import get_random_secret_key as g; print(g()+g())"`.
  Production settings refuse to start without a strong `SECRET_KEY`.
* `ADMIN_USERNAME` / `ADMIN_PASSWORD` create the super admin on the first deploy. Later deploys keep the
  password you change in **Management → Change password** (the release script uses `--keep-password`).
  To reset a forgotten password, run `python manage.py ensure_admin` once with the new `ADMIN_PASSWORD`.
* On Render and Railway the platform hostname is added to `ALLOWED_HOSTS` automatically; add your own
  domain once it is connected.
* Optional integrations (email, Razorpay, WhatsApp, Google Maps) are listed in `.env.example` and the README.
* `chmod 600 .env` and never commit it.

## 2. The release script

`scripts/release.sh` runs on every deploy (all options below call it):

1. `migrate` – creates/updates database tables and the reference data (categories, Bangarpet localities, plans)
2. `createcachetable` – shared cache used for login throttling and rate limits
3. `collectstatic` – fingerprinted, compressed CSS/JS/fonts served by WhiteNoise
4. `ensure_admin --keep-password --skip-if-missing` – the admin login from `ADMIN_USERNAME` / `ADMIN_PASSWORD`
5. `check --deploy` – Django's production security checks (the HSTS *subdomains/preload* warnings are opt-in)

The site starts with **no listings and no users** apart from the admin. Owners and brokers register,
add listings, and you approve them in `/management/`.

## 3. Option A — Linux VPS (Ubuntu) with Gunicorn + Nginx

```bash
sudo apt install python3-venv python3-dev nginx sqlite3 certbot python3-certbot-nginx git
sudo adduser --system --group --home /srv/bph bph
sudo -u bph git clone <repo-url> /srv/bph/app
cd /srv/bph/app
sudo -u bph python3 -m venv /srv/bph/venv
sudo -u bph /srv/bph/venv/bin/pip install -r requirements.txt
sudo -u bph cp .env.example .env && sudo -u bph nano .env          # fill in section 1
sudo -u bph PYTHON=/srv/bph/venv/bin/python bash scripts/release.sh
```

* Gunicorn service: `sudo cp deploy/bph.service /etc/systemd/system/ && sudo systemctl enable --now bph`
* Nginx: copy `deploy/nginx.conf.example` to `/etc/nginx/sites-available/bph`, replace `example.com`,
  link it into `sites-enabled`, then `sudo nginx -t && sudo systemctl reload nginx`.
* DNS: point `A` records for `example.com` and `www` at the server IP.
* HTTPS: `sudo certbot --nginx -d example.com -d www.example.com` (renews automatically).
* Cron: install `deploy/crontab.example` for the `bph` user (`crontab -u bph -e`).
* Permissions: `media/` writable by the app user; `private_media/` mode `700` and **never** exposed by Nginx.

**Updating:**

```bash
cd /srv/bph/app && sudo -u bph git pull
sudo -u bph /srv/bph/venv/bin/pip install -r requirements.txt
sudo -u bph PYTHON=/srv/bph/venv/bin/python bash scripts/release.sh
sudo systemctl restart bph
```

## 4. Option B — cPanel "Setup Python App" (shared hosting)

1. cPanel → **Setup Python App** → Create application: Python 3.11+, application root e.g. `bph`,
   application URL your domain, startup file `passenger_wsgi.py`, entry point `application`.
2. Upload the code into the application root (cPanel **Git Version Control** or File Manager).
3. Create `.env` in the application root (section 1) and add `SERVE_MEDIA=True` (shared hosting cannot
   alias `/media/`; Django then serves public photos, never the private verification documents).
4. Open the terminal command cPanel shows to enter the virtual environment, then:
   `pip install -r requirements.txt && bash scripts/release.sh`
5. SSL: cPanel → **SSL/TLS Status** → run AutoSSL for the domain.
6. Cron: cPanel → **Cron Jobs** → hourly:
   `cd ~/bph && ~/virtualenv/bph/3.11/bin/python manage.py run_scheduled_tasks`
7. Click **Restart** on the Python App page after every change.

## 5. Option C — Render

1. Push the repository to GitHub.
2. Render dashboard → **New → Blueprint** → select the repository. Render reads `render.yaml` and creates
   the web service, a PostgreSQL database and a 2 GB disk for uploaded photos.
3. When asked, enter `SITE_URL` (e.g. `https://bangarpet-property-hub.onrender.com`, later your domain),
   `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` for your own domain (can be left empty at first) and
   `ADMIN_PASSWORD`. `SECRET_KEY` is generated for you.
4. Deploys run `scripts/release.sh` and then Gunicorn; health checks use `/healthz/`.
5. Custom domain: Settings → Custom Domains, then add it to `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `SITE_URL`.
6. Cron: add a Render **Cron Job** running `python manage.py run_scheduled_tasks` hourly with the same
   environment variables.

The persistent disk needs a paid instance type. Without it, uploaded photos disappear on every deploy.

## 6. Option D — Railway

1. New Project → **Deploy from GitHub repo**; add the **PostgreSQL** plugin (it provides `DATABASE_URL`).
2. Variables: everything in section 1 plus `FORWARDED_ALLOW_IPS=*` and `SERVE_MEDIA=True`.
3. Add a **Volume** mounted at `/data` and set `MEDIA_ROOT=/data/media`, `PRIVATE_MEDIA_ROOT=/data/private_media`.
4. The `Procfile` runs `scripts/release.sh` and then Gunicorn on Railway's `$PORT`.
5. Settings → Networking → generate a domain (added to `ALLOWED_HOSTS` automatically) or add your own.
6. Cron: add a second service with the same variables and start command
   `python manage.py run_scheduled_tasks`, scheduled hourly.

## 7. Option E — Docker / Docker Compose

```bash
cp .env.example .env        # fill in section 1 and POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f web
```

`docker-compose.yml` starts PostgreSQL and the app (release script + Gunicorn on port 8000) with
volumes for the database, photos, private documents and logs. Put a TLS proxy in front of port 8000
(Caddy, Nginx, Traefik or your host's load balancer) and point your domain at it.
Cron: `docker compose exec web python manage.py run_scheduled_tasks` hourly from the host's crontab.

## 8. Installable web app (PWA)

Nothing extra to configure: once the site is on **HTTPS**, Chrome, Edge and Android offer
*Install app*, and iPhone users can use *Share → Add to Home Screen*. The app name, icons and colours
come from `/manifest.webmanifest`; `/sw.js` caches static files and shows `/offline/` when there is no
connection. Pages and dashboards are never cached on the device. After each deploy, returning visitors
pick up the new version automatically.

## 9. Static and media files

* Static files: `collectstatic` → `STATIC_ROOT` (default `staticfiles/`), served by WhiteNoise with
  compressed, fingerprinted filenames (or directly by Nginx).
* Public media (`MEDIA_ROOT`, default `media/`): property photos (re-encoded, EXIF stripped), banners, avatars.
  Served by Nginx (`/media/` alias) or by Django when `SERVE_MEDIA=True`.
* Private documents (`PRIVATE_MEDIA_ROOT`, default `private_media/`): served only through
  `/accounts/documents/<id>/` after a permission check.
* On platforms with temporary file systems (Render, Railway, Docker without volumes) media **must** live
  on a persistent disk/volume, or move public media to S3-compatible storage with `django-storages`.

## 10. SQLite vs PostgreSQL

SQLite is fine for a small launch **on a VPS or cPanel with persistent disk** (one write at a time,
keep Gunicorn workers ≤ 3, back up with `scripts/backup.sh`). Use PostgreSQL on Render, Railway and Docker,
and when traffic grows. Moving from SQLite:

```bash
python manage.py dumpdata --natural-foreign --natural-primary \
    --exclude contenttypes --exclude auth.permission --exclude sessions --indent 2 > data.json
# set DATABASE_URL=postgres://user:pass@host:5432/bph
python manage.py migrate
python manage.py loaddata data.json
```

The PostgreSQL driver (`psycopg`) is already in `requirements.txt`.

## 11. Backups, logging and monitoring

* `scripts/backup.sh [dir]` – consistent SQLite (or `pg_dump`) backup plus media archives; keeps the last 14.
  Schedule nightly and copy backups off-server (they contain personal data – store them encrypted).
  `scripts/restore.sh backups/<timestamp>` restores; test it once before launch.
* Logs go to stdout and `logs/bph.log` (rotated). Set `ADMIN_ERROR_EMAILS=you@example.com` to get
  emails about server errors (needs SMTP).
* Uptime monitoring: point any monitor (UptimeRobot, Better Stack…) at `https://<domain>/healthz/`.

## 12. Go-live checklist

- [ ] Section 1 variables set, `DEBUG=False`, `scripts/release.sh` finished without errors
- [ ] HTTPS working and HTTP redirects to HTTPS
- [ ] Signed in as the admin and **changed the admin password** (Management → Change password)
- [ ] Management → Settings filled in (contact phone/email, WhatsApp, office address, social links)
- [ ] Locations, categories and plan prices reviewed
- [ ] Email working (try password reset); Razorpay test payment + webhook verified, then live keys
- [ ] WhatsApp templates approved before enabling WhatsApp notifications
- [ ] Hourly cron (`run_scheduled_tasks`) installed; first backup taken and a restore tested
- [ ] "Install app" works on a phone (Chrome → menu → Install app)
- [ ] Legal review of privacy policy, terms and listing policy
