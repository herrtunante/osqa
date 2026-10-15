FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Build dependencies for mysqlclient
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# supercronic runs the scheduled commands in the "cron" service (cron/crontab):
# a static binary, pinned and checked against its SHA-256.
ARG TARGETARCH=amd64
ARG SUPERCRONIC_VERSION=v0.2.33
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sum=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671 ;; \
      arm64) sum=f1f8585c66de020fef494dd636058f99949d108f569fef00016a1c8b9eb145b3 ;; \
      *) echo "no supercronic checksum for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    python -c "import hashlib, sys, urllib.request; data = urllib.request.urlopen(sys.argv[1], timeout=60).read(); \
hashlib.sha256(data).hexdigest() == sys.argv[2] or sys.exit('supercronic checksum mismatch'); \
open('/usr/local/bin/supercronic', 'wb').write(data)" \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" "$sum"; \
    chmod 0755 /usr/local/bin/supercronic

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

# Version reported to Sentry, e.g. docker build --build-arg SENTRY_RELEASE=osqa@$(git rev-parse --short=12 HEAD)
ARG SENTRY_RELEASE=""
ENV SENTRY_RELEASE=${SENTRY_RELEASE}

# Run as an unprivileged user (uid/gid 999, as in docs/k8s/); only the runtime
# data directories are writable.
RUN useradd --system --uid 999 --user-group --home-dir /app osqa \
    && mkdir -p /app/log /app/cache /app/tmp /app/forum/upfiles /app/backups \
    && chown -R osqa:osqa /app/log /app/cache /app/tmp /app/forum/upfiles /app/backups
USER osqa

EXPOSE 8000

# /healthz answers "ok" when the app and its database are up (/readyz also
# checks the cache).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

# Settings: OSQA_SETTINGS_LOCAL=settings_local_docker reads everything from
# environment variables (docker-compose.yml, docs/k8s/); otherwise mount a
# settings_local.py at /app/settings_local.py.
CMD ["gunicorn", "wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
