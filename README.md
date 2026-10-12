# OSQA

OSQA is an open source Q&A site (questions, answers, comments, voting, tags,
reputation, badges and moderation), originally from osqa.net. This fork runs on
**Django 5.2 LTS and Python 3.11+**.

## Requirements

- Python 3.11 or newer
- MySQL/MariaDB (default), PostgreSQL or SQLite
- For `mysqlclient`: the MySQL client headers, e.g.
  `apt-get install default-libmysqlclient-dev pkg-config`
- Optional: memcached (recommended cache in production)

## Setup

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt        # requirements-dev.txt adds the debug toolbar

cp settings_local.py.dist settings_local.py
# Edit settings_local.py: DATABASES, APP_URL, ALLOWED_HOSTS and SECRET_KEY.
# Generate a key with:
#   python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())'

venv/bin/python manage.py migrate
venv/bin/python manage.py runserver
```

The first account registered through the site becomes the site owner and
superuser. Site settings (email server, reputation thresholds, ...) are edited
in the admin pages at `/admin/` and stored in the database.

`SECRET_KEY` can also come from the `OSQA_SECRET_KEY` environment variable; the
site refuses to start without one.

## Production

- WSGI entry point: `wsgi.py` (`gunicorn wsgi:application`); for mod_wsgi copy
  `osqa.wsgi.dist`.
- Cache: the model layer caches objects and query results, so the cache must be
  shared by all worker processes. `settings_local.py.dist` uses a file cache;
  memcached is recommended. The per-process local-memory cache is not suitable.
- Behind HTTPS, enable the settings listed in `settings_local.py.dist`
  (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`).
- Email digests: run `cron/send_email_alerts` daily.

### Docker

```bash
cp settings_local.py.dist settings_local.py   # configure the database, APP_URL, ...
echo "OSQA_SECRET_KEY=<random key>" > .env
docker compose up --build
```

The container runs gunicorn as an unprivileged user on `127.0.0.1:8000`; put a
TLS reverse proxy in front of it. `settings_local.py`, uploads, cache and logs
are mounted from the host, not baked into the image.

## Upgrading an existing installation

- Add `SECRET_KEY` to `settings_local.py` (the old built-in key was public).
  Changing it logs everybody out.
- Sessions are now stored as JSON, so existing sessions are dropped once.
- An old `CACHE_BACKEND = 'file://...'` line keeps working; it is translated
  into `CACHES`.
- A database created by the old Django 1.6/South version is not migrated in
  place: create a fresh database with `migrate` and import the old data into it
  (next section).

## Importing a database from the old OSQA (Django 1.6)

The scripts in `scripts/data_import/` load a MySQL dump of the old site into a
fresh SQLite database (`osqa.db`). Run them from the repository root:

```bash
mkdir importData        # place the dump here (git-ignored, never commit it)
python scripts/data_import/split_insert_statements.py   # -> importData/osqa-split.sql
python manage.py migrate                                 # with DATABASES pointing at osqa.db
python scripts/data_import/import_original_data.py
```

The dump file name is set at the top of `split_insert_statements.py`.
`docs/data-migration-guide.md` describes other approaches (MySQL to
MySQL/PostgreSQL) and how to verify the result; `docs/database-tables.md` lists
the tables.

## Tests

```bash
pip install -r requirements-dev.txt
OSQA_SETTINGS_LOCAL=settings_local_test python manage.py test forum.tests
```

Always pass the `forum.tests` label. CI (`.github/workflows/tests.yml`) runs the
suite and `makemigrations --check` on Python 3.11 and 3.13.

## Modules

Optional features live in `forum_modules/` and are switched off with
`DISABLED_MODULES` in `settings_local.py`.

- Working and covered by tests: `localauth` (username/password login),
  `default_badges` (the standard badges; thresholds under Admin > Badges) and
  `robotstxt`.
- Load, but untested on Python 3: `mysqlfulltext` and `pgfulltext` (enabled
  automatically on that database).
- Not yet ported to Python 3; they fail to import and are skipped with an error
  in the log: `openidauth`, `oauthauth`, `facebookauth`, `akismet`, `exporter`, `sximporter` and
  `updates`. `ldapauth`, `stats`, `recaptcha`, `project_badges` and
  `sphinxfulltext` have not been checked. Add the modules you don't need to
  `DISABLED_MODULES` to keep the log clean.

## Layout

- `forum/` - the application: models, views, actions, templates (`skins/`),
  settings stored in the database (`forum/settings/`), tests (`forum/tests/`)
- `forum_modules/` - optional modules
- `scripts/` - data import (`data_import/`) and development helpers (`dev/`)
- `docs/` - database reference and migration guide; `docs/upgrade-history/`
  holds the notes from the Django 1.6 to 4.2 port

## License

See `LICENSE`.
