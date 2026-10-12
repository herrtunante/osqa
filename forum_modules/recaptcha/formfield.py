from django import forms
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from forum import REQUEST_HOLDER

from .lib import captcha
from . import settings

class ReCaptchaField(forms.Field):

	def __init__(self, *args, **kwargs):
		super(ReCaptchaField, self).__init__(*args, widget=ReCaptchaWidget, **kwargs)

	def clean(self, values):
		super(ReCaptchaField, self).clean(values)

		request = REQUEST_HOLDER.request
		remoteip = request and request.META.get('REMOTE_ADDR') or None
		check_captcha = captcha.submit(values, str(settings.RECAPTCHA_PRIV_KEY), remoteip)

		if not check_captcha.is_valid:
			raise forms.ValidationError(_('Invalid captcha, please try again.'), code=check_captcha.error_code)

		return values


class ReCaptchaWidget(forms.Widget):
	def render(self, name, value, attrs=None, renderer=None):
		return mark_safe(captcha.displayhtml(str(settings.RECAPTCHA_PUB_KEY)))

	def value_from_datadict(self, data, files, name):
		return data.get('g-recaptcha-response', None)

	def value_omitted_from_data(self, data, files, name):
		return 'g-recaptcha-response' not in data
