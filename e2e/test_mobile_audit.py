"""Screenshots of every page each kind of user can reach, on a phone, for a
visual review (not run by default: set E2E_MOBILE_AUDIT=1).

    E2E_MOBILE_AUDIT=1 OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e.test_mobile_audit

Images go to tmp/mobile-audit/<role>-<page>-<light|dark>.png.
"""
import os
import re
import unittest

from .test_crawl import CrawlTest, SKIP, shape

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'mobile-audit')


@unittest.skipUnless(os.environ.get('E2E_MOBILE_AUDIT'), 'set E2E_MOBILE_AUDIT=1 to take the phone screenshots')
class MobileAuditTest(CrawlTest):
    def shoot(self, user, role, seeds):
        os.makedirs(OUT, exist_ok=True)
        page = self.open(user, device='mobile')
        queue, seen, done = list(seeds), set(), 0
        while queue and done < 120:
            path = queue.pop(0)
            if shape(path) in seen:
                continue
            seen.add(shape(path))
            try:
                response = page.goto(self.url(path))
                page.wait_for_load_state('load')
            except Exception:
                page = self.open(user, device='mobile')
                continue
            page.problems.clear()
            if not response or response.status >= 400:
                continue
            name = re.sub(r'[^a-z0-9]+', '-', shape(path).lower()).strip('-') or 'home'
            for scheme in ('light', 'dark'):
                page.emulate_media(color_scheme=scheme)
                page.screenshot(path=os.path.join(OUT, '%s-%s-%s.png' % (role, name[:60], scheme)), full_page=True)
            done += 1
            for href in page.eval_on_selector_all('a[href]:not(.ajax-command):not(.url_field_anchor)',
                                                  'els => els.map(e => e.getAttribute("href"))'):
                href = (href or '').strip()
                for prefix in (self.live_server_url, 'http://testserver'):
                    if href.startswith(prefix):
                        href = href[len(prefix):]
                if href.startswith('/') and not href.startswith('//') and not SKIP.search(href):
                    queue.append(href.split('#')[0])
        page.problems.clear()

    def test_anonymous(self):
        self.shoot(None, 'anon', ['/'])

    def test_member(self):
        self.shoot(self.member, 'member', ['/', self.member.get_profile_url()])

    def test_administrator(self):
        self.shoot(self.admin, 'admin', ['/admin/', self.admin.get_profile_url()])

    # The crawler's own tests aren't run again here.
    test_newcomer = test_member_on_a_phone = test_expert = test_moderator = None
    test_administrator_on_a_phone = test_anonymous_on_a_phone = None
