# Setting up a new OSQA forum

This guide covers setting up a new, empty forum on a Linux server, with Docker
Compose, or on another container platform. To move an existing forum from the
original OSQA (Python 2 / Django 1.6), set the new site up with this guide and
then follow [upgrading-from-original-osqa.md](upgrading-from-original-osqa.md).

## What a forum needs

| Part | What to use | Notes |
|---|---|---|
| Python | 3.12 or newer | The Docker image uses 3.13 |
| Database | MySQL 8 / MariaDB 10.6+ (recommended), PostgreSQL, or SQLite for small or test sites | Create it with `utf8mb4` so every language and emoji can be stored |
| Cache | memcached (recommended), or the file cache | Must be **shared by all worker processes**: posts and users are served from it. Never use the per-process local-memory cache |
| Web server | gunicorn behind nginx, Apache, Caddy or a load balancer that terminates HTTPS | The app speaks plain HTTP on a local port |
| Disk | `forum/upfiles/` (uploaded images, logo), `log/`, `cache/` (with the file cache) | Uploads must persist and be shared if you run several app servers |
| Email | An SMTP server | Entered in the admin (Admin > Email settings), not in files |
| Scheduled jobs | cron, systemd timers or the platform's scheduler | Digests and dormant-account clean-up daily; AI jobs if used |
| Optional | Sentry, Google Gemini (AI features), OAuth apps (Google/GitHub/Facebook sign-in) | |

Everything that changes per site (name, logo, colours, texts, email server,
reputation rules, limits) lives in the database and is edited in the admin at
`/admin/`. Only the database connection, `APP_URL`, `SECRET_KEY` and a few
server settings go in `settings_local.py` or environment variables.

## Settings you will set

Copy `settings_local.py.dist` to `settings_local.py`. The file is never
committed and is mounted into the container in Docker setups. Set:

| Setting | Example | Why |
|---|---|---|
| `DATABASES` | see below | Database connection |
| `APP_URL` | `'https://forum.example.org'` | The public address, without a trailing slash. With `https://`, secure cookies and HSTS are switched on |
| `ALLOWED_HOSTS` | `('forum.example.org',)` | Host names the site answers to |
| `SECRET_KEY` (or env `OSQA_SECRET_KEY`) | a 50-character random string | Signs sessions and links. Generate with `python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())'`. Changing it signs everybody out |
| `TRUSTED_PROXY_COUNT` (or env `OSQA_TRUSTED_PROXIES`) | `1` | Number of reverse proxies in front. The visitor's IP and HTTPS are then read from `X-Forwarded-For` / `X-Forwarded-Proto`, and HTTP is redirected to HTTPS. Leave `0` if visitors reach gunicorn directly |
| `CACHES` | memcached, see the `.dist` file | Shared cache |
| `TIME_ZONE` | `'Europe/Rome'` | Dates are stored in this zone. **When moving an existing forum, keep the old value**, or every date shifts |
| `LANGUAGE_CODE` | `'en'` | Interface language |
| `SENTRY_DSN` (optional) | `'https://<key>@o<org>.ingest.sentry.io/<project>'` | Server and browser error reports (see the README) |
| `DISABLED_MODULES` | `['recaptcha', 'project_badges']` | Optional modules to switch off |

MySQL / MariaDB connection:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'osqa',
        'USER': 'osqa',
        'PASSWORD': '...',
        'HOST': '127.0.0.1',
        'PORT': '3306',
        'CONN_MAX_AGE': 600,
        'OPTIONS': {'charset': 'utf8mb4'},
    }
}
```

Create the database first:

```sql
CREATE DATABASE osqa CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'osqa'@'%' IDENTIFIED BY '...';
GRANT ALL PRIVILEGES ON osqa.* TO 'osqa'@'%';
```

---

## Option A: a Linux server (systemd + nginx)

Commands for Ubuntu 24.04 or Debian 13, whose `python3` is 3.12 or newer.
Debian 12 ships 3.11: install a newer Python (e.g. with `uv` or `pyenv`) or use
Docker. Other distributions need the equivalent packages.

### 1. Packages and user

```bash
sudo apt-get install python3 python3-venv python3-dev build-essential \
    default-libmysqlclient-dev pkg-config nginx memcached git
sudo useradd --system --create-home --home-dir /srv/osqa osqa
sudo -u osqa git clone https://github.com/openforis/osqa.git /srv/osqa/app
```

### 2. Python environment and settings

```bash
cd /srv/osqa/app
sudo -u osqa python3 -m venv /srv/osqa/venv
sudo -u osqa /srv/osqa/venv/bin/pip install -r requirements.txt
sudo -u osqa cp settings_local.py.dist settings_local.py
sudo -u osqa chmod 600 settings_local.py
sudo -u osqa editor settings_local.py        # DATABASES, APP_URL, ALLOWED_HOSTS, SECRET_KEY, TRUSTED_PROXY_COUNT = 1, CACHES
sudo -u osqa mkdir -p log cache tmp forum/upfiles
sudo -u osqa /srv/osqa/venv/bin/python manage.py migrate
```

### 3. gunicorn as a service

`/etc/systemd/system/osqa.service`:

```ini
[Unit]
Description=OSQA forum
After=network.target mysql.service memcached.service

[Service]
User=osqa
Group=osqa
WorkingDirectory=/srv/osqa/app
Environment=SENTRY_ENVIRONMENT=production
ExecStart=/srv/osqa/venv/bin/gunicorn wsgi:application --bind 127.0.0.1:8000 --workers 3 --access-logfile -
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now osqa
curl http://127.0.0.1:8000/healthz      # -> ok
```

A rule of thumb for `--workers` is 2 × CPU cores + 1. The access log goes to
the journal (`journalctl -u osqa`); application and security logs go to
`log/` (rotated at 10 MB).

### 4. nginx with HTTPS

`/etc/nginx/sites-available/osqa` (get a certificate with certbot, e.g.
`sudo certbot --nginx -d forum.example.org`):

```nginx
# Skin media URLs carry a version (?v=<hash of the file>): those never change.
map $arg_v $osqa_media_cache {
    ""      "public, max-age=3600";
    default "public, max-age=31536000, immutable";
}

server {
    listen 80;
    server_name forum.example.org;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name forum.example.org;
    # ssl_certificate / ssl_certificate_key: added by certbot

    client_max_body_size 5m;          # uploads are limited to 2.5 MB by default

    # Optional: serve uploads and the default skin's files without Python
    # (caching rules: see "Caching and compression" below).
    location /upfiles/ {
        alias /srv/osqa/app/forum/upfiles/;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }
    location /m/default/media/ {
        alias /srv/osqa/app/forum/skins/default/media/;
        add_header Cache-Control $osqa_media_cache;
        gzip on;
        gzip_types text/css application/javascript text/javascript image/svg+xml application/json;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }
}
```

`TRUSTED_PROXY_COUNT = 1` must match: one proxy (nginx) appends the visitor's
address to `X-Forwarded-For`. With a load balancer in front of nginx, use `2`.

#### Caching and compression

What OSQA sends by itself (when Django serves the files, e.g. without the
optional `alias` locations, behind a CDN, or on a container platform):

| Request | `Cache-Control` | Validation |
|---|---|---|
| Skin media with the current version, `/m/<skin>/media/...?v=<hash>` (every `{% media %}` URL) | `public, max-age=31536000, immutable` | ETag (the hash), Last-Modified |
| Skin media without a version (images referenced from CSS) and Django's admin media (`/m/django_admin/`) | `public, max-age=3600` | ETag / Last-Modified, `304 Not Modified` |
| Skin media with an old version (a page saved before a deploy) | `no-cache` (content of the current file) | ETag |
| Uploads, `/upfiles/...` (a new upload always gets a new name) | `public, max-age=31536000, immutable` | ETag |
| The default logo shipped in `forum/upfiles` | `public, max-age=3600` | ETag |
| Question pages for anonymous visitors without a pending message (once their view is counted), and `/feeds/rss/` | `private, no-cache`, `Vary: Cookie, Accept-Language` | ETag, `304 Not Modified` while nothing on the site changed (at most an hour) |
| Other pages | none | none |

The version is a hash of the file's content, computed once per process, so
a deploy that changes a file changes its URL and nothing stale is served; the
service worker (`/sw.js`, never cached for long) names its cache after the
same hashes. Requests for files skip the session and user middleware: they
set no cookie and don't vary by cookie, so a CDN or proxy cache may keep them.

Compression: OSQA gzips CSS, JavaScript, SVG and JSON files it serves (when
the browser accepts gzip) and never HTML pages, which carry the CSRF token
(BREACH). gunicorn compresses nothing. nginx compresses only `text/html` by
default (`gzip on;` in the stock `nginx.conf`), proxied pages included: Django
masks the CSRF token differently in every response, its defence against
BREACH, but to keep pages uncompressed add `gzip off;` to `location /`. For
the files nginx serves itself, the `gzip_types` line above compresses the
text ones (`gzip_static on;` would use `.gz` files placed next to them).

If several app servers are updated one at a time, leave `/m/` to OSQA (drop
the `alias` for it): nginx can't tell whether `?v=` matches the file on its
own disk, while OSQA only marks the current version immutable.

#### Security headers (Content-Security-Policy)

OSQA sends its own `Content-Security-Policy` on every page. Inline scripts
run only with the nonce that policy names, and the nonce is new on every
response, so:

- Don't add a second `Content-Security-Policy` in nginx, Apache, Caddy, a
  CDN or a load balancer (`add_header Content-Security-Policy ...`, Helmet-like
  middleware, "security headers" options). A browser applies every policy it
  gets, and one without this response's nonce blocks the forum's scripts.
- Don't let a proxy or CDN cache HTML pages and serve one copy to several
  visitors: the copy's nonce wouldn't match the header sent with it. Question
  pages are marked `private`, and a `304 Not Modified` carries no policy, so
  the browser keeps the policy stored with its own copy.
- Scripts, styles, fonts, frames or form targets from other origins (analytics,
  widgets, a chat box in the custom `<head>` or footer) go in Admin > Security >
  "Extra script sources", separated by spaces, e.g.
  `https://plausible.io https://cdn.example.org`. They are added to
  `script-src`, `style-src`, `font-src`, `connect-src`, `frame-src` and
  `form-action`. Inline `<script>` elements in the custom `<head>`, header and
  footer get the nonce automatically; inline event handlers (`onclick="..."`)
  never run.
- To try a change first, set `CONTENT_SECURITY_POLICY_REPORT_ONLY = True` in
  `settings_local.py`: blocked items are only reported (security log, Sentry).
  `CONTENT_SECURITY_POLICY = "..."` replaces the whole policy (write `{nonce}`
  where the nonce goes, e.g. `script-src 'self' 'nonce-{nonce}'`).

### 5. Scheduled jobs

`crontab -e -u osqa`:

```cron
OSQA=/srv/osqa/app
PY=/srv/osqa/venv/bin/python
# Daily digests and dormant (bot) account clean-up
15 6 * * *  cd $OSQA && $PY manage.py send_email_alerts
45 6 * * *  cd $OSQA && $PY manage.py cleanup_inactive_users
0 7 * * *   cd $OSQA && $PY manage.py remind_unaccepted
# Only with the AI module configured:
30 2 * * *  cd $OSQA && $PY manage.py build_knowledge_index
5 * * * *   cd $OSQA && $PY manage.py suggest_answers
```

### 6. First steps on the site

See [After installing](#after-installing) below.

### Updating a server installation

```bash
cd /srv/osqa/app
sudo -u osqa git pull
sudo -u osqa /srv/osqa/venv/bin/pip install -r requirements.txt
sudo -u osqa /srv/osqa/venv/bin/python manage.py migrate
sudo systemctl restart osqa
```

Read the README's "Upgrading an existing installation" notes first. When an
update adds the search index (migration `0013`), also run
`manage.py rebuild_search_index` once after `migrate`. Take a
database backup before `migrate`, and run `migrate` in a quiet period on large
sites, since some migrations add indexes to big tables.

---

## Option B: Docker Compose

The repository's `Dockerfile` builds an image that runs gunicorn on port 8000
as an unprivileged user, with a health check on `/healthz`.
`docker-compose.yml` publishes it on `127.0.0.1:8000` for a reverse proxy on
the host. It mounts `settings_local.py`, `forum/upfiles`, `cache` and `log`
from the host, so the image contains no site data.

### 1. Configure

```bash
git clone https://github.com/openforis/osqa.git && cd osqa
cp settings_local.py.dist settings_local.py     # DATABASES, APP_URL, ALLOWED_HOSTS, CACHES
cat > .env <<EOF
OSQA_SECRET_KEY=$(python3 -c 'from secrets import token_urlsafe; print(token_urlsafe(50))')
SENTRY_DSN=
SENTRY_ENVIRONMENT=production
EOF
mkdir -p forum/upfiles cache log
```

In Docker the database host is not `127.0.0.1`. Use the database container's
service name (below), or `host.docker.internal` for a database on the host.
`OSQA_TRUSTED_PROXIES` defaults to `1` in `docker-compose.yml`, which is right
for one reverse proxy on the host.

### 2. Database and cache in the same Compose project (optional)

To run MariaDB and memcached next to the forum, add a
`docker-compose.override.yml`:

```yaml
services:
  osqa:
    depends_on: [db, memcached]
  db:
    image: mariadb:11.4
    restart: unless-stopped
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
    environment:
      MARIADB_DATABASE: osqa
      MARIADB_USER: osqa
      MARIADB_PASSWORD: ${DB_PASSWORD:?set DB_PASSWORD in .env}
      MARIADB_ROOT_PASSWORD: ${DB_ROOT_PASSWORD:?set DB_ROOT_PASSWORD in .env}
    volumes:
      - db-data:/var/lib/mysql
  memcached:
    image: memcached:1.6
    restart: unless-stopped
volumes:
  db-data:
```

and in `settings_local.py` use `'HOST': 'db'` and
`CACHES = {'default': {'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache', 'LOCATION': 'memcached:11211'}}`.

### 3. Build, create the tables, start

```bash
docker compose build --build-arg SENTRY_RELEASE=osqa@$(git rev-parse --short=12 HEAD)
docker compose run --rm osqa python manage.py migrate
docker compose up -d
docker compose ps                    # the osqa service turns "healthy"
curl http://127.0.0.1:8000/healthz   # -> ok
```

Put the nginx configuration from Option A in front of `127.0.0.1:8000`; drop
the two optional `alias` locations (OSQA then sends the caching headers
described there itself), or point them at the mounted `forum/upfiles`. Caddy, Traefik or a cloud load balancer work the same way, as
long as they set `X-Forwarded-For` and `X-Forwarded-Proto`.

### 4. Scheduled jobs

From the host's crontab:

```cron
15 6 * * *  cd /srv/osqa && docker compose exec -T osqa python manage.py send_email_alerts
45 6 * * *  cd /srv/osqa && docker compose exec -T osqa python manage.py cleanup_inactive_users
0 7 * * *   cd /srv/osqa && docker compose exec -T osqa python manage.py remind_unaccepted
```

### Updating

```bash
git pull
docker compose build --build-arg SENTRY_RELEASE=osqa@$(git rev-parse --short=12 HEAD)
docker compose run --rm osqa python manage.py migrate
docker compose up -d
```

### Backups

- **Database:** `docker compose exec -T db mariadb-dump -uroot -p"$DB_ROOT_PASSWORD" --single-transaction osqa | gzip > osqa-$(date +%F).sql.gz`.
  On a plain server, use `mysqldump --single-transaction osqa`.
- **Files:** `forum/upfiles/` and `settings_local.py`.
- `cache/` and `log/` don't need backing up.

---

## Option C: Kubernetes, Cloud Run and other container platforms

Use the same image. What changes is where state lives:

- **Configuration:** mount `settings_local.py` from a Secret (or ConfigMap plus
  Secret) at `/app/settings_local.py`. Pass `OSQA_SECRET_KEY`, `SENTRY_DSN`,
  `OSQA_TRUSTED_PROXIES` and the AI keys as environment variables from Secrets.
  You can keep database passwords out of the file by reading them in
  `settings_local.py` with `os.environ['DB_PASSWORD']`.
- **Database:** use a managed MySQL/MariaDB or PostgreSQL (Cloud SQL, RDS, Azure Database).
- **Cache:** use a managed or in-cluster **memcached** (Memorystore,
  ElastiCache). The file cache doesn't work across replicas, and the
  local-memory cache isn't shared between workers.
- **Uploads:** `/app/forum/upfiles` must be persistent and, with more than one
  replica, shared. Use a ReadWriteMany volume (NFS, Filestore, EFS), or on
  Cloud Run a Cloud Storage volume mount. Container file systems are
  temporary, so without such a volume uploaded images are lost on the next deploy.
- **Logs:** with no persistent `log/` directory, send logs to the platform:
  set `LOGGING` in `settings_local.py` to a `logging.StreamHandler`
  (stdout). Keep the `forum.security` logger at `INFO`, so its lines reach
  the platform's log viewer.
- **Health checks:** use `GET /healthz` for liveness and readiness probes. It
  answers on any host name, also during maintenance, and returns 503 when the
  database is down.
- **Migrations:** run `python manage.py migrate` as a one-off job (a Kubernetes
  Job, Helm hook or Cloud Run job) before each new version starts serving.
- **Scheduled jobs:** create CronJobs (Kubernetes) or Cloud Scheduler +
  Cloud Run jobs that run the same image with
  `python manage.py send_email_alerts`, `cleanup_inactive_users` and `remind_unaccepted` (plus the
  AI commands if used).
- **Proxy count:** the ingress or load balancer is the proxy. Usually
  `OSQA_TRUSTED_PROXIES=1`; count every hop that appends to
  `X-Forwarded-For` (check the platform's documentation, e.g. a Google
  load balancer in front of Cloud Run adds its own entry). The security log's
  `ip=` field shows which address OSQA sees. If it is always the same internal
  address, the count is too low.
- **Resources:** about 3 gunicorn workers per container, with 512 MB to 1 GB of memory.

---

## After installing

1. **Owner account:** open the site and register. The first account becomes the
   site owner (superuser). Do this immediately after the site is reachable.
2. **Admin > Email settings:** SMTP server, sender address, then "test email
   settings". Notifications, sign-in links and digests depend on it.
3. **Admin > Basic settings and look and feel:** site name, description, logo
   and colours; the about, FAQ and privacy page texts.
4. **Admin > Security:** sign-in lockout, posting limits. If you add analytics
   or widgets in the custom `<head>` or footer, list their origins under
   "Extra script sources", or the Content-Security-Policy blocks them.
5. **Admin > Users settings:** sign-up protections and dormant-account
   clean-up (defaults are fine for most sites).
6. **Optional:**
   - Admin > External Keys: Google/GitHub/Facebook sign-in (callback URLs in
     the README's "Social login").
   - Admin > AI assistance: Gemini key or Vertex AI project.
   - `SENTRY_DSN`.
7. **Check:**
   - `/healthz` returns `ok`.
   - Ask a question, answer it from a second account, and check the
     notification email and its unsubscribe link.
   - Upload an image.
   - With Sentry configured, send a test event and check it arrives:
     `python manage.py shell -c "import sentry_sdk; sentry_sdk.capture_message('OSQA test'); sentry_sdk.flush()"`.
     Pages that don't exist (404) are not reported.

Admin pages are at `/admin/`. Django's own model admin is at `/nimda/`, for
superusers.
