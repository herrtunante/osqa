from .general import NextUrlField,  UserNameField,  UserEmailField, UserRealNameField, UserNameValidationField, SetPasswordForm, DescribedFieldsMixin
from forum import settings

from forum.models import User
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.safestring import mark_safe
from django import forms
from forum.utils.antispam import BotTrapMixin


class SimpleRegistrationForm(forms.Form):
    next = NextUrlField()
    username = UserNameField()
    email = UserEmailField()
    real_name = UserRealNameField()
    username_validation = UserNameValidationField()
    # No "what is seven plus seven?": a puzzle is a cognitive test (WCAG 2.2
    # 3.3.8); the honeypot, signed time stamp and per-IP limit stay.

class SignUpFieldsMixin(DescribedFieldsMixin):
    """Help texts and browser hints (autocomplete, no autocapitalisation) for
    the fields of the sign-up forms; errors and help are linked to the inputs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fields = self.fields
        fields['username'].help_text = _(
            "Shown with your questions and answers. %(min)d to 30 characters: letters, numbers, spaces, "
            "hyphens (-) and underscores (_).") % {'min': int(settings.MIN_USERNAME_LENGTH)}
        fields['username'].widget.attrs.update(autocomplete='username', autocapitalize='none', spellcheck='false',
                                               autofocus=True)
        fields['email'].help_text = _("We'll email you a link to confirm it. It's never shown publicly.")
        fields['email'].widget.attrs.update(autocomplete='email', autocapitalize='none', spellcheck='false')
        if 'real_name' in fields:
            fields['real_name'].help_text = _("Shown on your profile.")
            fields['real_name'].widget.attrs.update(autocomplete='name')
        self.describe_fields()


class PublicRegistrationForm(BotTrapMixin, SignUpFieldsMixin, SimpleRegistrationForm):
    """The sign-up form after an external login, with the bot traps."""


class TemporaryLoginRequestForm(DescribedFieldsMixin, forms.Form):
    def __init__(self, data=None):
        super().__init__(data)
        self.user_cache = None

    email = forms.EmailField(
            required=True,
            label=_lazy("Email address"),
            widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autocapitalize': 'none', 'spellcheck': 'false',
                                           'autofocus': True}),
            error_messages={
                'required': _lazy("Enter your email address."),
                'invalid': _lazy('Enter an email address like name@example.com.'),
            }
    )

    def clean_email(self):
        # No error for unknown addresses: the page must not tell anyone
        # which addresses have an account.
        self.user_cache = list(User.objects.filter(email__iexact=self.cleaned_data['email']))
        return self.cleaned_data['email']

class ChangePasswordForm(SetPasswordForm):
    """ change password form """
    oldpw = forms.CharField(widget=forms.PasswordInput(attrs={'class':'required', 'autocomplete': 'current-password'}),
                label=_lazy('Current password'))

    def __init__(self, data=None, user=None, *args, **kwargs):
        if user is None:
            raise TypeError("Keyword argument 'user' must be supplied")
        super().__init__(data, user, *args, **kwargs)
        self.user = user
        self.fields['password1'].label = _('New password')
        self.fields['password2'].label = _('Confirm new password')

    def clean_oldpw(self):
        """ test old password """
        if not self.user.check_password(self.cleaned_data['oldpw']):
            raise forms.ValidationError(_("That isn't your current password. Please try again."))
        return self.cleaned_data['oldpw']
