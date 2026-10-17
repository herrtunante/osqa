from forum.settings import EXT_KEYS_SET
from forum.settings.base import Setting

RECAPTCHA_PUB_KEY = Setting('RECAPTCHA_PUB_KEY', '', EXT_KEYS_SET, dict(
label = "Recaptch public key",
help_text = """
Get this key (the reCAPTCHA v2 "I'm not a robot" site key) at
<a href="https://www.google.com/recaptcha/admin">reCAPTCHA</a> to enable
recaptcha anti spam through.
""",
required=False))

RECAPTCHA_PRIV_KEY = Setting('RECAPTCHA_PRIV_KEY', '', EXT_KEYS_SET, dict(
label = "Recaptch private key",
help_text = """
This is the secret key you'll get in the same place as the recaptcha public key.
""",
required=False))
