"""Time charts (osqaTimeChart in media/js/osqa.charts.js): the reputation
graph on a profile and the admin statistics graphs. Points a few seconds
apart (a fresh site) must still give a readable time axis: dates or whole
hours, never fractional seconds, and few enough labels to fit a phone. The
reputation graph is the running total and ends at the reputation shown in
the profile header. Screenshots go to tmp/e2e/chart-fix-*.png."""
import os
import re

from django.urls import reverse

from forum.models import User

from .base import ARTIFACTS, E2ETestCase

CHART = '''sel => {
    const $el = jQuery(sel), plot = $el.data('plot');
    const labels = Array.from(document.querySelectorAll(sel + ' .flot-x-axis text, ' + sel + ' .flot-x-axis .flot-tick-label'))
        .map(e => e.textContent.trim()).filter(t => t);
    const data = plot.getData()[0].data;
    return {labels: labels, last: data[data.length - 1][1], points: data.length, width: plot.width(),
            ys: Array.from(document.querySelectorAll(sel + ' .flot-y-axis text, ' + sel + ' .flot-y-axis .flot-tick-label'))
                .map(e => e.textContent.trim()).filter(t => t)};
}'''


class TimeChartTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        from forum.actions import VoteUpAction
        # Reputation set directly (as an administrator or an import would)
        # plus changes a few seconds apart.
        self.alice = self.create_user('alice', reputation=30)
        self.bob = self.create_user('bob', reputation=500)
        self.admin = self.create_user('admin', reputation=1000, is_staff=True, is_superuser=True)
        question = self.create_question(self.alice, 'How do I export Collect survey data to CSV?')
        self.create_question(self.alice, 'Can SEPAL process Sentinel-2 time series?')
        self._as_request(self.bob)
        VoteUpAction(user=self.bob, node=question, ip='127.0.0.1').save()

    def check_axis(self, chart, where):
        self.assertTrue(chart['labels'], 'no time axis labels on %s' % where)
        for label in chart['labels']:
            self.assertNotRegex(label, r'0000|\d\.\d', 'fractional time label %r on %s' % (label, where))
        # About one label per 90px at most.
        self.assertLessEqual(len(chart['labels']), max(2, chart['width'] // 70), chart['labels'])
        for label in chart['ys']:
            self.assertRegex(label, r'^-?[\d,]+$', 'y axis label %r on %s' % (label, where))

    def shoot(self, page, selector, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        # The chart with some of the page around it.
        box = page.evaluate('''sel => {
            const r = document.querySelector(sel).getBoundingClientRect();
            return {y: r.top + window.scrollY, height: r.height};
        }''', selector)
        top = max(0, box['y'] - 120)
        page.screenshot(path=os.path.join(ARTIFACTS, 'chart-fix-%s.png' % name), full_page=True,
                        clip={'x': 0, 'y': top, 'width': page.viewport_size['width'],
                              'height': box['y'] - top + box['height'] + 60})

    def test_reputation_graph(self):
        alice = User.objects.get(id=self.alice.id)
        for device, dark in (('desktop', False), ('mobile', False), ('mobile', True)):
            page = self.open(self.bob, device=device, dark=dark)
            self.visit(page, reverse('user_reputation', kwargs={'id': alice.id, 'slug': alice.username}))
            page.wait_for_selector('#diagram canvas')
            chart = page.evaluate(CHART, '#diagram')
            self.check_axis(chart, 'the reputation graph (%s)' % device)
            self.assertEqual(chart['last'], alice.reputation)
            header = page.locator('.reputation-graph .profile-section-note strong').inner_text()
            self.assertEqual(int(re.sub(r'\D', '', header)), alice.reputation)
            if device == 'mobile':
                self.shoot(page, '#diagram', 'reputation-%s' % ('dark' if dark else 'light'))

    def test_admin_statistics(self):
        for dark in (False, True):
            page = self.open(self.admin, device='mobile', dark=dark)
            self.visit(page, reverse('admin_statistics'))
            for selector in ('#questions_graph', '#userss_graph'):
                page.wait_for_selector(selector + ' canvas')
                self.check_axis(page.evaluate(CHART, selector), 'admin %s' % selector)
            self.shoot(page, '#questions_graph', 'admin-statistics-%s' % ('dark' if dark else 'light'))
