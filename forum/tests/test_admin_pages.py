"""Administration pages: statistics, the test email page, dark mode and
layout details of the admin templates."""
import json
import re
from datetime import datetime, timedelta

from django.test import Client

from forum import settings
from forum.models import Question, User
from forum.settings.forms import StringListWidget

from .base import OSQATestCase
from .test_antispam import SettingMixin


class AdminPagesTest(SettingMixin, OSQATestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin', is_superuser=True, is_staff=True)
        self.login(self.admin)

    def question(self, title, days_ago=0, body='<p>Body text</p>'):
        question = Question(author=self.admin, title=title, body=body, tagnames='one')
        question.save()
        if days_ago:
            Question.objects.filter(id=question.id).update(added_at=datetime.now() - timedelta(days=days_ago))
        return question

    def graph(self, response, graph_id):
        match = re.search(r"osqaTimeChart\('#%s', (\[.*?\])\);" % graph_id, response.content.decode())
        return json.loads(match.group(1))

    def test_statistics_count_every_item(self):
        self.question('An old question', days_ago=60)
        self.question('A recent question')
        self.question('Another recent question')
        response = self.client.get('/admin/statistics/')
        self.assertEqual([n for _, n in self.graph(response, 'questions_graph')], [2, 3])
        # The last point of the users graph is the number of users.
        self.assertEqual(self.graph(response, 'userss_graph')[-1][1], User.objects.count())

    def test_statistics_page_title(self):
        self.assertContains(self.client.get('/admin/statistics/'), '<title>OSQA administration area - Statistics</title>',
                            html=False)

    def test_test_email_page_does_not_send_on_get(self):
        response = self.client.get('/admin/test_email_settings/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.sent_mail, [])
        self.assertContains(response, 'id="send-test-email"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assertContains(response, 'id="admin-menu"')

    def test_test_email_needs_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post('/admin/test_email_settings/').status_code, 403)
        self.assertEqual(self.sent_mail, [])

    def test_test_email_form_post_shows_full_page(self):
        response = self.client.post('/admin/test_email_settings/')
        self.assertEqual(len(self.sent_mail), 1)
        self.assertContains(response, 'admin@example.com')
        self.assertContains(response, 'id="send-test-email"')

    def test_test_email_other_methods_refused(self):
        self.assertEqual(self.client.put('/admin/test_email_settings/').status_code, 405)
        self.assertEqual(self.sent_mail, [])

    def test_test_email_needs_superuser(self):
        self.login(self.create_user('moderator', is_staff=True))
        self.client.post('/admin/test_email_settings/', **self.AJAX)
        self.assertEqual(self.sent_mail, [])

    def test_dark_mode_follows_the_site_setting(self):
        for layout in (True, False):
            self.set(settings.DJSTYLE_ADMIN_INTERFACE, layout)
            self.set(settings.DARK_MODE, True)
            response = self.client.get('/admin/')
            self.assertContains(response, 'data-color-scheme="auto"')
            if layout:
                self.assertContains(response, 'css/dark_mode.css')
            self.set(settings.DARK_MODE, False)
            response = self.client.get('/admin/')
            self.assertNotContains(response, 'data-color-scheme')
            self.assertNotContains(response, 'css/dark_mode.css')

    def test_dark_mode_stylesheet_is_served(self):
        response = self.client.get('/m/django_admin/css/dark_mode.css')
        self.assertEqual(response.status_code, 200)

    def test_breadcrumbs_have_no_stray_dash(self):
        self.set(settings.DJSTYLE_ADMIN_INTERFACE, True)
        for path in ('/admin/', '/admin/tools/createuser/', '/admin/tools/nodeman/', '/admin/tools/exporter/',
                     '/admin/tools/importer/', '/admin/sximporter/'):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                crumbs = re.search(r'<div class="breadcrumbs">(.*?)</div>', body, re.S).group(1)
                self.assertNotIn(' - ', re.sub(r'\s+', ' ', crumbs))

    def test_node_manager_summary_escaped_once(self):
        self.question('Exporting', body='<p>Use Export &gt; CSV &amp; <b>more</b> &lt;script&gt;</p>')
        body = self.client.get('/admin/tools/nodeman/').content.decode()
        self.assertIn('Use Export &gt; CSV &amp; more &lt;script&gt;', body)
        self.assertNotIn('&amp;gt;', body)
        self.assertNotIn('<script>', body.split('<body', 1)[1].replace('<script>osqaAdminMenu', ''))

    def test_summary_is_plain_text(self):
        question = Question(title='T', body='<p>A &gt; B &amp; C &lt;i&gt;</p>')
        self.assertEqual(question.summary, 'A > B & C <i>')

    def test_empty_states(self):
        self.assertContains(self.client.get('/admin/static_pages/'), 'There are no custom pages yet.')

    def test_string_list_widget_escapes_values(self):
        html = StringListWidget().render('FLAG_REASONS', ['"><script>alert(1)</script>'])
        self.assertNotIn('<script>', html)
        self.assertIn('&quot;&gt;&lt;script&gt;', html)
        self.assertIn('type="button"', html)
