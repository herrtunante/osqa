FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build dependencies for mysqlclient
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# Version reported to Sentry, e.g. docker build --build-arg SENTRY_RELEASE=osqa@$(git rev-parse --short=12 HEAD)
ARG SENTRY_RELEASE=""
ENV SENTRY_RELEASE=${SENTRY_RELEASE}

# Run as an unprivileged user; only the runtime data directories are writable.
RUN useradd --system --home-dir /app osqa \
    && mkdir -p /app/log /app/cache /app/tmp /app/forum/upfiles \
    && chown -R osqa:osqa /app/log /app/cache /app/tmp /app/forum/upfiles
USER osqa

EXPOSE 8000

# /healthz answers "ok" when the app and its database are up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

# settings_local.py (database, APP_URL, SECRET_KEY or OSQA_SECRET_KEY) is
# mounted at runtime; see docker-compose.yml.
CMD ["gunicorn", "wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
