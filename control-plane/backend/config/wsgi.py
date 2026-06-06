"""WSGI entrypoint (gunicorn config.wsgi:application — deployment_and_devops.md §2.3)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
