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
- Cancelling a vote didn't lower the post score in earlier versions of the
  port; run Admin > Recalculate scores and reputation once to fix stored
  scores.
- Migrations `0003`-`0005` add indexes to large tables (nodes, actions); run
  `migrate` during a quiet period on big sites.
- The OpenID, Twitter, old Facebook, stats, updates and Sphinx modules were
  removed: their services no longer exist (stats/updates reported to the
  defunct osqa.net). Migration `0006` drops the OpenID tables. Users who only
  signed in with one of those providers can request a temporary login link by
  email ("Forgot your password?") and then connect Google, GitHub or Facebook,
  or set a password, from their account settings.
- Migration `0007` hides the two default sidebar blocks (the WebFaction hosting
  advert and "Learn more about OSQA", both pointing at services that no longer
  exist) when they still contain the shipped text; edited blocks are kept.
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

The dump file name is set at the top of `split_insert_statements.py`. The
import clears the cache when it finishes; clear it yourself (`rm -rf cache/*`
for the default file cache) whenever you replace or edit the database outside
the site, since posts and users are served from the cache. After importing,
run Admin > Recalculate scores and reputation.
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

All modules below except the full-text search ones are covered by the test suite.

| Module | What it does | Setup |
|---|---|---|
| `localauth` | Username/password login and registration | - |
| `oauth2auth` | "Sign in with Google / GitHub / Facebook" (OAuth 2.0 / OpenID Connect) | Admin > External Keys; see below |
| `ldapauth` | Login against an LDAP directory | `pip install python-ldap`; LDAP settings in the admin. Disables itself without python-ldap |
| `default_badges` | The standard badges | Thresholds under Admin > Badges |
| `project_badges` | "Bug Buster" badge for questions tagged `bug` | Disabled by default |
| `akismet` | Spam check of new posts with Akismet | Akismet API key in the admin; does nothing without it |
| `recaptcha` | reCAPTCHA v2 on registration | Disabled by default; site and secret keys in the admin |
| `exporter` | Full-site XML backup (Admin > Tools > XML data exporter) and restore (XML data importer) | - |
| `sximporter` | Import of a StackExchange data dump (`/admin/sximporter/`) | Old StackExchange 1.0 dump format only |
| `robotstxt` | Editable `/robots.txt` | - |
| `mysqlfulltext`, `pgfulltext` | Full-text search on MySQL / PostgreSQL | Enabled automatically on that database; not yet tested on Python 3 |

### Social login

For each provider you want, create an OAuth app and enter its client id and
secret under Admin > External Keys; the button appears on the sign-in page once
both are set. Register this redirect (callback) URI with the provider:

- Google (Google Cloud console > Credentials): `<APP_URL>/account/google/done/`
- GitHub (Settings > Developer settings > OAuth Apps): `<APP_URL>/account/github/done/`
- Facebook (developers.facebook.com, Facebook Login): `<APP_URL>/account/facebook/done/`

Signed-in users can connect more providers from their account's
authentication settings.

## Look and feel

The default skin is responsive (phones, tablets, desktops). Its modern styles
live in `forum/skins/default/media/style/modern.css`, loaded after the legacy
`style.css`; the colour tokens at the top of that file (`--brand`, `--accent`,
...) are the quickest way to re-brand the site. Admin > Custom CSS is loaded
last and can override them, e.g. `:root { --brand: #00704a; }`.

Visitors whose device is set to dark mode get a dark colour scheme; turn it off
under Admin > View settings > "Dark mode" if your custom CSS assumes a light
background. jQuery 3.7 and jQuery UI 1.13 are served from the skin
(`media/vendor/`), so pages load no third-party scripts unless Google search,
Analytics or a provider login is configured.

Admin > "Switch to django style interface" uses Django's own admin stylesheets;
OSQA serves them itself from `/m/django_admin/`, like the skin media under
`/m/`, so no `collectstatic` or web-server configuration is needed.

The header search box uses Google Programmable Search when Admin > External
Keys > "Google custom search engine ID" is set (it defaults to the engine this
fork used before), and the site's own search when it is empty.

## Features added in this fork

- Fenced code blocks (```` ```python ````) and syntax highlighting (highlight.js,
  self-hosted) in posts and the editor preview.
- `@username` mentions in posts and comments, with autocomplete and an email to
  the mentioned user (controlled by the "mentions me" notification setting).
- Close as duplicate with a link to the original question; moderators can move
  the answers to the original.
- Paste or drop images into the editor; drafts of questions and answers are
  saved in the browser until posted.
- schema.org QAPage structured data and OpenGraph/Twitter link previews.
- Tag descriptions and synonyms (moderators: "edit tag" on a tag's page).
- Users can download their data (JSON) and delete their account, which
  anonymises it and keeps their posts (profile menu > "your data and account").

## Layout

- `forum/` - the application: models, views, actions, templates (`skins/`),
  settings stored in the database (`forum/settings/`), tests (`forum/tests/`)
- `forum_modules/` - optional modules
- `scripts/` - data import (`data_import/`) and development helpers (`dev/`)
- `docs/` - database reference and migration guide; `docs/upgrade-history/`
  holds the notes from the Django 1.6 to 4.2 port

## License

See `LICENSE`.
