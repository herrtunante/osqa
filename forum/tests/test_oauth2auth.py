from unittest import mock
from urllib.parse import parse_qs, urlparse

import forum.authentication  # noqa: F401 -- scan providers before importing a provider module directly
from forum.models import AuthKeyUserAssociation, User
from forum_modules.oauth2auth import authentication as oauth2
from forum_modules.oauth2auth import settings as oauth2_settings

from .base import OSQATestCase

GOOGLE_PROFILE = {'sub': '1234567890', 'email': 'carol@gmail.com', 'email_verified': True,
                  'name': 'Carol Example', 'given_name': 'carol'}


class FakeProvider(object):
    """Stands in for the providers' HTTP endpoints."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url.split('?')[0], data, headers))
        for prefix, response in self.responses.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError('unexpected request to %s' % url)


class OAuth2LoginTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        for name, value in [('GOOGLE_CLIENT_ID', 'google-id'), ('GOOGLE_CLIENT_SECRET', 'google-secret'),
                            ('GITHUB_CLIENT_ID', 'github-id'), ('GITHUB_CLIENT_SECRET', 'github-secret'),
                            ('FACEBOOK_APP_ID', ''), ('FACEBOOK_APP_SECRET', '')]:
            patcher = mock.patch.object(oauth2_settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_http(self, responses):
        fake = FakeProvider(responses)
        patcher = mock.patch.object(oauth2, 'http_json', fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def start_login(self, provider):
        response = self.client.get('/account/%s/signin/' % provider)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response['Location']).query)
        return response['Location'], query

    def logged_in_user_id(self):
        return self.client.session.get('_auth_user_id')

    def register(self, **fields):
        data = {'bnewaccount': '1', 'next': '/', 'username_validation': '', 'username_validation_sum': '14',
                'form_token': self.signup_token()}
        data.update(fields)
        return self.client.post('/account/register/', data)

    def test_signin_page_lists_configured_providers_only(self):
        page = self.client.get('/account/signin/').content.decode()
        self.assertIn('Sign in with Google', page)
        self.assertIn('Sign in with GitHub', page)
        self.assertNotIn('Sign in with Facebook', page)

    def test_redirect_to_provider(self):
        location, query = self.start_login('google')
        self.assertTrue(location.startswith('https://accounts.google.com/o/oauth2/v2/auth?'))
        self.assertEqual(query['client_id'], ['google-id'])
        self.assertEqual(query['redirect_uri'], ['http://testserver/account/google/done/'])
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['state'][0], self.client.session['oauth2_state'])

    def test_google_register_then_login(self):
        fake = self.fake_http({oauth2.GoogleAuthConsumer.token_url: {'access_token': 'token-1'},
                               oauth2.GoogleAuthConsumer.userinfo_url: GOOGLE_PROFILE})
        _, query = self.start_login('google')
        response = self.client.get('/account/google/done/', {'code': 'auth-code', 'state': query['state'][0]})
        self.assertRedirects(response, '/account/register/', fetch_redirect_response=False)

        # The token request carries the code, secret and the same redirect URI.
        token_call = fake.calls[0]
        self.assertEqual(token_call[1]['code'], 'auth-code')
        self.assertEqual(token_call[1]['client_secret'], 'google-secret')
        self.assertEqual(token_call[1]['redirect_uri'], 'http://testserver/account/google/done/')

        form = self.client.get('/account/register/').content.decode()
        self.assertIn('carol@gmail.com', form)

        response = self.register(username='carol', email='carol@gmail.com', real_name='Carol Example')
        self.assertEqual(response.status_code, 302, response.content[:2000])
        user = User.objects.get(username='carol')
        self.assertTrue(user.email_isvalid)
        self.assertEqual(AuthKeyUserAssociation.objects.get(user=user).key, 'google:1234567890')
        self.assertEqual(self.logged_in_user_id(), str(user.id))

        self.client.logout()
        _, query = self.start_login('google')
        self.client.get('/account/google/done/', {'code': 'auth-code-2', 'state': query['state'][0]})
        self.assertEqual(self.logged_in_user_id(), str(user.id))

    def test_github_unverified_email_is_not_validated(self):
        self.fake_http({oauth2.GitHubAuthConsumer.token_url: {'access_token': 'token-1'},
                        oauth2.GitHubAuthConsumer.emails_url: [{'email': 'dan@example.com', 'primary': True, 'verified': False}],
                        oauth2.GitHubAuthConsumer.user_url: {'id': 42, 'login': 'dan', 'name': 'Dan', 'email': 'dan@example.com'}})
        _, query = self.start_login('github')
        self.client.get('/account/github/done/', {'code': 'c', 'state': query['state'][0]})
        self.register(username='dan', email='dan@example.com', real_name='Dan')
        user = User.objects.get(username='dan')
        self.assertFalse(user.email_isvalid)
        self.assertEqual(AuthKeyUserAssociation.objects.get(user=user).key, 'github:42')

    def test_wrong_state_is_rejected(self):
        fake = self.fake_http({})
        self.start_login('google')
        response = self.client.get('/account/google/done/', {'code': 'c', 'state': 'forged'})
        self.assertRedirects(response, '/account/signin/', fetch_redirect_response=False)
        self.assertEqual(fake.calls, [])
        self.assertContains(self.client.get('/account/signin/'), 'expired or is invalid')

    def test_callback_without_started_login_is_rejected(self):
        fake = self.fake_http({})
        self.client.get('/account/google/done/', {'code': 'c', 'state': 'anything'})
        self.assertEqual(fake.calls, [])
        self.assertIsNone(self.logged_in_user_id())

    def test_provider_error_is_reported(self):
        self.fake_http({oauth2.GoogleAuthConsumer.token_url: oauth2.InvalidAuthentication('Could not contact')})
        _, query = self.start_login('google')
        response = self.client.get('/account/google/done/', {'code': 'c', 'state': query['state'][0]})
        self.assertRedirects(response, '/account/signin/', fetch_redirect_response=False)
        self.assertIsNone(self.logged_in_user_id())

    def test_unconfigured_provider(self):
        response = self.client.get('/account/facebook/signin/')
        self.assertRedirects(response, '/account/signin/', fetch_redirect_response=False)
        self.assertContains(self.client.get('/account/signin/'), 'not configured')

    def test_logged_in_user_can_link_provider(self):
        user = self.create_user('erin')
        self.login(user)
        self.fake_http({oauth2.GoogleAuthConsumer.token_url: {'access_token': 'token-1'},
                        oauth2.GoogleAuthConsumer.userinfo_url: GOOGLE_PROFILE})
        _, query = self.start_login('google')
        self.client.get('/account/google/done/', {'code': 'c', 'state': query['state'][0]})
        self.assertEqual(AuthKeyUserAssociation.objects.get(user=user).key, 'google:1234567890')


class RegistrationRobotChecksTest(OSQATestCase):
    def form(self, **fields):
        from forum.forms import SimpleRegistrationForm
        data = {'next': '/', 'username': 'carol', 'email': 'carol@example.com', 'real_name': 'Carol',
                'username_validation': '', 'username_validation_sum': '14'}
        data.update(fields)
        return SimpleRegistrationForm(data)

    def test_human_passes(self):
        self.assertTrue(self.form().is_valid())

    def test_wrong_sum_fails(self):
        self.assertIn('username_validation_sum', self.form(username_validation_sum='15').errors)

    def test_filled_honeypot_fails(self):
        self.assertIn('username_validation', self.form(username_validation='robot').errors)
