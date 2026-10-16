# Browser tests (Playwright)

These tests drive a real browser (Chromium) through the forum as each kind of
user would: anonymous visitors, new and regular members, moderators (staff)
and administrators (superusers), on a desktop screen and on a phone in dark
mode. They run against Django's live test server: a fresh test database, the
real middleware, templates, CSS and JavaScript, nothing mocked except outgoing
email, which is captured.

Every page a test opens is watched. A test fails when a page:
- answers with a server error (5xx);
- throws a JavaScript error;
- logs a console error, including content blocked by the Content-Security-Policy;
- scrolls sideways on the phone viewport. The failure names the elements that
  stick out.

Two kinds of test:
- **Workflow tests** do what people do (ask, answer, moderate, configure)
  and check the result in the database and on the page.
- **The crawler** (`test_crawl.py`) follows every link each kind of user can
  see, from the home page, their profile and the admin pages. It checks every
  page it reaches, once per kind of page, and reports all failures together,
  each with the page that linked to it.

## Running them

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium        # once; add --with-deps on a fresh Linux machine
OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e
```

The suite takes about five minutes. Run part of it by naming the module, class
or test:

```bash
OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e.test_posting
OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e.test_admin.AdministrationTest.test_maintenance_mode
```

`settings_local_e2e.py` uses the unit test settings with a file-based SQLite
test database (`tmp/test_e2e.db`). The live server answers the browser from
several threads, which can't share an in-memory database.

Options (environment variables):

| Variable | Effect |
|---|---|
| `E2E_HEADED=1` | Show the browser window |
| `E2E_SLOWMO=300` | Wait 300 ms between browser actions, to follow along |
| `E2E_TIMEOUT=20000` | Per-action timeout in ms (default 10000) |
| `E2E_CHROMIUM_PATH=/path/to/chrome` | Use this Chromium instead of Playwright's download |
| `E2E_ARTIFACTS=dir` | Where screenshots of failed tests go (default `tmp/e2e/`) |
| `E2E_DB_NAME=name.db` | Test database file in `tmp/`, so several runs can work side by side |
| `E2E_RECORD=file` | Append every forum URL the browser requests to this file (used by `coverage.py`) |
| `E2E_CSS_COVERAGE=1` | Record which style sheet rules each page uses, in `tmp/e2e/css-usage.jsonl` (used by `css_coverage.py`) |
| `E2E_UPDATE_BASELINES=1` | Visual tests: write the screenshots as the new baselines instead of comparing |
| `E2E_VISUAL_STRICT=1` | Visual tests: compare even when the baselines were made on another environment |

When a test fails, a full-page screenshot of every page it had open is saved
in `tmp/e2e/`, named after the test.

## What is covered

| Module | Who | What |
|---|---|---|
| `test_public.py` | anonymous | Every public page on desktop and on a phone in dark mode; question content; voting asks to sign in; search; tag pages; Markdown/JSONL/llms.txt/RSS/sitemap/health endpoints; 404 page; language switcher |
| `test_search.py` | anonymous | The header search box; results ranked (title matches first) with marked snippets; sort tabs; a `[tag]` operator; search tips; the empty state (phone, dark) with "without the filters" and "Ask a question" (title prefilled) |
| `test_searchbox.py` | anonymous, members | The header search box: "/" focuses it (not while typing elsewhere); live suggestions (questions, tags, people) from `/search/suggest/` as a combobox: arrow keys, Enter opens a question, Escape closes then leaves; the "Search for" row runs the full search; clicking a tag or a person; the clear button; full-width panel with large rows on a phone, light and dark (screenshots in `tmp/e2e/searchbox-*.png`) |
| `test_theme_and_shortcuts.py` | anonymous, members, administrators | The theme switch: System follows the emulated colour scheme (also when it changes); Light/Dark from the footer and the header menu apply without a reload, are right at the first paint after a reload (data-theme and background at DOMContentLoaded), persist by cookie and in a member's preferences (a fresh browser too); the admin follows; highlighted code and charts take the theme's colours. Keyboard shortcuts: the `?` dialog (axe, focus kept inside, Escape), `/`, `g` + letter, `a`, `j`/`k`/Enter on lists, `j`/`k`/`u` on a question page, nothing while typing or with modifiers, turning them off and on in the preferences |
| `test_accounts.py` | new and regular users | Sign-up (with the password rules), sign-out, sign-in; sign-in lockout after wrong passwords; forgotten password by email link; following a tag; email settings (master switch, checkboxes); unsubscribe link from an email, with undo; editing the profile; account pages on a phone; data download and account deletion |
| `test_signin_pages.py` | anonymous | Sign-in, sign-up and forgotten-password pages on a desktop and a phone in dark mode (screenshots in `tmp/e2e/signin-pages-*.png`); show-password button; error summary and inline errors on a bad sign-up, values kept; links between the pages; signing in with an email address; Google/GitHub as labelled buttons when configured |
| `test_posting.py` | askers, answerers, commenters | Asking with live preview; answering; commenting with an @mention (and the mention suggestions); voting; notifications bell and list; accepting an answer; favourites; following/unfollowing a question; editing a question; reporting a post; answering on a phone |
| `test_question_page.py` | askers, members, moderators | The question page: Copy button on code blocks (clipboard, keyboard, "Copied" announced); accepted answer badge and "Jump to accepted answer"; the asker's accept toggle; answer sort tabs; `<time>` elements with exact dates; edited link to revisions; the post menu from the keyboard and as a sheet on a phone; vote pressed state; the comment form and its counter; a question closed as a duplicate (screenshots in `tmp/e2e/question-page-*.png`) |
| `test_ask_page.py` | askers, editors | The ask and edit pages: tags as chips (Enter/comma/space, Backspace, remove buttons, suggestions, the submitted value); "Similar questions" with accepted/answered markers; Write/Preview tabs on a phone and the preview below the editor on a desktop; the draft restored and discarded; Ctrl+Enter and the leave warning (not after posting); the error summary with values kept; editing someone else's question (note, revision picker, edit summary) and one's own answer (screenshots in `tmp/e2e/ask-page-*.png`) |
| `test_editing.py` | members | Ask form validation; image upload in the editor; editing an answer; deleting a comment |
| `test_emails.py` | members, administrators | Every email a member can get, checked for recipients, content and unsubscribe links: new comment and @mention, answer accepted (only to those who asked for it), new member, new question, daily digest; turning each preference off stops it; unsubscribe and undo for every kind; auto-following viewed or all questions; the "questions you follow" tab; welcome email validation link; changing an email address and validating the new one |
| `test_rules.py` | anonymous, newcomers, members, moderators | The rules behind the buttons, with the message shown and the score/reputation in the database: who may vote (sign-in prompt, reputation per direction), down votes and taking votes back, daily votes and the "votes left" warning, old votes; accepting and unaccepting; reporting (refusals, daily limit, the hide/delete counts); comment length, reputation and edit window; a moderator deleting a comment; award points refusals; retagging without edit rights |
| `test_content.py` | members, moderators | Revisions and rollback; permanent answer links; awarding points; liking and editing comments; community wiki (phone); converting comments, answers and questions; question and tag suggestions on the ask page; interesting/ignored tags; tag descriptions; posts written before signing in, published or discarded |
| `test_people.py` | members, administrators, agents | Profile tabs (votes, favourites, reputation, activity, preferences); reporting a user; awarding points (phone); badges; contact form; Markdown help; OpenSearch; llms.txt and the links in it; the MCP server; the notification bell's count and "mark all read"; email validation link (phone); the AI assistant and AI suggestions with a fake Gemini |
| `test_admin_tools.py` | administrators | Custom pages; statistics (phone); recalculating counts; bootstrap mode and defaults; resetting a setting; testing email settings; acting on moderation reports (phone); XML backup and download; the StackExchange importer; sign-up with GitHub, then linking and unlinking Google (provider calls mocked) |
| `test_accessibility.py` | anonymous, members, administrators | WCAG 2.0/2.1 A and AA (axe-core, vendored in `e2e/vendor/`) on the main pages, the user and post menus, the header search suggestions, the admin dashboard, settings, support, statistics, custom page, email preview, create user and node manager pages, the admin menu open on a phone, and the moderation inbox (with the spam dialog open), on a desktop and on a phone in dark mode. A failure lists each rule, its impact and the offending elements |
| `test_offline.py` | anonymous, members | Installable app: manifest, icons and service worker; a saved question read offline with the offline banner (desktop, phone in dark mode, axe-checked); the offline page listing saved pages; sign-out forgets them |
| `test_moderation.py` | members, moderators, administrators | Suspending for a day with public and private messages (shown escaped), signing in while suspended, withdrawing the suspension, a suspension that has ended, an indefinite one; closing with a reason (and an empty one refused), owners closing and reopening their own question; deleting and undeleting; bulk close/delete/undelete in Admin > Tools; granting and revoking moderator rights; converting a comment, and one's own answer, into a question; @mentions in a question and in an edit on the bell; the contact form for members; badge pages; the profile avatar (phone, dark) |
| `test_crawl.py` | every role | Every link reachable by anonymous visitors (desktop and phone), newcomers, members (phone), experts, moderators and administrators (desktop and phone) |
| `test_ui_robustness.py` | anonymous, members, moderators | Reflow at 320px and at 1280px zoomed to 200% (no sideways scrolling, the main button on screen and not covered); tablet (768px) and wide (1920px) layouts (header parts don't overlap, sidebar beside or below the content); long content (200-character title without spaces, 60-character username, ten long tags, a huge table, an unbroken URL, long comments) stays inside its column on desktop and phone; keyboard only ("Skip to content" first, then the search box, the navigation and Ask, focus always visible; the close/delete/report dialogs keep focus inside, close on Escape and give it back); `prefers-reduced-motion` (no animation or transition, jQuery effects instant); the print style sheet of a question page |
| `test_visual.py` | anonymous, members, administrators | Visual regression: screenshots compared with the baselines in `visual/baselines/` (see below) |
| `test_review_queue.py` | newcomers, members, moderators | First posts review: a newcomer's answer held in "before" mode (notice, "Waiting for review" badge), hidden from other members and visitors, approved from the moderation inbox on a phone (the asker's email, the author's notice); a held question in lists and its 404 for others; "Delete as spam" with its dialog (cancel, suspend, other posts deleted) and undo; the header count, the inbox filters, j / k between cards, dismissing a flag; the old Flagged posts URL |
| `test_admin.py` | moderators, administrators, and ordinary users | Closing as a duplicate, reopening, deleting; flagged posts and the support dashboard; ordinary users and moderators kept out of what isn't theirs; **every settings page loads and saves**; changing a setting; the admin pages (header links, menu beside the page with the current page marked, folded above it on a phone), the old layout switch URL redirecting to the dashboard, the moderators' menu; creating users; granting moderator rights; suspending; maintenance mode (closed to visitors, open to the allowed address); Django's model admin |

## Visual regression tests

`test_visual.py` screenshots key pages and compares them with the PNGs in
`e2e/visual/baselines/`: home, question page (accepted answer, code,
comments), ask page, sign-in, sign-up with errors, search results with the
suggestions open, a profile, the tags index and the admin dashboard, each on a
desktop in light mode and on a phone in dark mode, plus a desktop in dark mode
for the home, question and sign-in pages. A screenshot fails when more than
0.2% of its pixels differ by more than 24 (of 255) in a colour channel; the
failure writes `<name>-actual.png`, `-expected.png` and `-diff.png` (differing
pixels in red) to `tmp/e2e/visual/`, which CI uploads with the other
screenshots.

The pages are made deterministic: fixed users and posts; every date in the
database moved to fixed times in March 2021 and "now" frozen for the relative
dates; question views not counted; animations, transitions, the caret and
jQuery effects off; Gravatar images replaced by a local grey square (every
other outside request is refused); screenshots wait for the network, fonts
and animations and stop at 1500px (desktop) or 2200px (phone, at device
scale 1 to keep the files small). Use `mask=` in `snapshot()` for anything
that can't be fixed.

**Updating the baselines.** After an intended visual change (a redesign, a new
element on one of these pages), regenerate them and review the new images
before committing:

```bash
E2E_UPDATE_BASELINES=1 OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e.test_visual
git diff --stat e2e/visual/baselines/      # look at each changed image
```

A screenshot without a baseline is skipped with a message, not failed. The
images depend on the machine that renders them (the fonts behind
`system-ui`, the Chromium version): `visual/baselines/environment.json`
records the environment that made them, and on a different one the
comparisons are skipped with a message (set `E2E_VISUAL_STRICT=1` to compare
anyway). To have CI compare them, generate the baselines in the same
environment as CI (e.g. a run of the CI job with `E2E_UPDATE_BASELINES=1`
that uploads `e2e/visual/baselines/` as an artifact) and commit those.

## Which URLs are covered

```bash
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/coverage.py            # runs the suite, then reports
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/coverage.py --report   # report on the last run
```

The report resolves every page, form and AJAX request the browser made to
the forum's URL patterns and lists those no test reached. Static files and
Django's model admin are left out. Requests made with `page.request` aren't
browser requests and don't count: reach an endpoint by clicking, by
`page.goto`, or with `fetch` inside the page. When you add a page or a
command, add a test that reaches it, and check the report.

## Which CSS is used

The public pages load one style sheet, `forum/skins/default/media/style/osqa.css`
(sections in cascade order: legacy base, modern layout and colour variables,
profile, ask, lists, search box, accessibility and print, editor, profile
menu, Markdown help), plus jQuery UI's; the sign-in pages add `auth.css`; the
admin pages (`osqaadmin/base.html`) use Django admin's style sheets plus `admin.css`.

```bash
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py                    # runs the suite, then reports
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py e2e.test_crawl     # only some tests
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py --report           # report on the last run
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py --report --prune   # remove dead rules
OSQA_SETTINGS_LOCAL=settings_local_e2e python e2e/css_coverage.py --report --overridden --file=default/media/style/osqa.css
```

With `E2E_CSS_COVERAGE=1` every page a test opens (all roles, desktop and
phone, light and dark, with the menus, dialogs, suggestions and errors the
tests bring up) is watched: Chromium's CSS coverage (CDP rule usage, taken at
each load and before each navigation) and a script that tests every selector,
with :hover/:focus-like states and pseudo-elements stripped, against the
document after each DOM change. The report lists per style sheet the rules
used, those only referenced, and the **dead** selectors: not used on any page
*and* naming a class or id that appears nowhere in the templates, JavaScript
(vendored libraries included) or Python of `forum/` and `forum_modules/`.
Selectors made only of elements, `@media print` and names built from a
prefix (`'vote-' + kind`) are kept. `--prune` removes dead selectors, rules
left empty and declarations no browser reads any more (IE hacks,
`-moz-border-radius`, a legacy prefix followed by its standard form);
`--overridden` lists (with `--prune`, removes) declarations that a later rule
with the same selector and `@media` context always replaces. A file changed
since the recorded run isn't judged (its offsets no longer fit). After
pruning, review the diff and run `e2e.test_visual`.

The unit tests (`forum/tests/`, run with `settings_local_test`) cover the
server side in more depth. These browser tests check that the pieces work
together in a real browser.

## Writing a test

```python
from .base import E2ETestCase

class MyTest(E2ETestCase):
    def test_something(self):
        alice = self.create_user('alice', reputation=100)        # also: is_staff=True, is_superuser=True
        question = self.create_question(alice, 'A question title', tags='collect')
        page = self.open(alice, device='mobile', dark=True)     # signed in; open() alone is anonymous
        self.visit(page, question.get_absolute_url())           # loads and checks the page
        self.click_command(page, '#post-%d-upvote' % question.id)  # AJAX command, then checks the page
        page.click('#node-%d-menu-trigger' % question.id)
        page.click('a[href^="/flag/"]')
        self.confirm_dialog(page, {'textarea[name=prompt]': 'spam'})  # commands that ask first
        self.check_page(page)                                   # after anything else you do
```

Other helpers:
- `create_answer`, `create_comment`: these go through the forum's actions, so
  notifications, emails and badges happen.
- `self.sent_mail` / `mail_to(user)`: the captured emails.
- `set_setting(settings.X, value)`: change a site setting for one test.
- `wait_for_ajax(page)`: wait for AJAX requests to finish.
- `settle(page)`: wait for AJAX, and for a page reload if the command
  triggered one.

Problems that a test expects, such as the 404 of a missing page, are
cleared with `page.problems.clear()`. Problems left unchecked fail the test
when it ends.
