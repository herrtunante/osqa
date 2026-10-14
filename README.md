# OSQA, Open Foris edition

OSQA is an open source Q&A site (questions, answers, comments, voting, tags,
reputation, badges and moderation), originally from osqa.net. This is the
edition maintained by [Open Foris](https://openforis.org/) for its support
forum; it runs on **Django 5.2 LTS and Python 3.12+**.

Any organisation can use it for its own Q&A community: the code is free and
open source (GPL v3) at <https://github.com/openforis/osqa>. Follow the setup
below, change the name, logo, colours and texts in the admin, and turn the
optional modules (social login, AI assistance, spam checks) on or off.

## Requirements

- Python 3.12 or newer
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

Step-by-step guides:
- [docs/deployment.md](docs/deployment.md): a new forum on a server (systemd +
  nginx), with Docker Compose, or on Kubernetes / Cloud Run.
- [docs/upgrading-from-original-osqa.md](docs/upgrading-from-original-osqa.md):
  moving a live forum from the original OSQA (Python 2 / Django 1.6).


- WSGI entry point: `wsgi.py` (`gunicorn wsgi:application`); for mod_wsgi copy
  `osqa.wsgi.dist`.
- Cache: the model layer caches objects and query results, so the cache must be
  shared by all worker processes. `settings_local.py.dist` uses a file cache;
  memcached is recommended. The per-process local-memory cache is not suitable.
- HTTPS: with an `https://` `APP_URL`, secure cookies and HSTS are on by default.
  Behind a reverse proxy set `TRUSTED_PROXY_COUNT = 1` (or `OSQA_TRUSTED_PROXIES=1`,
  the docker-compose default): visitor IPs then come from `X-Forwarded-For` and
  HTTP is redirected to HTTPS. See "Security and logging" below.
- Email digests: run `cron/send_email_alerts` daily.
- Dormant (bot) accounts: run `cron/cleanup_inactive_users` daily (see "Spam sign-ups" below).
- Accept reminders: run `cron/remind_unaccepted` daily (see "Notifications and support dashboard" below).
- AI assistant (optional, `ai` module): run `cron/build_knowledge_index` nightly and
  `cron/suggest_answers` hourly (see "AI assistance" below).

### Docker

```bash
cp settings_local.py.dist settings_local.py   # configure the database, APP_URL, ...
echo "OSQA_SECRET_KEY=<random key>" > .env
docker compose up --build
```

The container runs gunicorn as an unprivileged user on `127.0.0.1:8000`; put a
TLS reverse proxy in front of it. `settings_local.py`, uploads, cache and logs
are mounted from the host, not baked into the image.

## Languages

The interface is available in English, Spanish, French and Portuguese.
- **Choosing a language:** visitors get their browser's language, and can
  pick another with the switcher in the footer (remembered in a cookie).
- **Settings:**
  - `LANGUAGE_CODE` in `settings_local.py` is the default.
  - `LANGUAGES` limits or extends the list.
- **What stays the same:** URLs are identical in every language.
- **Limits:**
  - Posts aren't translated.
  - Notification emails and admin setting labels use the site's default language.

Updating the translations after changing texts (needs GNU gettext, e.g.
`apt-get install gettext`):

```bash
python manage.py makemessages -l es -l fr -l pt --no-location --no-obsolete \
    --ignore='venv/*' --ignore='docs/*' --ignore='scripts/*' --ignore='forum/tests/*' \
    --ignore='cache/*' --ignore='tmp/*' --ignore='*/media/vendor/*'
# translate the new entries in forum/locale/<lang>/LC_MESSAGES/django.po, then
python manage.py compilemessages
```

Commit both the `.po` and the compiled `.mo` files. To add a language, add
it to `LANGUAGES` and pass `-l <code>` to `makemessages`.
- **Strings defined at import time:** form labels, badge names, header
  links and tab titles must use `gettext_lazy`, imported as `_lazy`, so they
  follow each visitor's language. `makemessages` (overridden in
  `forum/management/commands/makemessages.py`) extracts `_lazy("...")` too.
- **First versions:** the current translations were written with AI
  assistance; a review by native speakers is welcome.

## Notifications and support dashboard

- **Bell:** signed-in users see a bell in the header with their unread
  notifications:
  - answers to questions they ask or follow;
  - comments on their posts;
  - mentions;
  - accepted answers;
  - badges.

  The list is at `/notifications/`. These notifications don't depend on the
  email settings, and opening a question marks its notifications read.
- **Support dashboard:** at Admin > Support dashboard, linked as "support
  queue" in the header for staff and moderators. It shows, for a period and
  optionally one tag:
  - questions asked;
  - the share answered by someone other than the asker;
  - median time to the first answer;
  - the share with an accepted answer;
  - a per-product-tag table;
  - the queue of unanswered questions, oldest first, with those past the
    response target highlighted;
  - answered questions with no accepted answer.

  Product tags, the response target and reminders are under Admin > Support
  and reminders.
- **Accept reminders:** `manage.py remind_unaccepted` (daily) reminds an asker
  once, by notification and by email, when their question has had an answer
  for 7 days (configurable) and none is accepted. It only looks at questions
  from the last 60 days, so an imported history isn't flooded. There is no
  email if the asker stopped following the question or turned emails off.

## Security and logging

- **Sign-in:** after 5 wrong passwords an account's password sign-in pauses for
  15 minutes, and one IP address may fail 20 times. "Forgot password" links are
  limited to 3 per address per hour, work once for 24 hours, and the page never
  says whether an address has an account.
- **Passwords:** Django's validators (at least 8 characters, not common, not
  numeric only, not similar to the username or email). Change them with
  `AUTH_PASSWORD_VALIDATORS`. Changing a password signs out the account's other sessions.
- **Posting limits:** questions, answers and comments per user per hour
  (moderators exempt).
- **Headers:** Content-Security-Policy (scripts, styles and frames only from the
  site and the Google services OSQA uses; no plugins; only this site may frame
  it), Permissions-Policy, plus Django's nosniff, Referrer-Policy and X-Frame-Options.
  Browsers report blocked content to `/csp-report/`, which goes to the security
  log. Add origins for your own analytics or widgets in the admin's extra script
  sources setting; `CONTENT_SECURITY_POLICY_REPORT_ONLY = True` only reports, and
  `CONTENT_SECURITY_POLICY = None` turns the header off.
- All the limits above are under Admin > Security.
- **Security log** (`log/security.log`, logger `forum.security`), one line per event:
  - failed and paused sign-ins;
  - sign-in link requests and uses;
  - password changes;
  - pages refused to signed-in users;
  - permission, reputation and settings changes;
  - maintenance mode;
  - account deletions;
  - blocked content.
- **Moderator actions** (votes, deletions, suspensions, closes, …) stay in the
  actions table with user and IP.
- **Log files:** `settings_local.py.dist` rotates them at 10 MB.
- **Sentry:** set `SENTRY_DSN` (settings or environment) to report server
  errors. Browser JavaScript errors and session health go to
  `SENTRY_FRONTEND_DSN`, which defaults to the same DSN (use a separate
  JavaScript project if you prefer; `off` turns it off).
  - Other settings: `SENTRY_ENVIRONMENT` (default `production`),
    `SENTRY_RELEASE` (default `osqa@<git commit>`; for Docker pass
    `--build-arg SENTRY_RELEASE=...`) and `SENTRY_TRACES_SAMPLE_RATE`
    (performance tracing, default 0).
  - Blocked content (CSP reports) arrives as warnings.
  - Flood protection: each visitor (signed-in user, or IP address) sends at
    most `SENTRY_MAX_EVENTS_PER_VISITOR` events per hour (default 10) from
    the server, and the same number from their browser. The browser count is
    kept in localStorage, so reloading doesn't reset it. Identical repeated
    errors are merged by Sentry's SDK as well. Because the browser DSN is
    public, also set a rate limit on the client key in Sentry
    (Project Settings > Client Keys) to cap deliberate abuse.
  - No request bodies, cookies, IP addresses, emails or log lines with them are
    sent. Signed-in users appear by numeric id only.
  - The browser SDK is self-hosted (`media/vendor/sentry-10.75.3`, see its
    README) and loads before the other scripts.
- **Health check:** `/healthz` returns `ok` when the app and database respond. It
  works on any host name and during maintenance; the Docker image uses it.
- **Dependencies:** CI runs `pip-audit`, and Dependabot proposes updates weekly.

Existing installations: copy the `LOGGING` block from `settings_local.py.dist`
into `settings_local.py` to get rotation and the security log.
Email validation and sign-in links sent before the upgrade stop working, so ask
for a new one.

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

The whole procedure (freeze, dump, import, checks, transfer to MySQL, files,
go-live) is in [docs/upgrading-from-original-osqa.md](docs/upgrading-from-original-osqa.md).
In short:

The scripts in `scripts/data_import/` load a MySQL dump of the old site into a
fresh SQLite database (`osqa.db`). Run them from the repository root:

```bash
mkdir importData        # place the dump here (git-ignored, never commit it)
python scripts/data_import/split_insert_statements.py importData/<dump>.sql   # -> importData/osqa-split.sql
python manage.py migrate                                 # with DATABASES pointing at osqa.db
python scripts/data_import/import_original_data.py
```

The import clears the cache when it finishes; clear it yourself (`rm -rf cache/*`
for the default file cache) whenever you replace or edit the database outside
the site, since posts and users are served from the cache. After importing,
run Admin > Recalculate scores and reputation. To move the result into
MySQL/MariaDB or PostgreSQL, use `dumpdata --all` / `loaddata` as described
in the guide. `docs/data-migration-guide.md` describes other approaches (MySQL to
MySQL/PostgreSQL) and how to verify the result; `docs/database-tables.md` lists
the tables.

## Tests

```bash
pip install -r requirements-dev.txt
OSQA_SETTINGS_LOCAL=settings_local_test python manage.py test forum.tests
```

Always pass the `forum.tests` label.

Browser tests (Playwright) go through every workflow, from anonymous visitor
to administrator, on desktop and phone; see [e2e/README.md](e2e/README.md):

```bash
python -m playwright install chromium
OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e
```
 CI (`.github/workflows/tests.yml`) runs the
suite and `makemigrations --check` on Python 3.12 and 3.13.

## Modules

Optional features live in `forum_modules/` and are switched off with
`DISABLED_MODULES` in `settings_local.py`.

All modules below except the (switched-off) full-text search ones are covered by the test suite.

| Module | What it does | Setup |
|---|---|---|
| `localauth` | Username/password login and registration | - |
| `oauth2auth` | "Sign in with Google / GitHub / Facebook" (OAuth 2.0 / OpenID Connect) | Admin > External Keys; see below |
| `ldapauth` | Login against an LDAP directory | `pip install python-ldap`; LDAP settings in the admin. Disables itself without python-ldap |
| `default_badges` | The standard badges | Thresholds under Admin > Badges |
| `project_badges` | "Bug Buster" badge for questions tagged `bug` | Disabled by default |
| `akismet` | Spam check of new posts with Akismet | Akismet API key in the admin; does nothing without it |
| `ai` | Duplicate question detection and spam/abuse moderation with Google Gemini | Gemini API key or Vertex AI project in the admin; does nothing without one. See below |
| `recaptcha` | reCAPTCHA v2 on registration | Disabled by default; site and secret keys in the admin |
| `exporter` | Full-site XML backup (Admin > Tools > XML data exporter) and restore (XML data importer) | - |
| `sximporter` | Import of a StackExchange data dump (`/admin/sximporter/`) | Old StackExchange 1.0 dump format only |
| `robotstxt` | Editable `/robots.txt` | - |
| `mysqlfulltext`, `pgfulltext` | Full-text search on MySQL / PostgreSQL | Switched off: not ported to Python 3 / Django 5 yet. Search uses the built-in keyword matching (plus semantic search with the `ai` module) |

### Spam sign-ups

Bots that register and never post (often only to put links in their profile)
are handled without any external service; the thresholds are under Admin >
Users settings:

- The sign-up forms have a hidden honeypot field and a signed time stamp:
  forms filled in faster than "Minimum time to fill in the sign-up form"
  (3 seconds) or with the honeypot filled are refused. At most "Sign-ups per
  IP address per hour" (5) accounts are created from one address.
- Below "Reputation for profile links" (25) a user's website and the links in
  their "about me" are plain text and their profile page carries
  `noindex, nofollow`; trusted users' profile links are `rel="nofollow ugc"`.
- `cron/cleanup_inactive_users` (`manage.py cleanup_inactive_users`, run
  daily) deletes accounts older than "Delete dormant accounts after" (15 days)
  that never confirmed their email, have no posts, votes or other activity, and
  haven't logged in or used the site in that time. Staff accounts are never
  deleted; `--dry-run` lists them instead, and 0 days turns it off.
- With the `ai` module configured, Gemini also reviews new and changed
  profiles (website, "about me") of low-reputation users; suspect accounts are
  listed under Admin > Flagged posts, where a moderator suspends the account or
  deletes it (deleting is offered only for accounts without posts).

### AI assistance

The `ai` module uses Google Gemini, either through the Gemini API or through
Vertex AI in your own Google Cloud project:

- **Gemini API**: enter a key from Google AI Studio under Admin > External
  Keys (or set `GEMINI_API_KEY` / `GOOGLE_API_KEY` on the server).
- **Vertex AI**: enter the project ID (and region, default `global`) under
  Admin > AI assistance, or set `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`.
  The server authenticates with Application Default Credentials: on Google
  Cloud the instance's service account, elsewhere a service account key in
  `GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth application-default login`.
  The account needs the Vertex AI User role. A project set here wins over an
  API key.

Each feature can be switched off under Admin > AI assistance:

- **Duplicate detection**: while someone types a question title, existing
  questions with matching keywords are sent to Gemini, which picks the ones
  that ask the same thing; the ask page lists them with a one-line reason.
  Results are cached and limited per visitor per hour.
- **Spam and abuse moderation**: new and edited posts by users below a
  reputation threshold (staff are never checked) are reviewed in the
  background. Likely spam, abuse or off-topic posts, and posts Gemini's safety
  filters refuse to read, are listed under Admin > Flagged posts > "Reported
  by automated checks", where a moderator dismisses the report or deletes the
  post. The AI never hides or deletes anything itself, and a report costs the
  author no reputation.
- **Profile spam review**: new or changed websites and "about me" texts of
  low-reputation users are checked for SEO links and advertising; suspect
  accounts are listed in the same queue (see "Spam sign-ups" above).

- **Assistant and knowledge index**: `manage.py build_knowledge_index`
  (nightly) indexes answered forum threads and the documentation crawled from
  "Documentation to crawl" (default: openforis.org, the Collect Earth Online
  docs, FAO's Open Foris pages, earthmap.org; same site and path only,
  robots.txt respected) with Gemini embeddings; only changed text is
  re-embedded. The ask page offers "Ask the AI assistant first": Gemini
  answers only from the closest threads and pages, citing them.
- **Nothing made up**: an assistant answer or suggestion is shown only when it
  cites at least one forum thread or documentation page and a second Gemini
  check confirms that every statement (steps, setting names, versions...) is
  supported by those sources and that they really address the question;
  otherwise the assistant says it found no clear answer and no suggestion is
  posted. The web at large is not used: add documentation sites to
  "Documentation to crawl" instead.
- **Suggested answers**: `manage.py suggest_answers` (hourly) adds a marked AI
  suggestion with sources to questions unanswered after 24 hours, when the
  forum or the documentation clearly covers them (see above). The asker
  and moderators can mark it helpful or remove it; it isn't an answer, earns
  no reputation and never goes back into the index or the exports.

These features send the text of public posts to Google (nothing else about
the author); say so in your privacy page if you turn them on. The model is
configurable (default `gemini-3.8-flash`; `gemini-3.5-flash-lite` is
cheaper). Without credentials, or if the API is unreachable, the site works as
before.

### Content for AI agents

So other assistants (Gemini, Claude, ChatGPT, in-house agents) can use the
forum and cite it, without the `ai` module:

- `/questions/<id>.md`: a thread as Markdown with metadata (status, score,
  tags, dates), accepted answer first; question pages link it.
- `/llms.txt`: what the site is and how to read it.
- `/export/qa.jsonl`: all answered questions with their answers.
- `/mcp`: a read-only MCP server (Streamable HTTP) with `search_forum` and
  `get_question`; with the knowledge index built, search is semantic.

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
background. jQuery 3.7, jQuery UI 1.14 (tag and user suggestions), Flot 4.2
(charts) and highlight.js 11.11 are served from the skin (`media/vendor/`), so
pages load no third-party scripts unless Google search, Analytics or a provider
login is configured. Post HTML is sanitized with nh3 and turned into plain text
for emails with html2text.

Admin > "Switch to django style interface" uses Django's own admin stylesheets;
OSQA serves them itself from `/m/django_admin/`, like the skin media under
`/m/`, so no `collectstatic` or web-server configuration is needed.

The header search box uses Google Programmable Search when Admin > External
Keys > "Google custom search engine ID" is set (it defaults to the engine this
fork used before), and the site's own search when it is empty.

## Features added in this fork

- Fenced code blocks (```` ```python ````) and syntax highlighting (highlight.js,
  self-hosted) in posts and the editor preview. The preview is rendered by the
  server with the same Markdown pipeline as published posts, so what you see is
  what gets posted.
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
- Optional AI assistance with Google Gemini: duplicate detection while asking, and a
  moderation queue for likely spam (see "AI assistance" above).

## Layout

- `forum/` - the application: models, views, actions, templates (`skins/`),
  settings stored in the database (`forum/settings/`), tests (`forum/tests/`)
- `forum_modules/` - optional modules
- `scripts/` - data import (`data_import/`) and development helpers (`dev/`)
- `docs/` - database reference and migration guide; `docs/upgrade-history/`
  holds the notes from the Django 1.6 to 4.2 port

## License

See `LICENSE`.
