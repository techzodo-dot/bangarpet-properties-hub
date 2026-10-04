# Bangarpet Property Hub - production container
#   docker build -t bangarpet-property-hub .
#   docker run --env-file .env -p 8000:8000 bangarpet-property-hub
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 bph \
    && mkdir -p media private_media logs staticfiles \
    && chown -R bph:bph /app
USER bph

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz/', timeout=4)"

# Migrations, static files and the admin login run on start, then Gunicorn serves the app.
CMD ["sh", "-c", "bash scripts/release.sh && exec gunicorn -c gunicorn.conf.py config.wsgi:application"]
