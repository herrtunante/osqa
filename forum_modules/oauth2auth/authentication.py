"""Sign in with Google, GitHub or Facebook using the OAuth 2.0 authorization code flow.

A provider is offered on the sign-in page once its client id and secret are set
(Admin > External Keys). The association key stored for a user is
"<provider>:<the provider's stable user id>".
"""
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.utils.translation import gettext as _

from forum.authentication.base import AuthenticationConsumer, ConsumerTemplateContext, InvalidAuthentication
from forum import settings as forum_settings

from . import settings

HTTP_TIMEOUT = 10
STATE_SESSION_KEY = 'oauth2_state'
REDIRECT_SESSION_KEY = 'oauth2_redirect_uri'


def http_json(url, data=None, headers=None):
    """GET (or POST when data is given) url and decode the JSON response."""
    if data is not None:
        data = urllib.parse.urlencode(data).encode('utf-8')
    request = urllib.request.Request(url, data=data, headers=dict({'Accept': 'application/json'}, **(headers or {})))
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, ValueError, OSError) as e:
        logging.error("OAuth 2.0 request to %s failed: %s", url.split('?')[0], e)
        raise InvalidAuthentication(_("Could not contact the login provider. Please try again later."))


class OAuth2AbstractAuthConsumer(AuthenticationConsumer):
    name = ''               # provider id: matches the URL and the consumer class name
    authorize_url = ''
    token_url = ''
    scope = ''

    def client_id(self):
        raise NotImplementedError

    def client_secret(self):
        raise NotImplementedError

    @classmethod
    def is_configured(cls):
        consumer = cls()
        return bool(str(consumer.client_id()).strip() and str(consumer.client_secret()).strip())

    def prepare_authentication_request(self, request, redirect_to):
        if not self.is_configured():
            raise InvalidAuthentication(_("This login method is not configured."))

        state = secrets.token_urlsafe(32)
        redirect_uri = '%s%s' % (forum_settings.APP_URL, redirect_to)
        request.session[STATE_SESSION_KEY] = state
        request.session[REDIRECT_SESSION_KEY] = redirect_uri

        return '%s?%s' % (self.authorize_url, urllib.parse.urlencode({
            'client_id': str(self.client_id()),
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': self.scope,
            'state': state,
        }))

    def process_authentication_request(self, request):
        expected_state = request.session.pop(STATE_SESSION_KEY, None)
        redirect_uri = request.session.pop(REDIRECT_SESSION_KEY, None)

        if request.GET.get('error'):
            raise InvalidAuthentication(_("The login was cancelled or refused by the provider."))

        # The state ties this callback to the request this browser started;
        # without it anyone could log a victim into the attacker's account.
        state = request.GET.get('state', '')
        if not expected_state or not secrets.compare_digest(state, expected_state):
            raise InvalidAuthentication(_("The login request expired or is invalid. Please try again."))

        code = request.GET.get('code')
        if not code:
            raise InvalidAuthentication(_("The login provider did not return an authorization code."))

        token = self.fetch_token(code, redirect_uri)
        access_token = token.get('access_token')
        if not access_token:
            logging.error("%s token response without access_token: %s", self.name, sorted(token.keys()))
            raise InvalidAuthentication(_("The login provider did not return an access token."))

        user_id, user_data = self.fetch_user(access_token)
        if not user_id:
            raise InvalidAuthentication(_("The login provider did not return a user id."))

        # Picked up by external_register to pre-fill the registration form.
        request.session['auth_consumer_data'] = user_data
        return '%s:%s' % (self.name, user_id)

    def fetch_token(self, code, redirect_uri):
        return http_json(self.token_url, data={
            'client_id': str(self.client_id()),
            'client_secret': str(self.client_secret()),
            'code': code,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        })

    def fetch_user(self, access_token):
        """Return (stable user id, dict with username/email/email_verified/real_name)."""
        raise NotImplementedError

    def get_user_data(self, key):
        # The profile was stored in the session by process_authentication_request.
        return {}


class GoogleAuthConsumer(OAuth2AbstractAuthConsumer):
    name = 'google'
    authorize_url = 'https://accounts.google.com/o/oauth2/v2/auth'
    token_url = 'https://oauth2.googleapis.com/token'
    userinfo_url = 'https://openidconnect.googleapis.com/v1/userinfo'
    scope = 'openid email profile'

    def client_id(self):
        return settings.GOOGLE_CLIENT_ID

    def client_secret(self):
        return settings.GOOGLE_CLIENT_SECRET

    def fetch_user(self, access_token):
        info = http_json(self.userinfo_url, headers={'Authorization': 'Bearer %s' % access_token})
        email = info.get('email', '')
        return info.get('sub'), {
            'username': info.get('given_name') or email.split('@')[0],
            'email': email,
            'email_verified': bool(info.get('email_verified')),
            'real_name': info.get('name', ''),
        }


class GitHubAuthConsumer(OAuth2AbstractAuthConsumer):
    name = 'github'
    authorize_url = 'https://github.com/login/oauth/authorize'
    token_url = 'https://github.com/login/oauth/access_token'
    user_url = 'https://api.github.com/user'
    emails_url = 'https://api.github.com/user/emails'
    scope = 'read:user user:email'

    def client_id(self):
        return settings.GITHUB_CLIENT_ID

    def client_secret(self):
        return settings.GITHUB_CLIENT_SECRET

    def fetch_user(self, access_token):
        headers = {'Authorization': 'Bearer %s' % access_token, 'X-GitHub-Api-Version': '2022-11-28'}
        user = http_json(self.user_url, headers=headers)

        # The profile email is optional and unverified; use the primary verified one.
        email, verified = user.get('email') or '', False
        try:
            for entry in http_json(self.emails_url, headers=headers):
                if entry.get('primary') and entry.get('verified'):
                    email, verified = entry.get('email', ''), True
                    break
        except InvalidAuthentication:
            pass

        return user.get('id'), {
            'username': user.get('login', ''),
            'email': email,
            'email_verified': verified,
            'real_name': user.get('name') or '',
        }


class FacebookAuthConsumer(OAuth2AbstractAuthConsumer):
    name = 'facebook'
    api_version = 'v19.0'
    authorize_url = 'https://www.facebook.com/%s/dialog/oauth' % api_version
    token_url = 'https://graph.facebook.com/%s/oauth/access_token' % api_version
    me_url = 'https://graph.facebook.com/%s/me' % api_version
    scope = 'email public_profile'

    def client_id(self):
        return settings.FACEBOOK_APP_ID

    def client_secret(self):
        return settings.FACEBOOK_APP_SECRET

    def fetch_user(self, access_token):
        me = http_json('%s?%s' % (self.me_url, urllib.parse.urlencode(
            {'fields': 'id,name,email', 'access_token': access_token})))
        email = me.get('email', '')
        return me.get('id'), {
            'username': (me.get('name') or email.split('@')[0]).replace(' ', ''),
            'email': email,
            # Facebook only returns confirmed email addresses.
            'email_verified': bool(email),
            'real_name': me.get('name', ''),
        }


class OAuth2AbstractAuthContext(ConsumerTemplateContext):
    mode = 'BIGICON'
    type = 'CUSTOM'
    code_template = 'modules/oauth2auth/button.html'
    consumer_class = None

    @classmethod
    def is_enabled(cls):
        return cls.consumer_class.is_configured()


class GoogleAuthContext(OAuth2AbstractAuthContext):
    weight = 100
    human_name = 'Google'
    consumer_class = GoogleAuthConsumer


class GitHubAuthContext(OAuth2AbstractAuthContext):
    weight = 110
    human_name = 'GitHub'
    consumer_class = GitHubAuthConsumer


class FacebookAuthContext(OAuth2AbstractAuthContext):
    weight = 120
    human_name = 'Facebook'
    consumer_class = FacebookAuthConsumer
