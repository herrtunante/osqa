"""Which rules of the skin's style sheets the forum's pages use.

    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py               # runs the browser tests, then reports
    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py e2e.test_crawl  # only these tests
    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py --report      # report on the last run
    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py --report --prune      # also rewrite the files
    OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py --report --overridden # declarations later rules replace
    ... --file=default/media/style/osqa.css      # only this file (path under forum/skins), repeatable

E2E_CHROMIUM_PATH and the other e2e variables apply (e2e/README.md).

How a rule is judged:

1. **Used in a browser.** With E2E_CSS_COVERAGE set, every page the browser
   tests open (all roles, desktop and phone, light and dark, with the menus,
   dialogs, suggestions and errors the tests bring up) is watched twice:
   - Chromium's own CSS coverage (CDP ``CSS.startRuleUsageTracking``, taken
     at every load and at the end of the test) records the rules whose
     selectors matched while the page was styled;
   - a script in the page tests each selector, with user-action states
     (:hover, :focus, :checked...) and pseudo-elements stripped, against the
     document at load and after every change to the DOM, so a rule for a
     hover or focus state counts as used when its element was on a page.
2. **Referenced in the code.** Every class and id a selector needs (names
   inside :not(), :is(), :where() and :has() aren't required) is looked up
   among the words of the templates, JavaScript (including the vendored
   libraries, which build their own markup) and Python (template tags,
   forms, settings) under forum/ and forum_modules/; a name made at run time
   from a prefix (``'vote-' + kind``, ``icon-{{ name }}``, ```tag-${x}```)
   counts when its prefix appears that way. Selectors made only of elements
   and attributes are always kept (posts can contain any element), and so is
   everything inside @media print, @keyframes and @font-face.

A selector is **dead** when neither applies; ``--prune`` removes dead
selectors from their rules and rules left without selectors, then the
obsolete declarations no current browser reads (``*zoom`` IE hacks,
``filter: progid:...``, ``-moz-border-radius``, a prefixed declaration
followed in the same rule by its standard form). ``--overridden`` lists (and
with ``--prune`` removes) declarations that a later rule with the same
selector, in the same @media context and the same file, always replaces;
the replacing declaration must be standard and accepted by the browser
(``CSS.supports``), so that an older browser that drops it can't fall back.

Review the diff and run the visual tests (e2e.test_visual) after pruning.
The usage data of a run is kept in tmp/e2e/css-usage.jsonl.
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKINS = os.path.join(ROOT, 'forum', 'skins')
RECORD = os.path.join(ROOT, 'tmp', 'e2e', 'css-usage.jsonl')
SOURCE_DIRS = [os.path.join(ROOT, 'forum'), os.path.join(ROOT, 'forum_modules')]
SOURCE_EXTENSIONS = ('.html', '.txt', '.py', '.js', '.xml', '.json')
SKIPPED_DIRS = {'tests', 'locale', 'migrations', '__pycache__'}


def stylesheets():
    """The skin's own style sheets (vendored libraries left out), as paths relative to forum/skins."""
    found = []
    for folder, dirs, files in os.walk(SKINS):
        dirs[:] = sorted(d for d in dirs if d != 'vendor')
        found.extend(os.path.relpath(os.path.join(folder, f), SKINS) for f in sorted(files) if f.endswith('.css'))
    return found


# -- parsing -----------------------------------------------------------------

GROUP_AT_RULES = ('@media', '@supports', '@layer', '@container', '@document')


class Node:
    """A rule: kind 'style' (selectors + declarations), 'group' (@media...,
    with children), 'at' (@keyframes, @font-face: kept as they are) or
    'statement' (@import ...;). Offsets are into the file's text."""

    def __init__(self, kind, start, end, prelude_span, body_span, context):
        self.kind, self.start, self.end = kind, start, end
        self.prelude_span, self.body_span, self.context = prelude_span, body_span, context
        self.children, self.selectors = [], []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def _skip_space(text, i, end):
    while i < end:
        if text[i].isspace():
            i += 1
        elif text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = end if j < 0 else j + 2
        else:
            break
    return i


def _scan(text, i, end, stops):
    """Index of the first character in `stops` outside strings, comments and brackets."""
    depth = 0
    while i < end:
        c = text[i]
        if c in '"\'':
            j = i + 1
            while j < end and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            i = j + 1
            continue
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = end if j < 0 else j + 2
            continue
        if c in '([':
            depth += 1
        elif c in ')]':
            depth -= 1
        elif depth <= 0 and c in stops:
            return i
        i += 1
    return end


def _block_end(text, i, end):
    """Index just after the '}' matching the '{' at i."""
    depth = 0
    while i < end:
        c = text[i]
        if c in '"\'':
            j = i + 1
            while j < end and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            i = j + 1
            continue
        if text.startswith('/*', i):
            j = text.find('*/', i + 2)
            i = end if j < 0 else j + 2
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return end


def strip_comments(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def parse(text, start=0, end=None, context=()):
    end = len(text) if end is None else end
    nodes, i = [], start
    while True:
        i = _skip_space(text, i, end)
        if i >= end:
            return nodes
        stop = _scan(text, i, end, '{;}')
        if stop >= end or text[stop] in ';}':
            close = min(stop + 1, end)
            if text[i] == '@':
                nodes.append(Node('statement', i, close, (i, stop), None, context))
            i = close
            continue
        close = _block_end(text, stop, end)
        prelude = ' '.join(strip_comments(text[i:stop]).split())
        if prelude.lower().startswith(GROUP_AT_RULES):
            node = Node('group', i, close, (i, stop), (stop + 1, close - 1), context)
            node.children = parse(text, stop + 1, close - 1, context + (prelude,))
        elif prelude.startswith('@'):
            node = Node('at', i, close, (i, stop), (stop + 1, close - 1), context)
        else:
            node = Node('style', i, close, (i, stop), (stop + 1, close - 1), context)
            j = i
            while j < stop:
                k = _scan(text, j, stop, ',')
                s = _skip_space(text, j, k)
                e = k
                while e > s and text[e - 1].isspace():
                    e -= 1
                if e > s:
                    node.selectors.append((' '.join(strip_comments(text[s:e]).split()), s, e))
                j = k + 1
        nodes.append(node)
        i = close


def declarations(text, node):
    """(property, value, important, start, end) for each declaration of a style rule."""
    found = []
    i, end = node.body_span
    while i < end:
        i = _skip_space(text, i, end)
        if i >= end:
            break
        k = _scan(text, i, end, ';')
        raw = strip_comments(text[i:k]).strip()
        if ':' in raw:
            prop, value = raw.split(':', 1)
            value = value.strip()
            important = bool(re.search(r'!\s*important\s*$', value, re.I))
            value = re.sub(r'\s*!\s*important\s*$', '', value, flags=re.I)
            found.append((prop.strip().lower(), value, important, i, min(k + 1, end)))
        i = k + 1
    return found


def all_style_rules(nodes):
    for node in nodes:
        for each in node.walk():
            if each.kind == 'style':
                yield each


# -- selectors -----------------------------------------------------------------

# States that come and go with what people do, and states set by forms.
DYNAMIC_PSEUDO = {'hover', 'focus', 'focus-visible', 'focus-within', 'active', 'visited', 'link', 'any-link',
                  'target', 'checked', 'indeterminate', 'invalid', 'valid', 'user-invalid', 'user-valid',
                  'placeholder-shown', 'autofill', 'open', 'popover-open', 'modal', 'fullscreen', 'default',
                  'required', 'optional', 'read-only', 'read-write', 'enabled', 'disabled', 'playing', 'paused'}


def _top_level_pseudos(selector):
    """(start, end, name, is_element) of each pseudo-class/element outside brackets."""
    found, i, depth = [], 0, 0
    while i < len(selector):
        c = selector[i]
        if c in '"\'':
            j = selector.find(c, i + 1)
            i = len(selector) if j < 0 else j + 1
            continue
        if c in '([':
            depth += 1
        elif c in ')]':
            depth -= 1
        elif c == ':' and depth == 0:
            element = selector.startswith('::', i)
            m = re.match(r':{1,2}(-?[\w-]+)', selector[i:])
            if m:
                j = i + m.end()
                if j < len(selector) and selector[j] == '(':
                    level, j = 1, j + 1
                    while j < len(selector) and level:
                        level += {'(': 1, ')': -1}.get(selector[j], 0)
                        j += 1
                found.append((i, j, m.group(1).lower(), element))
                i = j
                continue
        i += 1
    return found


def stripped(selector):
    """The selector without user-action states and pseudo-elements, for querySelector()."""
    out, last = [], 0
    for start, end, name, element in _top_level_pseudos(selector):
        if element or name in DYNAMIC_PSEUDO or name.startswith('-'):
            out.append(selector[last:start])
            if (start == 0 or selector[start - 1] in ' >+~') and ''.join(out)[-1:] in ('', ' ', '>', '+', '~'):
                out.append('*')   # the state was the whole compound: ".x :focus > .y"
            last = end
    out.append(selector[last:])
    result = ' '.join(''.join(out).split())
    # A compound that was only a state (".menu > :hover") becomes "*".
    result = re.sub(r'([>+~])\s*(?=[>+~]|$)', r'\1 *', result)
    result = re.sub(r'^([>+~])', r'* \1', result)
    return result or '*'


def required_names(selector):
    """Classes and ids the selector needs on the page (not those inside functional pseudo-classes)."""
    text = selector
    for start, end, name, element in reversed(_top_level_pseudos(selector)):
        text = text[:start] + ' ' + text[end:]
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    return re.findall(r'[.#](-?[_a-zA-Z][\w-]*)', text)


# -- references in the code ----------------------------------------------------

def source_words():
    """(words, dynamic prefixes) found in templates, JavaScript and Python."""
    words, prefixes = set(), set()
    for top in SOURCE_DIRS:
        for folder, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
            for name in files:
                if not name.endswith(SOURCE_EXTENSIONS):
                    continue
                try:
                    with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as f:
                        text = f.read()
                except OSError:
                    continue
                words.update(re.findall(r'[A-Za-z0-9_-]+', text))
                prefixes.update(re.findall(r'([A-Za-z][\w-]*[-_])(?=[\'"{%+$`])', text))
    return words, prefixes


# Classes that Django's forms and widgets and highlight.js (".hljs-title.class_")
# put in the markup, from code outside forum/.
GENERATED = {'errorlist', 'nonfield', 'nonform', 'helptext', 'required', 'class_', 'function_', 'language_',
             'inherited__'}


def is_referenced(name, words, prefixes):
    if name in words or name in GENERATED:
        return True
    return any(name.startswith(p) for p in prefixes if len(p) > 2)


# -- recording in the browser tests (called from e2e/base.py) -------------------

_PAGE_SCRIPT = '''(() => {
    const selectors = %s, seen = new Set();
    function check() {
        const found = [];
        selectors.forEach((s, i) => {
            if (seen.has(i)) return;
            try { if (document.querySelector(s)) { seen.add(i); found.push(i); } } catch (e) { seen.add(i); }
        });
        if (found.length && window.__osqaCssSeen) window.__osqaCssSeen(found);
    }
    let timer = null;
    function later() { if (!timer) timer = setTimeout(() => { timer = null; check(); }, 150); }
    document.addEventListener('DOMContentLoaded', () => {
        check();
        new MutationObserver(later).observe(document.documentElement, {subtree: true, childList: true, attributes: true});
    });
    window.addEventListener('load', later);
})();'''

_selector_list = None


def _all_stripped_selectors():
    global _selector_list
    if _selector_list is None:
        found = set()
        for rel in stylesheets():
            with open(os.path.join(SKINS, rel), encoding='utf-8') as f:
                text = f.read()
            for rule in all_style_rules(parse(text)):
                found.update(stripped(s) for s, _, _ in rule.selectors)
        _selector_list = sorted(found)
    return _selector_list


def _skin_file(url, live_server_url):
    """'http://host/m/default/media/style/x.css?v=..' -> 'default/media/style/x.css'."""
    if not url.startswith(live_server_url + '/m/'):
        return None
    path = url[len(live_server_url) + 3:].split('?')[0].split('#')[0]
    if not path.endswith('.css') or '/vendor/' in path or not os.path.isfile(os.path.join(SKINS, path)):
        return None   # Django's admin style sheets (/m/django_admin/), vendored libraries
    return path


def track(page, live_server_url):
    """Starts recording which rules `page` uses."""
    selectors = _all_stripped_selectors()
    page.css_seen, page.css_used, page.css_sheets, page.css_versions = set(), defaultdict(set), {}, {}
    page.expose_binding('__osqaCssSeen', lambda source, found: page.css_seen.update(selectors[i] for i in found))
    page.add_init_script(_PAGE_SCRIPT % json.dumps(selectors))
    cdp = page.context.new_cdp_session(page)
    page.css_cdp = cdp

    def added(event):
        header = event.get('header', {})
        rel = _skin_file(header.get('sourceURL', ''), live_server_url)
        if rel:
            page.css_sheets[header['styleSheetId']] = rel
            version = re.search(r'[?&]v=([0-9a-f]+)', header['sourceURL'])
            page.css_versions[rel] = version.group(1) if version else None
    cdp.on('CSS.styleSheetAdded', added)
    cdp.send('DOM.enable')
    cdp.send('CSS.enable')
    cdp.send('CSS.startRuleUsageTracking')
    # Chromium forgets a document's usage when the next one replaces it: take
    # what was used at each load and when the next page is requested. Event
    # handlers mustn't wait for Playwright calls, so these requests are queued
    # on Playwright's event loop and their answers recorded when they come.
    page.on('load', lambda _: _take_soon(page))
    page.on('request', lambda request: _take_soon(page) if request.is_navigation_request()
            and request.frame == page.main_frame else None)


def _record_usage(page, usage):
    for rule in usage:
        rel = page.css_sheets.get(rule['styleSheetId'])
        if rel and rule.get('used'):
            page.css_used[rel].add((int(rule['startOffset']), int(rule['endOffset'])))


def _take_soon(page):
    def done(task):
        if not task.cancelled() and task.exception() is None:
            _record_usage(page, (task.result() or {}).get('coverage', []))
    try:
        task = page._loop.create_task(page.css_cdp._impl_obj.send('CSS.takeCoverageDelta', None))
        task.add_done_callback(done)
    except Exception:
        pass


def _take(page):
    try:
        _record_usage(page, page.css_cdp.send('CSS.takeCoverageDelta').get('coverage', []))
    except Exception:
        pass


def flush(pages):
    """Appends what `pages` used to the record (before their contexts close)."""
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with open(RECORD, 'a') as record:
        for page in pages:
            if not hasattr(page, 'css_used'):
                continue
            if not page.is_closed():
                try:
                    page.evaluate('() => new Promise(r => setTimeout(r, 200))')  # pending DOM checks
                except Exception:
                    pass
                _take(page)
            record.write(json.dumps({'used': {k: sorted(v) for k, v in page.css_used.items()},
                                     'versions': page.css_versions, 'seen': sorted(page.css_seen)}) + '\n')


# -- report ---------------------------------------------------------------------

def load_record():
    """(used ranges per file, stripped selectors seen, version token per file) of the last run."""
    used, seen, versions = defaultdict(set), set(), {}
    for line in open(RECORD) if os.path.exists(RECORD) else []:
        data = json.loads(line)
        for rel, ranges in data['used'].items():
            used[rel].update(tuple(r) for r in ranges)
        versions.update(data.get('versions', {}))
        seen.update(data['seen'])
    return used, seen, versions


def content_token(path):
    """The ?v= token {% media %} gives the file (forum/utils/static_files.py)."""
    import hashlib
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _kept_context(node):
    return any('print' in c.lower() and c.lower().startswith('@media') for c in node.context)


def judge(text, rel, used, seen, words, prefixes):
    """{rule node: [(selector, start, end, verdict)]}, verdict 'used', 'referenced' or 'dead'."""
    starts = sorted(s for s, e in used.get(rel, ()))
    import bisect
    verdicts = {}
    for rule in all_style_rules(parse(text)):
        i = bisect.bisect_left(starts, rule.start)
        covered = i < len(starts) and starts[i] < rule.end
        result = []
        for selector, s, e in rule.selectors:
            # Chromium's coverage is per rule: it vouches for a selector only
            # when the rule has no other.
            if stripped(selector) in seen or (covered and len(rule.selectors) == 1):
                verdict = 'used'
            elif _kept_context(rule) or all(is_referenced(n, words, prefixes) for n in required_names(selector)):
                verdict = 'referenced'
            else:
                verdict = 'dead'
            result.append((selector, s, e, verdict))
        verdicts[rule] = result
    return verdicts


def _line_span(text, start, end):
    """Widens [start, end) to whole lines when nothing else is on them."""
    s = start
    while s > 0 and text[s - 1] in ' \t':
        s -= 1
    if s == 0 or text[s - 1] == '\n':
        e = end
        while e < len(text) and text[e] in ' \t':
            e += 1
        if e < len(text) and text[e] == '\r':
            e += 1
        if e < len(text) and text[e] == '\n':
            return s, e + 1
    return start, end


def _apply(text, edits):
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def prune_dead(text, verdicts):
    """Removes dead selectors and rules (and @media blocks left empty)."""
    edits, removed = [], 0
    by_start = {rule.start: v for rule, v in verdicts.items()}

    def visit(nodes):
        nonlocal removed
        alive_any = False
        for node in nodes:
            if node.kind == 'group':
                if visit(node.children):
                    alive_any = True
                else:
                    edits[:] = [e for e in edits if not (node.start <= e[0] < node.end)]
                    edits.append(_line_span(text, node.start, node.end) + ('',))
                continue
            if node.kind != 'style':
                alive_any = True
                continue
            verdict = by_start[node.start]
            kept = [v for v in verdict if v[3] != 'dead']
            removed += len(verdict) - len(kept)
            if not kept:
                edits.append(_line_span(text, node.start, node.end) + ('',))
                continue
            alive_any = True
            if len(kept) < len(verdict):
                separator = ',\n' if '\n' in text[node.prelude_span[0]:node.prelude_span[1]] else ', '
                first, last = verdict[0][1], verdict[-1][2]
                edits.append((first, last, separator.join(text[s:e] for _, s, e, _ in kept)))
        return alive_any

    visit(parse(text))
    return _apply(text, edits), removed


OBSOLETE_PROPERTY = re.compile(r'^([*_]|-ms-filter$|filter$|-moz-border-radius|-moz-box-shadow|-khtml-|-o-)')
PREFIX = re.compile(r'^-(webkit|moz|ms|o|khtml)-')
# Prefixed forms every current browser has replaced by the standard property
# (not -webkit-text-size-adjust, -webkit-user-select, -webkit-mask... which
# Safari or Chrome still need).
LEGACY_PREFIXED = ('border-radius', 'border-top-', 'border-bottom-', 'box-shadow', 'box-sizing', 'transition',
                   'transform', 'animation', 'background-size', 'opacity')


def obsolete_declarations(text, supports):
    """Declarations no current browser reads, as (start, end, description)."""
    found = []
    for rule in all_style_rules(parse(text)):
        decls = declarations(text, rule)
        for index, (prop, value, important, s, e) in enumerate(decls):
            reason = None
            if OBSOLETE_PROPERTY.match(prop) and not (prop == 'filter' and 'progid' not in value.lower()
                                                      and 'alpha(' not in value.lower()):
                reason = 'obsolete'
            elif (PREFIX.match(prop) and PREFIX.sub('', prop).startswith(LEGACY_PREFIXED)) or \
                    re.search(r'-(moz|webkit|o|ms)-[\w-]*gradient', value):
                standard = PREFIX.sub('', prop)
                later = [d for d in decls[index + 1:] if d[0] == standard and not PREFIX.match(d[0])
                         and not re.search(r'-(webkit|moz|ms|o|khtml)-', d[1])]
                if later and (not important or later[0][2]) and supports(standard, later[0][1]):
                    reason = 'prefixed, standard form follows'
            if reason:
                found.append((s, e, '%s: %s (%s)' % (prop, value, reason)))
    return found


SHORTHAND_EXCEPTIONS = {'border-collapse', 'border-spacing', 'flex-direction', 'flex-wrap', 'flex-flow',
                        'overflow-wrap', 'outline-offset', 'font-feature-settings', 'font-variation-settings',
                        'background-blend-mode', 'text-decoration-skip-ink', 'list-style-image'}
SHORTHANDS = ('margin', 'padding', 'border', 'background', 'font', 'list-style', 'outline', 'overflow', 'flex',
              'text-decoration', 'transition', 'animation', 'border-radius', 'inset', 'gap', 'columns', 'grid-area')


def _covers(later, earlier):
    if later == earlier:
        return True
    if earlier in SHORTHAND_EXCEPTIONS or later not in SHORTHANDS:
        return False
    if later == 'border' and earlier.startswith('border-radius'):
        return False
    return earlier.startswith(later + '-')


def overridden_declarations(text, supports):
    """Declarations that a later rule with the same selector and @media context
    always replaces, as (start, end, description). `supports(prop, value)` says
    whether the browser accepts the replacing declaration."""
    rules = list(all_style_rules(parse(text)))
    decls = {rule.start: declarations(text, rule) for rule in rules}
    later_by_key = defaultdict(list)    # (context, selector) -> [(rule start, prop, important, value)]
    for rule in rules:
        for selector, _, _ in rule.selectors:
            for prop, value, important, s, e in decls[rule.start]:
                if PREFIX.match(prop) or re.search(r'-(webkit|moz|ms|o|khtml)-', value):
                    continue
                later_by_key[(rule.context, selector)].append((rule.start, prop, important, value))
    found = []
    for rule in rules:
        for prop, value, important, s, e in decls[rule.start]:
            winners = []
            for selector, _, _ in rule.selectors:
                winner = next((d for d in later_by_key[(rule.context, selector)]
                               if d[0] > rule.start and _covers(d[1], prop) and (d[2] or not important)
                               and supports(d[1], d[3])), None)
                if winner is None:
                    break
                winners.append(winner)
            if winners and len(winners) == len(rule.selectors):
                found.append((s, e, '%s { %s: %s } replaced by %s: %s' % (
                    ', '.join(x[0] for x in rule.selectors), prop, value, winners[0][1], winners[0][3])))
    return found


def remove_declarations(text, spans):
    """Removes declarations, and style rules left with nothing in them."""
    text = _apply(text, [_line_span(text, s, e) + ('',) for s, e in spans])
    empty = [rule for rule in all_style_rules(parse(text))
             if not strip_comments(text[rule.body_span[0]:rule.body_span[1]]).strip()]
    return _apply(text, [_line_span(text, r.start, r.end) + ('',) for r in empty]), len(empty)


def browser_supports():
    """supports(prop, value) answered by Chromium's CSS.supports(), cached."""
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    options = {'executable_path': os.environ['E2E_CHROMIUM_PATH']} if os.environ.get('E2E_CHROMIUM_PATH') else {}
    page = playwright.chromium.launch(**options).new_page()
    cache = {}

    def supports(prop, value):
        if (prop, value) not in cache:
            cache[(prop, value)] = prop.startswith('--') or page.evaluate('([p, v]) => CSS.supports(p, v)', [prop, value])
        return cache[(prop, value)]
    return supports


def report(prune=False, overridden=False, files=None):
    """Prints the verdicts per style sheet; with `prune`, rewrites the files.

    Dead selectors are only judged (and pruned) in files the recorded run
    loaded in their current version: the coverage offsets of an older version
    don't fit, and a file no page loads is better deleted as a whole."""
    used, seen, versions = load_record()
    if not used and not seen:
        print('No usage recorded in %s: run the browser tests through this script first.' % RECORD)
        return 1
    words, prefixes = source_words()
    supports = browser_supports()
    print('\n%-42s %6s %6s %6s %6s %8s' % ('Style sheet', 'rules', 'used', 'refd', 'dead', 'bytes'))
    for rel in files or stylesheets():
        path = os.path.join(SKINS, rel)
        with open(path, encoding='utf-8', newline='') as f:
            text = f.read()
        current = rel in versions and versions[rel] in (None, content_token(path))
        verdicts = judge(text, rel, used, seen, words, prefixes)
        counts = defaultdict(int)
        for v in verdicts.values():
            for item in v:
                counts[item[3]] += 1
        note = '' if current else ('  (changed since the run: not judged)' if rel in versions
                                   else '  (no page loaded it)')
        print('%-42s %6d %6d %6d %6d %8d%s' % (rel, len(verdicts), counts['used'], counts['referenced'],
                                              counts['dead'] if current else 0, len(text.encode('utf-8')), note))
        if current:
            for selector in (item[0] for v in verdicts.values() for item in v if item[3] == 'dead'):
                print('    dead: ' + selector)
        obsolete = obsolete_declarations(text, supports)
        for _, _, description in obsolete:
            print('    obsolete: ' + description)
        if prune:
            if current:
                text, removed = prune_dead(text, verdicts)
            text, emptied = remove_declarations(text, [(s, e) for s, e, _ in obsolete_declarations(text, supports)])
        if overridden:
            replaced = overridden_declarations(text, supports)
            for _, _, description in replaced:
                print('    overridden: ' + description)
            if prune:
                text, more = remove_declarations(text, [(s, e) for s, e, _ in replaced])
        if prune:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
    return 0


def run_suite(labels):
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    if os.path.exists(RECORD):
        os.remove(RECORD)
    env = dict(os.environ, E2E_CSS_COVERAGE='1')
    return subprocess.call([sys.executable, 'manage.py', 'test'] + (labels or ['e2e']) + ['--noinput'],
                           cwd=ROOT, env=env)


if __name__ == '__main__':
    flags = {a.split('=')[0] for a in sys.argv[1:] if a.startswith('--')}
    only = [a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--file=')]
    labels = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--report' not in flags:
        run_suite(labels)   # failing tests still record what they saw
    sys.exit(report(prune='--prune' in flags, overridden='--overridden' in flags, files=only or None))
