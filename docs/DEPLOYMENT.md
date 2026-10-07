# Deployment guide

Bangarpet Property Hub is a standard Django app, so it runs on most hosts. Pick one option below:

| Option | Good for | Database | Effort |
|---|---|---|---|
| **A. Linux VPS** (Serverbyt, DigitalOcean, Hostinger VPS, AWS Lightsail…) | Full control, lowest monthly cost | SQLite to start, PostgreSQL later | Medium |
| **B. cPanel "Setup Python App"** (shared hosting, e.g. Serverbyt cPanel) | You already have cPanel hosting | SQLite | Low |
| **C. Render** (`render.yaml` included) | Push-to-deploy, managed database | PostgreSQL (managed) | Low |
| **D. Railway** (`Procfile` included) | Push-to-deploy, simple pricing | PostgreSQL (plugin) | Low |
| **E. Docker** (`Dockerfile`, `docker-compose.yml`) | Any server with Docker | PostgreSQL container | Low–medium |
| **F. Vercel** (`vercel.json` included) | Serverless, free Hobby plan for testing | Supabase or Neon PostgreSQL | Low |

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
* Optional integrations (email, PayU, WhatsApp, Google Maps) are listed in `.env.example` and the README.
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

## 8. Option F — Vercel (serverless) + PostgreSQL + Vercel Blob

Vercel runs Django as a serverless function. There is no persistent disk, so the app switches
automatically (when the `VERCEL` variable is present) to:

* **PostgreSQL** from Supabase or Neon for the database (`DATABASE_URL`, required),
* **Vercel Blob** for public photos, avatars and banners (`BLOB_READ_WRITE_TOKEN`),
* **the database** for private verification documents (never at a public URL),
* WhiteNoise serving `static/` directly, with a per-deployment `?v=` cache buster,
* logs on stdout only (Vercel → Project → Logs).

Setup:

1. Vercel → **Add New → Project** → import the GitHub repository. `vercel.json` sets the framework
   (Django), the build command (`scripts/vercel_build.sh`), the region (Mumbai, `bom1`) and the cron job.
2. Database, either:
   * **Supabase** (used by the live site): create a project in the Mumbai region (`ap-south-1`). In the
     SQL editor, create a login and schema for the app:
     `CREATE ROLE bph_app LOGIN PASSWORD '<strong password>'; GRANT bph_app TO postgres;`
     `CREATE SCHEMA bph AUTHORIZATION bph_app; ALTER ROLE bph_app SET search_path = bph;`
     Then set `DATABASE_URL=postgres://bph_app.<project-ref>:<password>@<pooler-host>:6543/postgres?sslmode=require`
     using the **transaction pooler** host from Supabase → Connect (for this project `aws-0-ap-south-1.pooler.supabase.com`).
     Vercel cannot reach Supabase's direct `db.<ref>.supabase.co` address (IPv6 only).
   * **Neon**: Project → **Storage** → **Create Database → Neon** → connect it (adds `DATABASE_URL`).

   The app turns off prepared statements and server-side cursors on Vercel, as transaction poolers require.
3. Project → **Storage** → **Create → Blob** (public access) → connect it (adds `BLOB_READ_WRITE_TOKEN`).
4. Project → **Settings → Environment Variables**:
   `DJANGO_SETTINGS_MODULE=config.settings.production`, `SECRET_KEY`, `ADMIN_USERNAME=bph@admin`,
   `ADMIN_PASSWORD` (mark it *Sensitive*) and `CRON_SECRET` (any long random string).
   The `*.vercel.app` hostnames are added to `ALLOWED_HOSTS` automatically and `SITE_URL` defaults to
   the production URL; add your own domain to `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `SITE_URL`.
5. Deploy. Each build runs migrations, creates the cache table and the admin login.

Limits to know about:

* **Hobby (free) plan is for non-commercial use.** Move to Pro before a public commercial launch.
* Requests are limited to **4.5 MB**. Photos are resized in the browser (max 1920 px) and sent in
  batches automatically; verification documents are limited to 4 MB.
* Hobby cron jobs run **once a day** (`/cron/scheduled-tasks/` at 06:00 IST). Listing expiry and
  alerts therefore run daily instead of hourly.
* Backups: Supabase/Neon take daily database backups (Supabase free plan: download them from the
  dashboard, or run `pg_dump` against the session pooler on port 5432); photos stay in the Blob store.
* The first deploy is slow (a few minutes) because migrations run from Vercel's build machine;
  later deploys only check for new migrations.

## 9. Installable web app (PWA)

Nothing extra to configure: once the site is on **HTTPS**, Chrome, Edge and Android offer
*Install app*, and iPhone users can use *Share → Add to Home Screen*. The app name, icons and colours
come from `/manifest.webmanifest`; `/sw.js` caches static files and shows `/offline/` when there is no
connection. Pages and dashboards are never cached on the device. After each deploy, returning visitors
pick up the new version automatically.

## 10. Static and media files

* Static files: `collectstatic` → `STATIC_ROOT` (default `staticfiles/`), served by WhiteNoise with
  compressed, fingerprinted filenames (or directly by Nginx).
* Public media (`MEDIA_ROOT`, default `media/`): property photos (re-encoded, EXIF stripped), banners, avatars.
  Served by Nginx (`/media/` alias) or by Django when `SERVE_MEDIA=True`.
* Private documents (`PRIVATE_MEDIA_ROOT`, default `private_media/`): served only through
  `/accounts/documents/<id>/` after a permission check.
* On platforms with temporary file systems (Render, Railway, Docker without volumes) media **must** live
  on a persistent disk/volume, or move public media to S3-compatible storage with `django-storages`.

## 11. SQLite vs PostgreSQL

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

## 12. Backups, logging and monitoring

* `scripts/backup.sh [dir]` – consistent SQLite (or `pg_dump`) backup plus media archives; keeps the last 14.
  Schedule nightly and copy backups off-server (they contain personal data – store them encrypted).
  `scripts/restore.sh backups/<timestamp>` restores; test it once before launch.
* Logs go to stdout and `logs/bph.log` (rotated). Set `ADMIN_ERROR_EMAILS=you@example.com` to get
  emails about server errors (needs SMTP).
* Uptime monitoring: point any monitor (UptimeRobot, Better Stack…) at `https://<domain>/healthz/`.

## 12a. Email codes (OTP) with Gmail

The site emails 6-digit one-time codes for: verifying the email address at sign-up, signing in
without a password, resetting a forgotten password, and the admin 2-step sign-in. Codes are valid
for 10 minutes, work once, allow 5 wrong tries and are rate limited. They switch on automatically
once SMTP is configured; until then sign-up and sign-in work without them.

1. On the Gmail account, turn on 2-Step Verification, then create an app password at
   https://myaccount.google.com/apppasswords (16 letters).
2. Set these environment variables (on Vercel: Project → Settings → Environment Variables, mark the
   password as Sensitive) and redeploy:

   | Variable | Value |
   | --- | --- |
   | `EMAIL_HOST` | `smtp.gmail.com` |
   | `EMAIL_PORT` | `587` |
   | `EMAIL_USE_TLS` | `True` |
   | `EMAIL_HOST_USER` | the Gmail address |
   | `EMAIL_HOST_PASSWORD` | the app password |
   | `DEFAULT_FROM_EMAIL` | `Bangarpet Property Hub <the Gmail address>` |
   | `ADMIN_OTP_EMAIL` | inbox for the admin's 2-step codes (needed when the admin login is a username such as `bph@admin`) |

Without `ADMIN_OTP_EMAIL`, a username-style admin login skips the second step instead of being
locked out. Set `EMAIL_OTP_ENABLED=False` to turn all codes off (for example if Gmail stops sending).
Gmail allows about 500 emails a day; use a transactional email service if you need more.

## 12b. Payments with PayU, and the customer Contact Pass

Online payments use PayU's hosted checkout: the customer pays on PayU's page (UPI, cards, net
banking, wallets) and is sent back to the site. A plan is activated only after PayU's reply hash
(signed with your merchant salt) and the amount have been checked on the server.

1. PayU dashboard → Developers → API keys: copy the **Merchant key** and **Salt (v1)**. Test mode and
   live mode have different keys.
2. Set `PAYU_MERCHANT_KEY`, `PAYU_MERCHANT_SALT` (mark it Sensitive on Vercel) and `PAYU_MODE`
   (`test` or `live`), then redeploy. The site shows a "test mode" note on the payment page in test mode.
3. Recommended: PayU dashboard → Webhooks → add `https://<domain>/payments/payu/webhook/` for successful
   and failed payments. Without it, the daily scheduled task asks PayU about unfinished payments.
4. Allow your domain in PayU if asked (the return URL is `https://<domain>/payments/payu/return/`).

**Contact Pass.** Customers can unlock a limited number of owner phone/WhatsApp contacts for free
each month (Management → Settings → "Limit free owner contacts" and "Free contacts per month", default 5).
After that they buy the **Contact Pass** (a customer plan with "Unlimited contacts", default ₹99 for
30 days, editable under Management → Plans). It is a one-time payment per period; customers are
reminded before it ends and buying again extends it. (Automatic monthly debits would need PayU's
recurring-payments product enabled on the account.)

## 12c. Direct UPI payments (no gateway)

UPI / bank transfer payments are on by default and work with or without PayU.

1. Management -> Settings -> Subscriptions & billing: enter your **UPI ID** (e.g. `name@okhdfcbank`)
   and **UPI payee name**, then Save. Optionally add bank details in "Manual payment instructions".
2. Owners, brokers (plans) and customers (Contact Pass) see "Pay directly by UPI": a QR code, the UPI ID
   with a copy button, and an "Open UPI app" button on phones. Amount and a note like `BPH basic U12`
   are pre-filled.
3. After paying, they enter the 12-digit UPI reference (UTR). Admins get a notification.
4. Check the reference in your bank / UPI app, then Management -> Payments -> Approve. That activates
   the plan or Contact Pass and sends the receipt. Reject if it can't be found.

Until a UPI ID is set, the page asks people to WhatsApp you for it. Untick "Allow UPI / bank transfer
payments" to switch it off.

## 13. Go-live checklist

- [ ] Section 1 variables set, `DEBUG=False`, `scripts/release.sh` finished without errors
- [ ] HTTPS working and HTTP redirects to HTTPS
- [ ] Signed in as the admin and **changed the admin password** (Management → Change password)
- [ ] Management → Settings filled in (contact phone/email, WhatsApp, office address, social links)
- [ ] Locations, categories and plan prices reviewed
- [ ] Email working (sign in with an email code, try a password reset); PayU test payment + webhook verified, then live keys and PAYU_MODE=live
- [ ] WhatsApp templates approved before enabling WhatsApp notifications
- [ ] Hourly cron (`run_scheduled_tasks`) installed; first backup taken and a restore tested
- [ ] "Install app" works on a phone (Chrome → menu → Install app)
- [ ] Legal review of privacy policy, terms and listing policy
