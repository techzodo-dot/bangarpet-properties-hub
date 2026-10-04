"""Gunicorn configuration: gunicorn -c gunicorn.conf.py config.wsgi:application"""
import multiprocessing
import os

# Platforms such as Render, Railway and Docker hosts pass the port in $PORT.
bind = os.environ.get("GUNICORN_BIND") or (f"0.0.0.0:{os.environ['PORT']}" if os.environ.get("PORT") else "127.0.0.1:8000")
# SQLite handles few concurrent writers; keep workers modest until you move to PostgreSQL.
workers = int(os.environ.get("GUNICORN_WORKERS", min(3, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
timeout = 60
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
