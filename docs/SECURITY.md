# Security overview and checklist

## Controls implemented

| Area | Implementation |
|---|---|
| Passwords | PBKDF2 hashing, Django password validators (min length 8, common/numeric/similarity checks). Admins cannot view passwords. |
| Login throttling | 5 failed attempts per account and per IP per 15 minutes (`core/ratelimit.py`), with a shared database cache in production. |
| Password reset | Django signed one-time tokens (2-hour expiry), identical response for unknown emails, rate limited. |
| Email verification | Signed, expiring tokens (3 days). |
| Sessions & cookies | HttpOnly, SameSite=Lax, Secure in production; session invalidated on suspension. |
| CSRF | Django CSRF middleware on all forms; logout and all state changes are POST-only. The PayU return URL and webhook are the only CSRF-exempt endpoints; both require PayU's SHA-512 reply hash (made with the merchant salt) and a matching amount. |
| Authorization | Role checks in every view (`core/permissions.py`); querysets scoped to the signed-in user; admin panel requires the admin role on every request; tests cover cross-user access. |
| Input validation | Django forms/model validation server-side; Indian phone/PIN validators; price sanity limits; date rules for visits. |
| Uploads | Size limits; Pillow verification of real image content (JPEG/PNG/WEBP only), dimension limits (decompression-bomb guard); images re-encoded and resized, removing EXIF/GPS metadata; random file names; documents limited to verified PDF/JPG/PNG. |
| Private documents | Stored outside `MEDIA_ROOT`, no public URL, served via a permission-checked view with `no-store`, `nosniff` and a sandboxing CSP; admin access is audit-logged. |
| XSS | Django auto-escaping everywhere; JSON for charts/maps via `json_script`; no user HTML is rendered. |
| SQL injection | Django ORM only; no raw SQL. |
| Headers | HSTS (production), X-Frame-Options DENY, nosniff, Referrer-Policy, Cross-Origin-Opener-Policy, Permissions-Policy, `noindex` + `no-store` on private areas. |
| Rate limiting | Registration, login, password reset, enquiries, reports, contact form, uploads and payments. |
| Payments | Orders created server-side; signatures verified with the secret on the server; webhooks verified, de-duplicated and amount-checked; activation is idempotent under row locks. No card data touches the server. |
| Secrets | Only from environment variables; `.env` is git-ignored; production refuses weak/missing `SECRET_KEY`. |
| Audit log | Logins, registrations, moderation decisions, verification decisions, document views, suspensions, role changes, payments, settings changes, exports and broadcasts. |
| Open redirects | `next` parameters and notification links are validated against the site host. |
| CSV export | Formula-injection characters are neutralised. |

## Verification document retention

* Documents awaiting review are kept until a decision; the partner can withdraw them at any time.
* After **acceptance**: kept for `VERIFICATION_DOC_RETENTION_DAYS` (default 180) then the file is deleted.
* After **rejection**: kept for 30 days then deleted.
* Deletion is performed by `run_scheduled_tasks` (or `purge_verification_documents`); the database row
  remains as an audit record without the file.
* Ask partners to mask Aadhaar numbers (all but the last 4 digits).

## Deployment security checklist

- [ ] `DEBUG=False`, strong unique `SECRET_KEY`, correct `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
- [ ] HTTPS everywhere; `SECURE_SSL_REDIRECT=True`; HSTS enabled (consider subdomains/preload once stable)
- [ ] `python manage.py check --deploy` reviewed
- [ ] `python manage.py createcachetable` run (shared cache for throttling)
- [ ] `.env` permissions `600`; `private_media/` permissions `700` and not web-accessible
- [ ] Web server denies script execution in `/media/`
- [ ] Google Maps key restricted by HTTP referrer; PayU salt and WhatsApp secrets only on the server
- [ ] PayU webhook added and a test payment confirmed
- [ ] Admin accounts use strong unique passwords; remove unused admins; review audit logs regularly
- [ ] OS and Python packages updated; `pip list --outdated` checked monthly
- [ ] Backups automated, encrypted off-site and restore-tested
- [ ] Error emails / log monitoring configured
- [ ] Consider a WAF/CDN (e.g. Cloudflare) and a Content-Security-Policy header once third-party scripts are final
