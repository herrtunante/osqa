import json
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs

from django.conf import settings as djsettings
from django.test import override_settings

from forum import settings
from forum.models import User
from forum_modules.localauth.forms import ClassicRegisterForm

from .base import OSQATestCase

import forum_modules.recaptcha
from forum_modules.recaptcha import settings as recaptcha_settings
from forum_modules.recaptcha.lib import captcha


def siteverify_reply(result):
    urlopen = mock.MagicMock()
    urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(result).encode('utf-8')
    return mock.patch('forum_modules.recaptcha.lib.captcha.urlopen', urlopen)


# The module is disabled in the test settings; enable it as the module loader would.
@override_settings(MODULE_LIST=djsettings.MODULE_LIST + [forum_modules.recaptcha])
class RecaptchaTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        for name in ('RECAPTCHA_PUB_KEY', 'RECAPTCHA_PRIV_KEY'):
            setting = getattr(recaptcha_settings, name)
            patcher = mock.patch.object(settings, name, setting, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(setattr, setting, '_temp', None)

        self.set_keys('site-key', 'secret-key')

    def set_keys(self, public, private):
        recaptcha_settings.RECAPTCHA_PUB_KEY.set_value(public)
        recaptcha_settings.RECAPTCHA_PRIV_KEY.set_value(private)

    def register(self, **extra):
        data = {
            'username': 'carol', 'email': 'carol@example.com',
            'password1': 's3cret-pass', 'password2': 's3cret-pass', 'next': '/',
        }
        data.update(extra)
        return self.client.post('/account/local/register/', data, REMOTE_ADDR='10.1.2.3')

    def test_widget_is_rendered(self):
        response = self.client.get('/account/local/register/')
        self.assertContains(response, 'class="g-recaptcha" data-sitekey="site-key"')
        self.assertContains(response, 'https://www.google.com/recaptcha/api.js')

    def test_registration_accepted_on_success(self):
        with siteverify_reply({'success': True}) as urlopen:
            response = self.register(**{'g-recaptcha-response': 'token-123'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='carol').exists())

        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, 'https://www.google.com/recaptcha/api/siteverify')
        self.assertEqual(parse_qs(request.data.decode('utf-8')), {
            'secret': ['secret-key'], 'response': ['token-123'], 'remoteip': ['10.1.2.3'],
        })
        self.assertTrue(urlopen.call_args[1]['timeout'])

    def test_registration_rejected_on_failure(self):
        with siteverify_reply({'success': False, 'error-codes': ['invalid-input-response']}):
            response = self.register(**{'g-recaptcha-response': 'bad-token'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid captcha')
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_registration_rejected_without_answer(self):
        with siteverify_reply({'success': True}) as urlopen:
            response = self.register()

        self.assertEqual(response.status_code, 200)
        urlopen.assert_not_called()
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_registration_rejected_when_google_unreachable(self):
        with mock.patch('forum_modules.recaptcha.lib.captcha.urlopen', side_effect=URLError('down')):
            response = self.register(**{'g-recaptcha-response': 'token-123'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_no_keys_no_captcha_field(self):
        self.set_keys('', '')
        self.assertNotIn('recaptcha', ClassicRegisterForm().fields)

        with siteverify_reply({'success': False}) as urlopen:
            self.assertEqual(self.register().status_code, 302)
        urlopen.assert_not_called()
        self.assertTrue(User.objects.filter(username='carol').exists())

    def test_site_key_is_escaped(self):
        self.assertNotIn('"><script>', captcha.displayhtml('"><script>'))
