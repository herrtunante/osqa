# OSQA Django Upgrade Plan
## From Django 1.6 → Django 4.2 LTS

### Current State
- Django 1.6.0 (released 2013)
- Python 2.7 syntax
- 288 Python files
- South migrations
- Old-style middleware, templates, URLs

### Target State
- Django 4.2 LTS (latest LTS, supported until April 2026)
- Python 3.12
- Modern Django patterns
- Django's built-in migrations

---

## Phase 1: Foundation (Critical)

### 1.1 Dependencies
- [ ] Update requirements.txt
  - Django 4.2.x
  - Remove South (replaced by Django migrations)
  - Update all dependencies to Python 3 compatible versions
  - markdown 3.x
  - html5lib latest
  - python3-openid
  - python-memcached → python-memcached or pymemcache

### 1.2 Python 2 → 3 Syntax
- [ ] Run and enhance fix_python3.py
  - except Exception, e → except Exception as e
  - print statements → print()
  - xrange → range
  - iteritems() → items()
  - dict.has_key() → key in dict
  - unicode() → str()
  - basestring → str
  - __unicode__ → __str__
  - filter(), map() returns (wrap with list() where needed)

### 1.3 Settings.py Major Changes
- [ ] MIDDLEWARE_CLASSES → MIDDLEWARE
- [ ] TEMPLATE_* settings → TEMPLATES dict
- [ ] Remove deprecated settings
- [ ] Add DEFAULT_AUTO_FIELD
- [ ] Update context processors paths
- [ ] Remove django.middleware.transaction (deprecated)

---

## Phase 2: Core Django Components

### 2.1 URL Patterns
- [ ] urls.py: Remove patterns()
- [ ] Update url() → path() or re_path()
- [ ] Update all forum/urls.py
- [ ] Fix namespace and app_name declarations

### 2.2 Models
- [ ] on_delete parameter required for ForeignKey
- [ ] Update Manager and QuerySet methods
- [ ] Fix model Meta options
- [ ] Update model form imports

### 2.3 Views
- [ ] Update render_to_response() → render()
- [ ] Fix direct_to_template → render
- [ ] Update class-based view imports
- [ ] Fix RequestContext usage

### 2.4 Forms
- [ ] Update form imports
- [ ] Fix form field changes
- [ ] Update form media handling

---

## Phase 3: Templates

### 3.1 Template Tags
- [ ] {% load url from future %} (remove)
- [ ] Update {% url %} tag syntax
- [ ] Fix custom template tags

### 3.2 Template Loaders
- [ ] Update TEMPLATE_LOADERS configuration
- [ ] Fix custom template loaders

---

## Phase 4: Database & Migrations

### 4.1 Remove South
- [ ] Document existing South migrations
- [ ] Create initial Django migration
- [ ] Test migration on copy of database

### 4.2 Model Changes
- [ ] Add on_delete to all ForeignKey fields
- [ ] Update any custom SQL

---

## Phase 5: Authentication & Sessions

- [ ] Update authentication backends
- [ ] Fix session serialization
- [ ] Update user model references
- [ ] Fix password hashers if needed

---

## Phase 6: Admin

- [ ] Update admin imports
- [ ] Fix admin configurations
- [ ] Test admin interface

---

## Phase 7: Middleware

- [ ] Update all custom middleware to new style
- [ ] Remove deprecated middleware
- [ ] Test middleware chain

---

## Phase 8: Third-Party Modules

### Forum Modules
- [ ] Update all modules in forum_modules/
- [ ] Fix module imports
- [ ] Test module loading system

---

## Phase 9: Testing & Validation

- [ ] Run Django system checks
- [ ] Fix deprecation warnings
- [ ] Run existing tests
- [ ] Manual testing of key features
- [ ] Performance testing

---

## Phase 10: Documentation

- [ ] Update README
- [ ] Update installation instructions
- [ ] Document breaking changes
- [ ] Create migration guide for users

---

## Critical Breaking Changes

### Django 1.6 → 1.7
- New migration system (replaced South)
- App loading refactor

### Django 1.7 → 1.8
- Multiple template engines support
- Model._meta API changes

### Django 1.8 → 1.9
- on_delete required for ForeignKey
- Removed patterns()

### Django 1.9 → 1.10
- MIDDLEWARE_CLASSES → MIDDLEWARE
- New-style middleware

### Django 1.10 → 1.11
- Template context processors changes
- render_to_response deprecated

### Django 2.0 → 2.1
- Python 2 support dropped
- path() and re_path() for URLs

### Django 2.2 → 3.0
- MIDDLEWARE fully replaces MIDDLEWARE_CLASSES
- Model field default on_delete behaviors

### Django 3.0 → 4.0
- CSRF protection changes
- Async support improvements

### Django 4.0 → 4.2
- DEFAULT_AUTO_FIELD required
- Various security improvements

---

## Estimated Timeline

- Phase 1-2: 2-3 days (Foundation + Core)
- Phase 3-4: 1-2 days (Templates + DB)
- Phase 5-7: 2-3 days (Auth + Admin + Middleware)
- Phase 8: 2-3 days (Modules)
- Phase 9-10: 1-2 days (Testing + Docs)

**Total: 8-13 days of focused development**

---

## Risk Areas

1. **South migrations** - Need careful conversion
2. **Custom middleware** - Major API changes
3. **Template loaders** - Custom skin system
4. **Module system** - Dynamic loading may break
5. **Database queries** - Some deprecated APIs
6. **File uploads** - Handler changes

---

## Testing Strategy

1. Unit tests for models
2. Integration tests for views
3. Manual testing of user flows
4. Performance benchmarks
5. Security audit

