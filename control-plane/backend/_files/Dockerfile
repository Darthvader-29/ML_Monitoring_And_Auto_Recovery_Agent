# Django control plane (deployment_and_devops.md §2.3). venvc.
# Build context: control-plane/backend/  (dockerfile: _files/Dockerfile).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY _files/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# manage.py, config/, and the apps (see .dockerignore for what is excluded)
COPY . .

EXPOSE 8000

# migrate + seed run as the compose command at boot (deployment_and_devops.md §7);
# the image CMD is the long-running production server.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "60"]
