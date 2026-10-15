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

When a test fails, a full-page screenshot of every page it had open is saved
in `tmp/e2e/`, named after the test.

## What is covered

| Module | Who | What |
|---|---|---|
| `test_public.py` | anonymous | Every public page on desktop and on a phone in dark mode; question content; voting asks to sign in; search; tag pages; Markdown/JSONL/llms.txt/RSS/sitemap/health endpoints; 404 page; language switcher |
| `test_accounts.py` | new and regular users | Sign-up (with the password rules), sign-out, sign-in; sign-in lockout after wrong passwords; forgotten password by email link; following a tag; email settings (master switch, checkboxes); unsubscribe link from an email, with undo; editing the profile; account pages on a phone; data download and account deletion |
| `test_posting.py` | askers, answerers, commenters | Asking with live preview; answering; commenting with an @mention (and the mention suggestions); voting; notifications bell and list; accepting an answer; favourites; following/unfollowing a question; editing a question; reporting a post; answering on a phone |
| `test_editing.py` | members | Ask form validation; image upload in the editor; editing an answer; deleting a comment |
| `test_emails.py` | members, administrators | Every email a member can get, checked for recipients, content and unsubscribe links: new comment and @mention, answer accepted (only to those who asked for it), new member, new question, daily digest; turning each preference off stops it; unsubscribe and undo for every kind; auto-following viewed or all questions; the "questions you follow" tab; welcome email validation link; changing an email address and validating the new one |
| `test_rules.py` | anonymous, newcomers, members, moderators | The rules behind the buttons, with the message shown and the score/reputation in the database: who may vote (sign-in prompt, reputation per direction), down votes and taking votes back, daily votes and the "votes left" warning, old votes; accepting and unaccepting; reporting (refusals, daily limit, the hide/delete counts); comment length, reputation and edit window; a moderator deleting a comment; award points refusals; retagging without edit rights |
| `test_content.py` | members, moderators | Revisions and rollback; permanent answer links; awarding points; liking and editing comments; community wiki (phone); converting comments, answers and questions; question and tag suggestions on the ask page; interesting/ignored tags; tag descriptions; posts written before signing in, published or discarded |
| `test_people.py` | members, administrators, agents | Profile tabs (votes, favourites, reputation, activity, preferences); reporting a user; awarding points (phone); badges; contact form; Markdown help; OpenSearch; llms.txt and the links in it; the MCP server; the notification bell's count and "mark all read"; email validation link (phone); the AI assistant and AI suggestions with a fake Gemini |
| `test_admin_tools.py` | administrators | Custom pages; statistics (phone); recalculating counts; bootstrap mode and defaults; resetting a setting; testing email settings; acting on moderation reports (phone); XML backup and download; the StackExchange importer; sign-up with GitHub, then linking and unlinking Google (provider calls mocked) |
| `test_accessibility.py` | anonymous, members, administrators | WCAG 2.0/2.1 A and AA (axe-core, vendored in `e2e/vendor/`) on the main pages, the user and post menus, the admin dashboard and a settings page, on a desktop and on a phone in dark mode. A failure lists each rule, its impact and the offending elements |
| `test_offline.py` | anonymous, members | Installable app: manifest, icons and service worker; a saved question read offline with the offline banner (desktop, phone in dark mode, axe-checked); the offline page listing saved pages; sign-out forgets them |
| `test_moderation.py` | members, moderators, administrators | Suspending for a day with public and private messages (shown escaped), signing in while suspended, withdrawing the suspension, a suspension that has ended, an indefinite one; closing with a reason (and an empty one refused), owners closing and reopening their own question; deleting and undeleting; bulk close/delete/undelete in Admin > Tools; granting and revoking moderator rights; converting a comment, and one's own answer, into a question; @mentions in a question and in an edit on the bell; the contact form for members; badge pages; the profile avatar (phone, dark) |
| `test_crawl.py` | every role | Every link reachable by anonymous visitors (desktop and phone), newcomers, members (phone), experts, moderators and administrators (desktop and phone) |
| `test_admin.py` | moderators, administrators, and ordinary users | Closing as a duplicate, reopening, deleting; flagged posts and the support dashboard; ordinary users and moderators kept out of what isn't theirs; **every settings page loads and saves**; changing a setting; admin pages in both admin layouts and on a phone; creating users; granting moderator rights; suspending; maintenance mode (closed to visitors, open to the allowed address); Django's model admin |

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
