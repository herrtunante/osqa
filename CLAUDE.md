# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OSQA is an open source Q&A system. It provides Stack Overflow-like functionality including questions, answers, comments, voting, badges, reputation points, and user moderation.

It runs on Django 5.2 LTS and Python 3.11+; it was ported from Django 1.6 / Python 2 (notes in `docs/upgrade-history/`). See `README.md` for setup and deployment.

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
- Available modules: authentication providers (LDAP, OAuth, OpenID, Facebook), full-text search (MySQL, PostgreSQL, Sphinx), badges, stats, exporter

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

### Caching

- `CACHES` must be shared by all worker processes (file cache by default, memcached recommended); `settings.py` translates an old `CACHE_BACKEND` string
- Models use `CachedManager` and `CachedQuerySet`; `get()` uses the per-object cache only for a bare pk lookup
- Cache keys are generated with `_generate_cache_key` method

### Authentication

Authentication providers are modules (only `localauth` is ported to Python 3 so far):
- `localauth`: Standard username/password
- `openidauth`: OpenID authentication
- `oauthauth`: OAuth (Twitter, Google, etc.)
- `facebookauth`: Facebook Connect
- `ldapauth`: LDAP integration

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
- Always pass the `forum.tests` label: unlabelled discovery also imports the
  vendored `httplib2` tests under `forum_modules/oauthauth/lib`, which hang.
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

7. **Command views**: `@decorate.withfn(command)` views only accept POST; mark a view `@prompt` if it renders a form on GET (and act only on POST). The JS sends the CSRF token from the page's `csrfmiddlewaretoken` input

8. **HTML in descriptions**: `forum.utils.html.hyperlink()`/`buildtag()` escape their text; escape any other user text interpolated into `Action.describe()` output, which templates render with `|safe`

9. **Sessions are JSON**: store only JSON-serializable values (e.g. `QueryDict.urlencode()`, ISO datetimes)

10. **Line endings**: several files (this one, `requirements.txt`, `settings_local.py.dist`, many templates) use CRLF; preserve them when editing
