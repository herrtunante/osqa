"""Accessibility: the main pages meet WCAG 2.0/2.1 levels A and AA, as
checked by axe-core (e2e/vendor/axe.min.js, used by the tests only), for
anonymous visitors, members and administrators, on a desktop in light mode
and on a phone in dark mode (the dark colours have their own contrast)."""
import os

from django.urls import reverse

from .base import E2ETestCase

AXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendor', 'axe.min.js')
WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']

# Parts of the page axe leaves out, each with the reason. Keep this short:
# fix the page instead whenever the markup is ours.
EXCLUDE = []

RUN_AXE = '''async ([tags, exclude]) => {
    const result = await axe.run({include: [document], exclude: exclude},
                                 {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']});
    return result.violations.map(v => ({
        id: v.id, impact: v.impact, help: v.help,
        // The selector, and the first reason axe gives (contrast figures, missing label...).
        nodes: v.nodes.map(n => n.target.join(' ') + ': ' + ((n.failureSummary || '').split('\\n')[1] || '').trim()),
    }));
}'''

FINISHED_ANIMATING = '''() => !(window.jQuery && jQuery(':animated').length) &&
    document.getAnimations().every(a => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)'''

VIEWS = (('desktop', False), ('mobile', True))


class AccessibilityTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', reputation=500)
        self.bob = self.create_user('bob', reputation=150)
        self.admin = self.create_user('admin', reputation=1000, is_staff=True, is_superuser=True)
        self.question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?',
                                             tags='collect export')
        self.answer = self.create_answer(self.bob, self.question,
                                         'Use the **CSV export** in the Collect designer:\n\n```\nFile > Export\n```')
        self.create_comment(self.alice, self.answer, 'Thanks, @bob, that worked for me.')
        self.create_comment(self.bob, self.question, 'Which version of Collect do you use?')
        self.create_question(self.bob, 'Can SEPAL process Sentinel-2 time series?', tags='sepal')

    def public_pages(self):
        return ['/', '/questions/', self.question.get_absolute_url(), '/questions/ask/', '/tags/', '/users/',
                self.alice.get_profile_url(), '/search/?q=export']

    def check_pages(self, user, paths, views=VIEWS):
        """Opens each page in each view and reports every violation together.
        A (path, selector) entry opens the page and clicks the selector (a menu) first."""
        failures = []
        for device, dark in views:
            page = self.open(user, device=device, dark=dark)
            for path in paths:
                path, click = path if isinstance(path, tuple) else (path, None)
                self.visit(page, path)
                if click:
                    page.click(click)
                    path = '%s, after clicking %s' % (path, click)
                failures += ['%s (%s%s):\n%s' % (path, device, ', dark' if dark else '', violation)
                             for violation in self.axe(page)]
        if failures:
            self.fail('%d accessibility violations (WCAG 2.1 A/AA, axe-core):\n\n%s' % (
                len(failures), '\n'.join(failures)))

    def axe(self, page):
        # Messages fade in: check the colours they end with, not a frame of the fade.
        page.wait_for_function(FINISHED_ANIMATING)
        if not page.evaluate('() => !!window.axe'):
            page.add_script_tag(path=AXE)
        # axe reads the page's style sheets itself (for css-orientation-lock)
        # and requests the targets of @import rules relative to the page
        # instead of to the style sheet (Django admin's forms.css imports
        # widgets.css): its own failed requests aren't the page's problems.
        fetched = []
        listener = lambda request: fetched.append(request.url) if request.resource_type in ('xhr', 'fetch') else None
        page.on('request', listener)
        try:
            violations = page.evaluate(RUN_AXE, [WCAG_TAGS, EXCLUDE])
        finally:
            page.remove_listener('request', listener)
        page.problems[:] = [p for p in page.problems if not any(url in p for url in fetched)]
        self.check_page(page)
        return ['  %s [%s] %s\n%s' % (v['id'], v['impact'], v['help'], ''.join(
            '    - %s\n' % node for node in v['nodes'][:5]) + (
            '    (+%d more)\n' % (len(v['nodes']) - 5) if len(v['nodes']) > 5 else ''))
            for v in violations]

    def test_anonymous_pages(self):
        self.check_pages(None, self.public_pages() + ['/account/signin/', '/account/local/register/'])

    def test_member_pages(self):
        self.check_pages(self.alice, self.public_pages() + [
            reverse('notifications'), self.alice.get_user_subscriptions_url(),
            (self.alice.get_profile_url(), '#user-menu'),
            (self.question.get_absolute_url(), '#node-%d-menu-trigger' % self.answer.id)])

    def test_administrator_pages(self):
        self.check_pages(self.admin, [self.question.get_absolute_url(), reverse('admin_index'),
                                      '/admin/settings/basic/'])
