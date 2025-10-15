# -*- coding: utf-8 -*-

import urllib2, urllib
import json
from json import load as load_json

API_SSL_SERVER="https://www.google.com/recaptcha/api"
API_SERVER="http://www.google.com/recaptcha/api"
VERIFY_SERVER="www.google.com"

class RecaptchaResponse(object):
    def __init__(self, is_valid, error_code=None):
        self.is_valid = is_valid
        self.error_code = error_code

def displayhtml (public_key):

    return """
    <div id="g-recaptcha"></div>
    
    <script type="text/javascript">
    	var onloadCallback = function() {
      		grecaptcha.render(request, 'g-recaptcha', {
          		'sitekey' : '%(PublicKey)s',
          		'theme' : "light",
        	});
      	};
    </script>
    <script src="https://www.google.com/recaptcha/api.js?onload=onloadCallback&render=explicit" async defer></script>


""" % {
        'PublicKey' : public_key,
        }


def submit (recaptcha_response_field,
            private_key,
            remoteip):

    if not (recaptcha_response_field and
            len (recaptcha_response_field)):
        return RecaptchaResponse (is_valid = False, error_code = 'incorrect-captcha-sol')


    def encode_if_necessary(s):
        if isinstance(s, unicode):
            return s.encode('utf-8')
        return s

    params = urllib.urlencode ({
        'secret': encode_if_necessary(private_key),
        'remoteip' :  encode_if_necessary(remoteip),
        'response' :  encode_if_necessary(recaptcha_response_field),
        })

    request = urllib2.Request (

        url = "https://%s/recaptcha/api/siteverify" % VERIFY_SERVER,
        data = params,
        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "User-agent": "reCAPTCHA Python"
        }
    )

 
    json_values = load_json(urllib2.urlopen(request))
    if json_values["success"]:
        return RecaptchaResponse(is_valid = True)
    else:
        return RecaptchaResponse(is_valid = False, error_code = json.dumps(json_values) )
