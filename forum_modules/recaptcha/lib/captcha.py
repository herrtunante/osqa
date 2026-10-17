
import json
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.utils.html import escape

VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"

# Seconds to wait for Google before giving up on a verification.
VERIFY_TIMEOUT = 10

class RecaptchaResponse:
    def __init__(self, is_valid, error_code=None):
        self.is_valid = is_valid
        self.error_code = error_code

def displayhtml(public_key):
    """The reCAPTCHA v2 checkbox widget; it posts its answer as g-recaptcha-response."""

    return """
    <div class="g-recaptcha" data-sitekey="%(PublicKey)s"></div>
    <script src="https://www.google.com/recaptcha/api.js" async defer></script>
""" % {
        'PublicKey' : escape(public_key),
        }


def submit(recaptcha_response_field,
           private_key,
           remoteip=None):
    """Verifies the user's answer with Google's siteverify API."""

    if not recaptcha_response_field:
        return RecaptchaResponse(is_valid=False, error_code='missing-input-response')

    params = {
        'secret': private_key,
        'response': recaptcha_response_field,
        }
    if remoteip:
        params['remoteip'] = remoteip

    request = Request(
        url=VERIFY_URL,
        data=urlencode(params).encode('utf-8'),
        headers={
            "Content-type": "application/x-www-form-urlencoded",
            "User-agent": "reCAPTCHA Python"
        }
    )

    try:
        with urlopen(request, timeout=VERIFY_TIMEOUT) as response:
            json_values = json.loads(response.read().decode('utf-8'))
    except (OSError, ValueError) as e:
        logging.error("Could not verify reCAPTCHA response: %s", e)
        return RecaptchaResponse(is_valid=False, error_code='verification-failed')

    if json_values.get("success"):
        return RecaptchaResponse(is_valid=True)
    else:
        return RecaptchaResponse(is_valid=False,
                                 error_code=', '.join(json_values.get('error-codes', [])) or 'invalid-input-response')
