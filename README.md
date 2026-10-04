# Bangarpet Property Hub

**Find. Rent. Buy. Manage.** — a full-stack property marketplace for Bangarpet, Kolar district, Karnataka.
Tenants and buyers search and enquire; owners and brokers list and manage properties; a super admin
moderates listings, verifies partners and runs the platform from a custom management panel.

Built with Django 5.2 (LTS), SQLite (PostgreSQL-ready), Bootstrap 5 and vanilla JavaScript. All frontend
assets (Bootstrap, icons, Chart.js, fonts) are vendored, so the site works without third-party CDNs.

---

## Quick start (development)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # defaults work for local development
python manage.py migrate             # creates tables + reference data (categories, Bangarpet locations, plans)
python manage.py seed_demo           # OPTIONAL: [DEMO] accounts (incl. an admin), listings and activity
python manage.py createsuperuser     # your own admin account (email + full name + password)
python manage.py runserver
```

Open http://127.0.0.1:8000/. The admin panel is at **/management/** (sign in with the superuser).

`seed_demo` creates four demo accounts (password `DemoPass#2024` for all):

| Account | Role | Start at |
|---|---|---|
| `admin@demo.bph.local` | super admin | `/management/` |
| `owner@demo.bph.local` | owner (Pro plan) | `/partner/dashboard/` |
| `broker@demo.bph.local` | verified broker (Broker plan) | `/partner/dashboard/` |
| `customer@demo.bph.local` | customer | `/dashboard/` |

It also creates 10 live listings and 1 waiting for approval, plus sample activity: enquiries, a requested
and a scheduled visit, saved homes, recently viewed properties, an open report and notifications.
Every demo listing has a `[DEMO]` title prefix, a banner on its page and illustrations labelled
"SAMPLE IMAGE · DEMO" - never real properties or photos.

* `python manage.py seed_demo --reset` recreates the demo data from scratch.
* `python manage.py seed_demo --clear` removes all demo accounts, listings, images and payments (real data is untouched).
* The command refuses to run when `DEBUG=False` (use `--force` only on a staging copy).

Emails print to the console in development (no SMTP needed).

## Running the tests

```bash
python manage.py test          # uses config.settings.test automatically (in-memory SQLite)
```

106 tests cover registration/login/throttling, password reset, email verification, role permissions,
cross-user access protection, the listing wizard, image upload validation (type, size, dimensions,
EXIF stripping), search filters/sorting/pagination, favourites, reports, enquiries and visit scheduling,
moderation, user suspension, subscriptions and expiry, Razorpay checkout/signature verification/webhooks
(idempotency, amount mismatch), manual payments, notifications, SEO (sitemap/robots) and the demo seeder.

## Feature overview

| Area | What works |
|---|---|
| Public site | Homepage with glass search panel, quick categories, featured & recent listings, video-tour previews, popular areas, verified brokers, how-it-works, CTA, partner sign-up, admin banners, FAQ, contact form. All listing sections come from the database (empty states when there is no data). |
| Search | `/properties/`, `/rent/`, `/buy/`, `/pg-rooms/`, `/commercial/`, `/properties/<category>/` — location, type, purpose, price, bedrooms, bathrooms, furnishing, parking, area, amenities, verified, owner/broker, availability date; sort (recommended/newest/price); grid, list and map (Google Maps) views; pagination; save search; clear filters. |
| Property page | `/property/<slug>/` — gallery with full-screen carousel, facts, amenities, description, video tour, privacy-aware location and map, owner/broker card with verified badge, call / WhatsApp / enquiry / visit request (respecting the owner's phone-visibility setting), save, share, report, similar listings, JSON-LD, OpenGraph, canonical. |
| Customer dashboard | `/dashboard/` — overview, profile, enquiries with status history, visits (cancel pending), saved properties, saved searches (daily alerts), recently viewed, notification settings, password change. |
| Owner/broker dashboard | `/partner/` — stats overview, listings table, 7-step add/edit wizard (basics, location, details, pricing, photos, contact, review & submit), pause/resume, mark rented/sold, renew, duplicate, delete with typed confirmation, per-listing performance (daily views chart on Pro/Broker), enquiry inbox with status updates and private notes, CSV export (Broker plan), visit scheduling, subscription and payment history with receipts, profile and verification document upload. |
| Management panel | `/management/` — live metrics and charts, approval queue, listing review with photos/edit-history/reports, approve/reject/request changes/remove/flag/feature, users (search, suspend/reactivate, role change, verification decisions, audit history), verification queue, enquiries overview, plans (create/edit/discounts), subscriptions (grant manually, renewals due), payments (manual verification), reports, banners and sponsored placements, locations/categories/amenities, platform settings with integration status, contact messages, broadcast notices, audit logs. |
| Payments | Razorpay orders created server-side, signatures verified server-side, signed & idempotent webhooks, amount checks, receipts/invoices, optional manual UPI/bank payments verified by an admin. A plan is never activated because the browser says so. |
| Notifications | In-app notifications always; email and WhatsApp Business (Cloud API templates) according to user preferences. Every delivery attempt is logged as `sent`, `failed`, `skipped` or `not_configured` — nothing is reported as delivered unless the provider accepted it. |

## Project structure

```
config/            settings (base / development / production / test), urls, wsgi
core/              platform settings, banners, ads, audit log, contact, permissions, rate limiting,
                   validators, image processing, SEO (sitemap/robots), management commands
accounts/          custom email-based User with roles, profiles, owner/broker profiles,
                   private verification documents, auth views (login throttling, reset, verification)
properties/        locations, categories, amenities, properties, images, favourites, saved searches,
                   revisions, daily stats; search service; public views
enquiries/         enquiries, status history, property visits and workflow services
subscriptions/     plans, subscriptions and entitlement rules (listing limits, durations, priority)
payments/          payments, invoices, webhook log; Razorpay client and payment state machine
notifications/     in-app notifications, delivery log, email/WhatsApp dispatch
moderation/        reports and listing review decisions; moderation & verification services
dashboard/         customer (/dashboard/) and owner/broker (/partner/) views
adminpanel/        custom management panel (/management/)
templates/ static/ design system (static/css/main.css), JS, vendored libraries, logo
deploy/ scripts/   systemd, Nginx, cron examples; backup/restore scripts
docs/              DEPLOYMENT.md, SECURITY.md
```

## Roles and access control

* **Guest** — browse, search, view listings and contact options.
* **Customer** (`customer`) — profile, favourites, saved searches, enquiries, visits, reports.
* **Owner / Broker** (`owner`, `broker`) — everything a customer can do, plus the partner dashboard.
* **Super admin** (`admin` role or Django superuser) — the management panel.

Access is enforced on the server in every view (`core/permissions.py`); partner and customer queries are
always scoped to `request.user`, so other users' listings, enquiries and documents return 404/403.
Django's default admin is not installed.

## Activating integrations

All integrations are optional. Until configured, the related features show a clear message and the
management panel lists them as **Not configured**.

| Integration | Environment variables | Notes |
|---|---|---|
| Google Maps | `GOOGLE_MAPS_API_KEY` | Enable "Maps JavaScript API"; restrict the key to your domain (HTTP referrers). Without it, "Open in Google Maps" links still work. |
| Razorpay | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Start with `rzp_test_` keys (test mode banner is shown). Switch to live keys after KYC. |
| Razorpay webhooks | `RAZORPAY_WEBHOOK_SECRET` | Dashboard → Webhooks → URL `https://<domain>/payments/razorpay/webhook/`, events `payment.captured`, `order.paid`, `payment.failed`. |
| Email | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Any SMTP provider. Also needed for password reset in production. |
| WhatsApp | `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Meta WhatsApp Cloud API. Create and get approved the templates listed in `notifications/services.py` (`bph_new_enquiry`, `bph_visit_request`, …), each with one body variable `{{1}}`. Then enable WhatsApp in Management → Settings. |

## Scheduled tasks

Run hourly (see `deploy/crontab.example`):

```bash
python manage.py run_scheduled_tasks
```

It expires listings and subscriptions, sends expiry reminders, expires partner verifications, sends
saved-search alerts and purges verification documents past their retention date. It is idempotent.

## Development phases

1. **Setup, auth, roles, models** — settings split, custom User, profiles, all models and migrations,
   reference data migration, permission mixins.
2. **Public site** — homepage, search, details, listing submission wizard.
3. **Dashboards** — customer, owner and broker dashboards; enquiries; visit scheduling.
4. **Management panel** — moderation, verification, banners/ads, configuration, audit logs.
5. **Subscriptions, payments, notifications** — plans, Razorpay, manual payments, email/WhatsApp.
6. **SEO, security, tests, performance, deployment** — sitemap/robots/JSON-LD, security headers,
   rate limiting, 106 automated tests, query optimisation and caching, production settings and docs.

## Before launch

Read **docs/DEPLOYMENT.md** and **docs/SECURITY.md**. Have the privacy policy, terms and listing policy
reviewed by a lawyer, verify the seeded locality list with local knowledge (Management → Locations), set
real contact details in Management → Settings, and test payments end-to-end in Razorpay test mode.
