"""Entry point for cPanel "Setup Python App" (Phusion Passenger) hosting.

Point the application startup file to passenger_wsgi.py and the entry point to
`application`. Environment variables are read from .env in the project root.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

from config.wsgi import application  # noqa: E402,F401
