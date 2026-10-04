# Web app demo

A single-file, browser-only version of Bangarpet Property Hub built from the
real project's design system and the `[DEMO]` data. It supports search and
filters, property pages with photo tours, saved homes, enquiries and visit
requests, reports and a 7-step "list your property" flow. Everything a visitor
does is stored only in their own browser; nothing reaches a server.

The full product (accounts, moderation, admin panel, payments, notifications)
is the Django app in this repository.

## Rebuild

```bash
python manage.py seed_demo --reset                      # optional: fresh demo data
python manage.py shell < preview/webapp/export_data.py  # -> preview/webapp/data.json
python preview/webapp/build.py                          # -> preview/webapp/dist/bangarpet-web-app.html
```

`app_template.html` holds the page, styles and script. `build.py` injects the
data, the Bootstrap Icons subset (font embedded) and the logo.
