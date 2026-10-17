"""The community health dashboard (/admin/, osqaadmin/health.html): what a
moderator sees (tiles, the daily chart drawn by osqaTimeChart with three
series, the bar charts and the heatmap), the period switch, the tables behind
every chart, refresh and the CSV download, on a phone in dark mode, and
axe-core's WCAG checks. Screenshots go to tmp/e2e/health-*.png."""
import csv
import io
import os

from django.urls import reverse

from forum.actions import AcceptAnswerAction, DeleteAction

from . import test_accessibility
from .base import ARTIFACTS, E2ETestCase


class HealthDashboardTest(E2ETestCase):
    # axe-core's WCAG 2.1 A/AA check, as in test_accessibility.py.
    axe = test_accessibility.AccessibilityTest.axe

    def setUp(self):
        super().setUp()
        self.moderator = self.create_user('moderator', reputation=200, is_staff=True)
        self.admin = self.create_user('admin', reputation=1000, is_staff=True, is_superuser=True)
        self.alice = self.create_user('alice', reputation=100)
        self.bob = self.create_user('bob', reputation=100)
        q1 = self.create_question(self.alice, 'How do I export Collect survey data to CSV?', tags='collect export')
        answer = self.create_answer(self.bob, q1)
        self._as_request(self.alice)
        AcceptAnswerAction(user=self.alice, node=answer, ip='127.0.0.1').save()
        self.create_question(self.alice, 'Can SEPAL process Sentinel-2 time series?', tags='sepal', days_ago=3)
        self.create_question(self.bob, 'An older question about Collect forms', tags='collect', days_ago=20)
        self.create_comment(self.bob, q1)
        deleted = self.create_question(self.bob, 'Cheap watches, buy now', tags='spam')
        self._as_request(self.moderator)
        DeleteAction(user=self.moderator, node=deleted, ip='127.0.0.1').save()

    def tile(self, page, key):
        return page.locator('#health-tile-%s .health-tile-value' % key).inner_text()

    def shoot(self, page, name):
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, 'health-%s.png' % name), full_page=True)

    def test_moderator_sees_tiles_and_charts(self):
        page = self.open(self.moderator)
        self.visit(page, '/')
        page.goto(self.url(reverse('admin_moderation')))
        page.click('#site-name')  # moderators reach the dashboard from the header
        page.wait_for_load_state()
        self.check_page(page)
        self.assertEqual(page.url, self.url('/admin/'))
        self.assertEqual(page.locator('.health-periods a[aria-current="page"]').inner_text(), '30 days')
        self.assertGreaterEqual(page.locator('.health-tile').count(), 18)
        self.assertEqual(self.tile(page, 'questions'), '3')  # not the deleted one
        self.assertEqual(self.tile(page, 'answers'), '1')
        self.assertEqual(self.tile(page, 'accepted'), '33%')
        self.assertIn('3 new questions', page.locator('#health-headline').inner_text())
        self.assertEqual(page.locator('.dashboard-grid').count(), 0)  # site status: administrators only

        page.wait_for_selector('#health-daily-chart canvas')
        series = page.evaluate('''() => jQuery('#health-daily-chart').data('plot').getData().map(s => ({
            label: s.label, points: s.data.length, total: s.data.reduce((a, p) => a + p[1], 0), color: s.color}))''')
        self.assertEqual([s['label'] for s in series], ['Questions', 'Answers', 'New members'])
        self.assertEqual(series[0]['total'], 3)  # the question 20 days ago too, not the deleted one
        self.assertEqual(len({s['color'] for s in series}), 3)
        self.assertEqual(series[0]['points'], 31)

        # Every chart has a summary and its figures in a table (folded, then opened).
        for section in ('health-daily', 'health-first-answer', 'health-tags', 'health-hours'):
            box = page.locator('#' + section)
            self.assertTrue(box.locator('.health-summary').inner_text().strip(), section)
            table = box.locator('details.health-table table')
            self.assertFalse(table.is_visible(), section)
            box.locator('details.health-table > summary').click()
            self.assertTrue(table.is_visible(), section)
        self.assertEqual(page.locator('#health-daily table tbody tr').count(), 31)
        self.assertIn('export', page.locator('#health-tags table').inner_text())
        answerer = page.locator('#health-answerers a', has_text='bob')
        self.assertEqual(answerer.get_attribute('href'), self.bob.get_profile_url())
        page.click('#health-definitions > summary')
        self.assertTrue(page.locator('#health-definitions dl').is_visible())
        self.shoot(page, 'desktop')
        self.assertEqual(self.axe(page), [])
        answerer.click()
        page.wait_for_load_state()
        self.check_page(page)
        self.assertEqual(page.url, self.url(self.bob.get_profile_url()))

    def test_period_switch_refresh_and_csv(self):
        page = self.open(self.admin)
        self.visit(page, '/admin/')
        page.click('.health-periods a:has-text("7 days")')
        page.wait_for_load_state()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/admin/?days=7'))
        self.assertEqual(page.locator('.health-periods a[aria-current="page"]').inner_text(), '7 days')
        self.assertEqual(self.tile(page, 'questions'), '2')
        self.assertIn('needs questions at least 7 days old', page.locator('#health-tile-answered_7d').inner_text())
        self.assertEqual(page.locator('#health-daily table tbody tr').count(), 8)
        page.click('.health-periods a:has-text("90 days")')
        page.wait_for_load_state()
        self.check_page(page)
        self.assertEqual(self.tile(page, 'questions'), '3')

        # Cached figures; "Refresh now" counts again.
        self.create_question(self.alice, 'A question asked after the figures', tags='collect')
        self.visit(page, '/admin/?days=90')
        self.assertEqual(self.tile(page, 'questions'), '3')
        page.click('.health-refresh')
        page.wait_for_load_state()
        self.check_page(page)
        self.assertTrue(page.url.endswith('/admin/?days=90'))
        self.assertEqual(self.tile(page, 'questions'), '4')

        with page.expect_download() as download:
            page.click('.health-csv')
        path = download.value.path()
        self.assertTrue(download.value.suggested_filename.startswith('community-health-90d-'))
        with open(path, encoding='utf-8') as f:
            rows = list(csv.reader(io.StringIO(f.read())))
        self.assertIn(['Activity', 'New questions', '4', '0'], rows)

    def test_phone_dark(self):
        page = self.open(self.moderator, device='mobile', dark=True)
        self.visit(page, '/admin/')
        page.wait_for_selector('#health-daily-chart canvas')
        for section in ('health-daily', 'health-first-answer', 'health-tags', 'health-hours'):
            page.locator('#%s details.health-table > summary' % section).click()
        page.click('#health-definitions > summary')
        self.check_page(page)  # no sideways scrolling with the tables open
        colours = page.evaluate('''() => ({
            tile: getComputedStyle(document.querySelector('.health-tile')).backgroundColor,
            text: getComputedStyle(document.querySelector('.health-tile-value')).color,
            line: jQuery('#health-daily-chart').data('plot').getData()[0].color})''')
        self.assertEqual(page.evaluate('document.documentElement.dataset.theme'), 'dark')
        self.assertNotEqual(colours['tile'], 'rgb(255, 255, 255)')
        self.assertEqual(colours['line'].replace(' ', ''), 'rgb(138,180,248)')  # --osqa-chart-1 in dark mode
        # Two tiles side by side at 390px.
        boxes = page.evaluate('''() => Array.from(document.querySelectorAll('#health-tile-questions, #health-tile-answers'))
            .map(e => e.getBoundingClientRect().top)''')
        self.assertEqual(boxes[0], boxes[1])
        self.shoot(page, 'phone-dark')
        self.assertEqual(self.axe(page), [])
