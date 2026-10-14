"""Follows every link each kind of user can see, and checks every page.

For anonymous visitors, new members, experienced members, moderators and
administrators, the crawler starts at the pages they would start from and
follows each same-site link it finds. Pages of the same kind (another
question, another user) are visited once. A page fails when it answers with
an error status or a server error, throws a JavaScript error, logs a console
error, or (on the phone) scrolls sideways. All failures are reported
together, with the page that linked to them.
"""
import re
from collections import deque

from forum.actions import AcceptAnswerAction, AwardAction, CloseAction, FlagAction, VoteUpAction
from forum.models import Badge, TagSynonym, Tag

from .base import E2ETestCase

# Links that sign out or change something when followed, and files that aren't pages.
SKIP = re.compile(r'/logout/|/account/signout/|/switch_interface/|/i18n/setlang/|^/m/|^/upfiles/|'
                  r'/notifications/\d+/|/unsubscribe/|/account/tempsignin/\d+/|/validate/|'
                  r'/export/qa\.jsonl|\.md$|/llms\.txt|/feeds/|/sitemap|/opensearch|/healthz|/mcp')
MAX_PAGES = 400


def shape(path):
    """'/questions/12/some-title?page=2' -> '/questions/N/S?page' (one page per shape)."""
    path, _, query = path.partition('?')
    parts = []
    for part in path.split('/'):
        parts.append('N' if part.isdigit() else part)
    path = '/'.join(parts)
    # Slugs after an id (questions, users, tags) are all the same page.
    path = re.sub(r'/N/[^/]+', '/N/S', path)
    path = re.sub(r'^/tags/[^/]+/', '/tags/T/', path)
    keys = sorted(k.split('=')[0] for k in query.split('&') if k)
    return path + ('?' + '&'.join(keys) if keys else '')


class CrawlTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin', reputation=5000, is_superuser=True, is_staff=True)
        self.moderator = self.create_user('moderator', reputation=200, is_staff=True)
        self.expert = self.create_user('expert', reputation=5000)
        self.member = self.create_user('member', reputation=20)
        self.newcomer = self.create_user('newcomer', reputation=1)
        self.build_forum()

    def build_forum(self):
        """Enough content that every kind of page has something to show."""
        q1 = self.create_question(self.member, 'How do I export Collect survey data to CSV?', tags='collect export')
        a1 = self.create_answer(self.expert, q1, 'Use **Export > CSV** in the designer.\n\n```\nFile > Export\n```')
        self.create_answer(self.moderator, q1, 'Another way: the command line exporter.')
        self.create_comment(self.member, a1, 'Thanks @expert, that worked.')
        self.create_comment(self.expert, q1, 'Which version of Collect do you use?')
        q2 = self.create_question(self.expert, 'Can SEPAL process Sentinel-2 time series?', tags='sepal sentinel-2')
        q3 = self.create_question(self.newcomer, 'Cheap watches, buy now', tags='spam')
        self._as_request(self.expert)
        VoteUpAction(user=self.expert, node=q1, ip='127.0.0.1').save()
        AcceptAnswerAction(user=self.member, node=a1, ip='127.0.0.1').save()
        self._as_request(self.moderator)
        CloseAction(user=self.moderator, node=q3, extra='spam', ip='127.0.0.1').save()
        FlagAction(user=self.expert, node=q2, extra='needs details', ip='127.0.0.1').save()
        TagSynonym.objects.create(source_name='csv', target=Tag.objects.get(name='export'), created_by=self.admin)
        badge = Badge.objects.order_by('id').first()
        if badge:
            self._as_request(self.member)
            AwardAction(user=self.member, node=q1).save(data=dict(badge=badge, trigger=None))
        self.questions = [q1, q2, q3]

    def crawl(self, user, device='desktop', dark=False, seeds=('/',)):
        page = self.open(user, device=device, dark=dark)
        queue = deque((seed, 'start') for seed in seeds)
        seen_shapes, visited, failures = set(), 0, []
        while queue and visited < MAX_PAGES:
            path, referrer = queue.popleft()
            if shape(path) in seen_shapes:
                continue
            seen_shapes.add(shape(path))
            visited += 1
            try:
                response = page.goto(self.url(path))
                page.wait_for_load_state('load')
                status = response.status if response else 0
                if status >= 400:
                    failures.append('%s answered %d (linked from %s)' % (path, status, referrer))
                    page.problems.clear()
                    continue
                self.check_page(page, path)
            except AssertionError as error:
                failures.append('%s: %s (linked from %s)' % (path, str(error).split('\n')[0][:300], referrer))
                page.problems.clear()
                continue
            except Exception as error:  # navigation errors
                failures.append('%s: %s (linked from %s)' % (path, str(error).split('\n')[0][:200], referrer))
                page.close()
                page = self.open(user, device=device, dark=dark)
                continue
            current = page.url[len(self.live_server_url):]
            # AJAX command links (vote, delete, flag…) are run by JavaScript as POST
            # requests; the workflow tests click them. The custom page form previews
            # the address of the page being written, which doesn't exist yet.
            for href in page.eval_on_selector_all('a[href]:not(.ajax-command):not(.url_field_anchor)', 'els => els.map(e => e.getAttribute("href"))'):
                href = (href or '').strip()
                if href.startswith(self.live_server_url):
                    href = href[len(self.live_server_url):]
                if href.startswith('http://testserver'):  # APP_URL in the test settings
                    href = href[len('http://testserver'):]
                if not href.startswith('/') or href.startswith('//') or SKIP.search(href):
                    continue
                href = href.split('#')[0]
                if href and shape(href) not in seen_shapes:
                    queue.append((href, current))
        return visited, failures

    def assert_crawl(self, user, **kwargs):
        visited, failures = self.crawl(user, **kwargs)
        self.assertGreater(visited, 10)
        self.assertEqual(failures, [], '%d of %d pages failed for %s:\n  %s' % (
            len(failures), visited, user.username if user else 'anonymous', '\n  '.join(failures)))

    def test_anonymous(self):
        self.assert_crawl(None)

    def test_anonymous_on_a_phone(self):
        self.assert_crawl(None, device='mobile', dark=True)

    def test_newcomer(self):
        self.assert_crawl(self.newcomer, seeds=('/', self.newcomer.get_profile_url()))

    def test_member_on_a_phone(self):
        self.assert_crawl(self.member, device='mobile', seeds=('/', self.member.get_profile_url()))

    def test_expert(self):
        self.assert_crawl(self.expert, seeds=('/', self.expert.get_profile_url()))

    def test_moderator(self):
        self.assert_crawl(self.moderator, seeds=('/', '/admin/support/', '/admin/flagged_posts/',
                                                 self.moderator.get_profile_url()))

    def test_administrator(self):
        self.assert_crawl(self.admin, seeds=('/', '/admin/', self.admin.get_profile_url()))

    def test_administrator_on_a_phone(self):
        self.assert_crawl(self.admin, device='mobile', dark=True, seeds=('/admin/',))
