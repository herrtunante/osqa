# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OSQA is an open source Q&A system. It provides Stack Overflow-like functionality including questions, answers, comments, voting, badges, reputation points, and user moderation.

It runs on Django 5.2 LTS and Python 3.12+; it was ported from Django 1.6 / Python 2 (notes in `docs/upgrade-history/`). See `README.md`, `docs/deployment.md` (new forums: server, Docker, Kubernetes) and `docs/upgrading-from-original-osqa.md` (moving a live old forum: import into SQLite with `scripts/data_import/`, then `dumpdata --all`/`loaddata` into MySQL). Old password hashes are read by `forum.utils.hashers.LegacySHA1PasswordHasher` and upgraded on sign-in; the `mysqlfulltext`/`pgfulltext` modules are switched off (not ported).

## Core Architecture

### Model Hierarchy

The application uses a polymorphic node system as the foundation:

- **Node** (`forum/models/node.py`): Base model for all content types
  - Questions, Answers, and Comments all inherit from Node
  - Uses `node_type` field to distinguish types
  - Implements a "leaf" pattern where queries return the specific subclass
  - State management through `NodeState` and `NodeStateDict`
  - Revision tracking via `NodeRevision`

Key models:
- `Question`, `Answer`, `Comment`: Content types extending Node
- `User` (`forum/models/user.py`): Extended Django auth user with reputation
- `Tag` (`forum/models/tag.py`): Question categorization
- `Action` (`forum/models/action.py`): Tracks all user actions (votes, edits, etc.)

### Views Organization

Views are organized by responsibility in `forum/views/`:
- `readers.py`: Read-only views (question list, question detail, user profiles)
- `writers.py`: Content creation/editing (ask question, post answer, upload)
- `commands.py`: AJAX commands for user actions (vote, accept, flag, delete)
- `auth.py`: Authentication and user management
- `admin.py`: Administrative interfaces
- `meta.py`: Static pages and error handlers

### Module System

OSQA has a pluggable module system in `forum_modules/`:
- Modules are auto-discovered from directories with `__init__.py`
- Each module can provide middleware, template loaders, and settings
- Modules can be disabled via `DISABLED_MODULES` in settings
- Available modules: see the table in `README.md` (authentication, badges, spam checks, backup/import, full-text search)

### Settings System

Settings are defined in `forum/settings/`:
- Each file defines related settings grouped into `SettingSet` objects
- Settings are stored in the database and can be modified at runtime
- Base Django settings in `settings.py`, overridden by `settings_local.py`
- Key settings modules: `basic.py`, `email.py`, `minrep.py` (minimum reputation), `repgain.py` (reputation gains), `voting.py`

### Actions System

All user interactions are tracked as Action objects (`forum/actions/`):
- Actions trigger reputation changes, notifications, and badges
- Actions can be canceled/undone
- Actions update node states (e.g., accepted answers, deleted content)

## Development Commands

### Setup and Installation

1. Copy and configure settings:
```bash
cp settings_local.py.dist settings_local.py
# Edit settings_local.py with database credentials and APP_URL
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Database setup:
```bash
python manage.py migrate
```

### Running the Application

Development server:
```bash
python manage.py runserver
```

Production: `gunicorn wsgi:application` (see `wsgi.py`, `Dockerfile`), or mod_wsgi with `osqa.wsgi.dist`

### Database Migrations

Django migrations live in `forum/migrations/`. After changing models run
`python manage.py makemigrations forum`; CI fails if a migration is missing.
Action subclasses are proxy models on the single `forum_action` table.

### Management Commands

Email alerts (used by cron):
```bash
python manage.py send_email_alerts
```

## Key Configuration Files

- `settings.py`: Core Django settings with module discovery
- `settings_local.py`: Local overrides (database, APP_URL, DEBUG, caching)
- `urls.py`: Root URL configuration (delegates to `forum/urls.py`)
- `forum/urls.py`: Main application URL routing
- `requirements.txt`: Python dependencies

## Important Implementation Details

### Node Type System

When querying nodes, use the manager methods to get the correct subclass:
- `Node.objects.get(id=x)` returns the leaf (Question, Answer, or Comment)
- Each subclass has its own manager that filters by `node_type`
- Parent/child relationships are maintained through the `parent` field

### Template System

- Skins are in `forum/skins/` (default skin is configurable)
- Template loaders check: filesystem → app directories → module templates → skins
- Django template engine with custom tags in `forum/templatetags/`; a custom `Node.render()` must return a string
- The default skin is responsive: `media/style/modern.css` (CSS variables, flex layout, phone breakpoint at 640px) overrides the fixed-width legacy `style.css`; check new UI at 390px width as well as desktop, and in dark mode (colours come from the variables, redefined under `prefers-color-scheme: dark`)
- JavaScript runs on jQuery 3.7 (self-hosted in `media/vendor/`): use `.on()` delegation, `.prop()` for checked/disabled and `.val()` for values; `media/js/osqa.compat.js` only shims the bundled minified plugins; tag/user suggestions use `$.fn.osqaAutocomplete` (`osqa.autocomplete.js`, jQuery UI) and charts `osqaTimeChart()` (`osqa.charts.js`, Flot 4)

### Content features

- Post HTML is rendered once per revision (`Node.rendered()` → `forum.models.node.render_markdown()`, stored in `body`; the editor preview POSTs to `markdown_preview`, which uses the same function): fenced code (`normalize_fences`), then `forum/utils/mentions.py` links `@username`; `forum.utils.html.sanitize_html` (nh3) holds the tag/attribute allow-list; notifications read mention ids from `data-user` attributes, which the sanitizer never accepts from user HTML
- A question closed as a duplicate keeps `{'duplicate_of': id}` in `Node.extra` (`Question.duplicate_of`)
- `TagSynonym` maps tag names to tags; `TagNamesField` resolves synonyms when tags are entered
- Security: `forum/middleware/security.py` (`ClientIPMiddleware` first: real IP from `X-Forwarded-For` when `TRUSTED_PROXY_COUNT` > 0, `/healthz`; `SecurityHeadersMiddleware`: CSP + Permissions-Policy, inline scripts allowed, extra origins in the `CSP_EXTRA_SOURCES` setting); sign-in lockout and sign-in link limits in `forum/views/auth.py` (counters in `forum/utils/throttle.py`); hourly posting limits `antispam.posting_limit_reached()`; DB settings in `forum/settings/security.py`; `SetPasswordForm` runs `AUTH_PASSWORD_VALIDATORS`; `ValidationHash` is HMAC-SHA256 keyed with `SECRET_KEY`. Log security events with `forum.utils.security_log.event()` (logger `forum.security`), never passwords or tokens
- Sentry (`forum/utils/sentry.py`): `init_server()` from settings.py when `SENTRY_DSN` is set (no PII; loggers `forum.security` and `forum.errors` are kept out of Sentry because their lines carry IPs and usernames), `SentryUserMiddleware` adds the user id and sets the visitor for `before_send`, which drops events past `SENTRY_MAX_EVENTS_PER_VISITOR` per hour (the browser applies the same cap in `osqa.sentry.js`); `{% sentry_browser %}` (first in `<head>` of `base_content.html` and the admin base) loads the self-hosted bundle and `media/js/osqa.sentry.js` when `SENTRY_FRONTEND_DSN` is set, and the CSP `connect-src` gets the DSN's origin
- Translations: `forum/locale/{es,fr,pt}` (.po and compiled .mo committed), `LocaleMiddleware` + footer switcher (`set_language`); URL patterns use plain strings (never `_()`), strings evaluated at import time use `gettext_lazy as _lazy`; after changing texts run makemessages/compilemessages (README "Languages")
- In-site notifications: `Notification` rows created by hooks in `forum/notifications.py` (independent of email prefs), bell = `ui.Include('inbox/bell.html')` in the header registry, views in `forum/views/inbox.py`; support dashboard `admin.support_dashboard` (`/admin/support/`, staff) with figures in `forum/utils/support.py`, `manage.py remind_unaccepted` (one `QuestionReminder` per question)
- Bot defences live in `forum/utils/antispam.py`: `BotTrapMixin` (honeypot + signed time stamp; sign-up templates render `{{ form1.bot_traps }}`, tests post `form_token=self.signup_token()`), the per-IP sign-up limit, `links_trusted()` for profile links, and `dormant_users()` for `manage.py cleanup_inactive_users`; `User.last_seen` is updated hourly by the `ExtendedUser` middleware
- Agent access (`forum/views/agents.py`): `/questions/<id>.md`, `/llms.txt`, `/export/qa.jsonl` and a hand-written read-only MCP endpoint `/mcp` (JSON-RPC; tools `search_forum`, `get_question`); `forum.utils.similar.search_questions()` takes pluggable `SEARCH_PROVIDERS` (the AI module registers semantic search)
- AI knowledge and assistant: `KnowledgeChunk` (forum threads and crawled docs with float32 embeddings) built by `manage.py build_knowledge_index` (`forum_modules/ai/knowledge.py`, `crawler.py`); `assistant.answer_question()` answers only from retrieved chunks with `[n]` citations and returns 'insufficient' unless it cites a source and a second `ask_model` check finds every statement supported (no web search: nothing that can't be verified); `AISuggestion` rows (from `manage.py suggest_answers`) are shown via `{% ai_suggestion %}` on question pages and are never answers, indexed or exported
- `forum_modules/ai` uses Google Gemini (`google-genai`; Gemini API key or Vertex AI project): every call goes through `client.ask_model()` (JSON-schema output; returns None on any failure so callers degrade to no AI, or `BLOCKED` when the safety filters refuse); tests patch `forum_modules.ai.client.get_client`. Moderation hooks create `ModerationReport` rows (core model, resolved under Admin > Flagged posts) and never hide or delete posts; `forum/utils/similar.py` holds the keyword question matching used by duplicate detection and `related_questions`

### Caching

- `CACHES` must be shared by all worker processes (file cache by default, memcached recommended); `settings.py` translates an old `CACHE_BACKEND` string
- Models use `CachedManager` and `CachedQuerySet`; `get()` uses the per-object cache only for a bare pk lookup
- Cache keys are generated with `_generate_cache_key` method

### Authentication

Authentication providers are modules (`forum/authentication/` collects their `*AuthConsumer` / `*AuthContext` classes; names ending in `AbstractAuthConsumer` are skipped):
- `localauth`: Standard username/password
- `oauth2auth`: Google, GitHub and Facebook via the OAuth 2.0 authorization code flow; keys in Admin > External Keys, a provider is shown only when its context's `is_enabled()` is true
- `ldapauth`: LDAP integration (needs python-ldap)

### User Interface Registry

The `forum/registry.py` and `forum/modules/ui.py` system registers UI elements:
- Header links, footer links, user menu items
- Modules can extend the UI through the registry
- Visibility controls (anonymous, authenticated, superuser)

## Testing

Smoke tests live in `forum/tests/` and run against SQLite using `settings_local_test.py`:
```bash
OSQA_SETTINGS_LOCAL=settings_local_test python manage.py test forum.tests
```
- Always pass the `forum.tests` label.
- `forum.tests.base.OSQATestCase` clears the cache, runs action hooks inline
  and captures outgoing email in `self.sent_mail`.
- CI (`.github/workflows/tests.yml`) runs the suite and `makemigrations --check`.
- UI test command available: `python manage.py test_ui`

## Common Pitfalls

1. **Settings Import**: Always import from `forum import settings`, not `django.conf import settings`, to get OSQA's runtime-configurable settings

2. **Node Queries**: Remember to use `.leaf` or model-specific managers to get the correct subclass

3. **Module Conflicts**: Check `DISABLED_MODULES` in `settings_local.py` if modules aren't loading

4. **Cache Issues**: Clear cache directory during development if seeing stale data

5. **Database**: This codebase uses MySQL by default; other databases may require adjustments to full-text search modules

6. **Metaclasses**: Use `class X(Base, metaclass=M)`; a `__metaclass__` attribute is silently ignored by Python 3 and breaks node types, denormalized counts, action proxies and badges

7. **Command views**: `@decorate.withfn(command)` views only accept POST; mark a view `@prompt` if it renders a form on GET (and act only on POST). `media/js/osqa.csrf.js` sends the CSRF token from `<meta name="csrf-token">` with every AJAX request

8. **HTML in descriptions**: `forum.utils.html.hyperlink()`/`buildtag()` escape their text; escape any other user text interpolated into `Action.describe()` output, which templates render with `|safe`

9. **Sessions are JSON**: store only JSON-serializable values (e.g. `QueryDict.urlencode()`, ISO datetimes)

10. **Line endings**: several files (this one, `requirements.txt`, `settings_local.py.dist`, many templates) use CRLF; preserve them when editing
