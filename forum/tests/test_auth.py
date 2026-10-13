import re

from forum.forms.general import clean_next
from forum.models import User

from .base import OSQATestCase


class LocalAuthTest(OSQATestCase):
    def register(self, username='carol', password='s3cret-pass'):
        return self.client.post('/account/local/register/', {
            'username': username, 'email': '%s@example.com' % username,
            'password1': password, 'password2': password, 'next': '/',
            'form_token': self.signup_token(),
        })

    def logged_in_user_id(self):
        return self.client.session.get('_auth_user_id')

    def test_signin_page_shows_local_form(self):
        self.assertContains(self.client.get('/account/signin/'), 'id="id_password"')

    def test_register_creates_superuser_and_sends_welcome_mail(self):
        response = self.register()
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(username='carol')
        self.assertTrue(user.is_superuser)  # the first account owns the site
        self.assertEqual(self.logged_in_user_id(), str(user.id))

        self.assertEqual(len(self.sent_mail), 1)
        recipient, subject, html, text, media = self.sent_mail[0]
        self.assertEqual(recipient, user)
        self.assertIn('Welcome', subject)
        self.assertTrue(text.strip())

    def test_second_account_is_not_superuser(self):
        self.create_user('alice')
        self.register()
        self.assertFalse(User.objects.get(username='carol').is_superuser)

    def test_password_login(self):
        user = self.create_user('carol', password='right-pass')

        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'wrong'})
        self.assertIsNone(self.logged_in_user_id())
        self.assertContains(self.client.get('/account/signin/'), 'valid username and password')

        self.client.post('/account/local/signin/', {'username': 'carol', 'password': 'right-pass'})
        self.assertEqual(self.logged_in_user_id(), str(user.id))

        self.client.get('/account/signout/')
        self.assertIsNone(self.logged_in_user_id())

    def test_temporary_login_link(self):
        user = self.create_user('carol')
        self.client.post('/account/tempsignin/', {'email': 'carol@example.com'})
        self.assertEqual(len(self.sent_mail), 1)

        link = re.search(r'/account/tempsignin/\d+/[^/\s"<]+/', self.sent_mail[0][3]).group(0)
        self.client.get(link)
        self.assertEqual(self.logged_in_user_id(), str(user.id))


class NextUrlTest(OSQATestCase):
    def test_accepts_local_paths(self):
        self.assertEqual(clean_next('/questions/1/'), '/questions/1/')

    def test_rejects_offsite_urls(self):
        default = clean_next(None)
        for url in ['//evil.com', '/\\evil.com', '%2F%2Fevil.com', 'http://evil.com']:
            with self.subTest(url=url):
                self.assertEqual(clean_next(url), default)


class UserCopyTest(OSQATestCase):
    def test_users_with_loaded_properties_can_be_copied(self):
        # The ONLINE_USERS setting deep-copies user objects when it is saved.
        import copy
        user = self.create_user('carol')
        user.prop.some_property
        self.assertEqual(copy.deepcopy({user: 1}).popitem()[1], 1)
