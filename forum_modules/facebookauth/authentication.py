# -*- coding: utf-8 -*-

import cgi
import logging

from urllib.request import urlopen
from urllib.parse import urlencode
from forum.authentication.base import AuthenticationConsumer, ConsumerTemplateContext, InvalidAuthentication

from django.conf import settings as django_settings
from django.utils.encoding import smart_str
from django.urls import reverse
from forum.utils.urls import root_relative_reverse
from django.utils.translation import gettext as _

import settings

from json import load as load_json


class FacebookAuthConsumer(AuthenticationConsumer):

    def prepare_authentication_request(self, request, redirect_to):
        args = dict(
            client_id=settings.FB_API_KEY,
            redirect_uri="%s%s" % (django_settings.APP_URL, redirect_to),
            scope="email"
        )

        facebook_api_authentication_url = "https://graph.facebook.com/oauth/authorize?" + urlencode(args)

        return facebook_api_authentication_url

    def process_authentication_request(self, request):
        try:
            redirect_uri = "%s%s" % (django_settings.APP_URL, root_relative_reverse('auth_provider_done', kwargs={'provider': 'facebook'}))
            args = dict(client_id=settings.FB_API_KEY, redirect_uri=redirect_uri)

            args["client_secret"] = settings.FB_APP_SECRET  #facebook APP Secret

            args["code"] = request.GET.get("code", None)
            response = cgi.parse_qs(urlopen("https://graph.facebook.com/oauth/access_token?" + urlencode(args)).read())
            access_token = response["access_token"][-1]


            user_data = self.get_user_data(access_token)
            assoc_key = user_data["id"]

            # Store the access token in cookie
            request.session["access_token"] = access_token
            request.session["assoc_key"] = assoc_key

            # Return the association key
            return assoc_key
        except Exception as e:
            logging.error("Problem during facebook authentication: %s" % e)
            raise InvalidAuthentication(_("Something wrond happened during Facebook authentication, administrators will be notified"))

    def get_user_data(self, access_token):
        profile = load_json(urlopen("https://graph.facebook.com/me?" + urlencode(dict(access_token=access_token))))

        name = profile["name"]

        # Check whether the length if the email is greater than 75, if it is -- just replace the email
        # with a blank string variable, otherwise we're going to have trouble with the Django model.
        email = smart_str(profile['email'])
        if len(email) > 75:
            email = ''

        # If the name is longer than 30 characters - leave it blank
        if len(name) > 30:
            name = ''

        # Return the user data.
        return {
            'id' : profile['id'],
            'username': name,
            'email': email,
        }

class FacebookAuthContext(ConsumerTemplateContext):
    mode = 'BIGICON'
    type = 'CUSTOM'
    weight = 100
    human_name = 'Facebook'
    code_template = 'modules/facebookauth/button.html'
    extra_css = []

    API_KEY = settings.FB_API_KEY
