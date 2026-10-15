"""Screenshots of every page each kind of user can reach, on a phone, for a
visual review (not run by default: set E2E_MOBILE_AUDIT=1).

    E2E_MOBILE_AUDIT=1 OSQA_SETTINGS_LOCAL=settings_local_e2e python manage.py test e2e.test_mobile_audit

Images go to tmp/mobile-audit/<role>-<page>-<light|dark>.png. Pages are
captured at rest: reduced motion, transitions and animations switched off,
fonts loaded, the network quiet, and a pause after switching the colour
scheme. Gravatar images are a local grey square; responses that aren't HTML
(feeds, text files) are skipped.
"""
import os
import re
import unittest

from . import test_crawl
from .test_crawl import SKIP, shape
from .visual import FREEZE_SCRIPT, placeholder_avatar

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'mobile-audit')
MAX_PAGES = 120


# The crawler class is reached through its module, not imported by name:
# a TestCase class in this module's namespace would be collected and run too.
@unittest.skipUnless(os.environ.get('E2E_MOBILE_AUDIT'), 'set E2E_MOBILE_AUDIT=1 to take the phone screenshots')
class MobileAuditTest(test_crawl.CrawlTest):
    def open_audit_page(self, user):
        page = self.open(user, device='mobile', reduced_motion='reduce')
        avatar, live = placeholder_avatar(), self.live_server_url

        def route(route):
            url = route.request.url
            if 'gravatar.com/' in url:
                return route.fulfill(status=200, content_type='image/png', body=avatar)
            return route.continue_() if url.startswith(live) else route.abort()
        page.context.route('**/*', route)
        page.context.add_init_script(FREEZE_SCRIPT)
        return page

    def settle(self, page):
        try:
            page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass  # a page that keeps polling is captured as it is
        page.evaluate('document.fonts.ready.then(() => true)')

    def shoot(self, user, role, seeds):
        os.makedirs(OUT, exist_ok=True)
        page = self.open_audit_page(user)
        queue, seen, done = list(seeds), set(), 0
        while queue and done < MAX_PAGES:
            path = queue.pop(0)
            if shape(path) in seen:
                continue
            seen.add(shape(path))
            try:
                response = page.goto(self.url(path))
                page.wait_for_load_state('load')
            except Exception:
                page = self.open_audit_page(user)
                continue
            page.problems.clear()
            if not response or response.status >= 400:
                continue
            if 'text/html' not in (response.headers.get('content-type') or ''):
                continue  # feeds, XML, plain text
            name = re.sub(r'[^a-z0-9]+', '-', shape(path).lower()).strip('-') or 'home'
            for scheme in ('light', 'dark'):
                page.emulate_media(color_scheme=scheme, reduced_motion='reduce')
                self.settle(page)
                page.wait_for_timeout(300)  # charts and scripts that follow the scheme
                page.screenshot(path=os.path.join(OUT, '%s-%s-%s.png' % (role, name[:60], scheme)), full_page=True)
            page.emulate_media(color_scheme='light', reduced_motion='reduce')
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

    def test_shoot_anonymous(self):
        self.shoot(None, 'anon', ['/'])

    def test_shoot_member(self):
        self.shoot(self.member, 'member', ['/', self.member.get_profile_url()])

    def test_shoot_administrator(self):
        self.shoot(self.admin, 'admin', ['/admin/', self.admin.get_profile_url()])


# The crawler's own tests aren't run again here (a None attribute isn't a test).
for _name in dir(test_crawl.CrawlTest):
    if _name.startswith('test_'):
        setattr(MobileAuditTest, _name, None)
