from django import forms
from django.contrib.auth import password_validation
import re
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.utils.translation import gettext_lazy as _lazy
from django.utils.functional import lazy
from django.utils.safestring import mark_safe
from forum import settings
from forum.models import User
from forum.modules import call_all_handlers
from urllib.parse import unquote
from django.utils.http import url_has_allowed_host_and_scheme

DEFAULT_NEXT = settings.APP_BASE_URL
def clean_next(next):
    if next is None:
        return DEFAULT_NEXT
    next = unquote(next).strip()
    # Only accept paths on this site; "//host" and "/\\host" would leave it.
    if next.startswith('/') and url_has_allowed_host_and_scheme(next, allowed_hosts=None):
        return next
    return DEFAULT_NEXT

def get_next_url(request):
    # Django 1.7+: request.REQUEST removed, check GET then POST
    next_url = request.GET.get('next') or request.POST.get('next')
    return clean_next(next_url)

def username_for_signin(text):
    """The username to sign in with, for what was typed in "Username or email":
    the text itself, or the username of the one account with that email
    address. Usernames can't contain "@" (username_re); an address shared by
    several accounts is ambiguous and is left as typed, so the sign-in fails."""
    if text and '@' in text and not User.objects.filter(username=text).exists():
        usernames = list(User.objects.filter(email__iexact=text).values_list('username', flat=True)[:2])
        if len(usernames) == 1:
            return usernames[0]
    return text

class StrippedNonEmptyCharField(forms.CharField):
    def clean(self,value):
        value = value.strip()
        if self.required and value == '':
            raise forms.ValidationError(_('this field is required'))
        return value

class NextUrlField(forms.CharField):
    def __init__(self):
        super().__init__(max_length = 255,widget = forms.HiddenInput(),required = False)
    def clean(self,value):
        return clean_next(value)

login_form_widget_attrs = { 'class': 'required login' }
username_re = re.compile(r'^[\-\w\s ]+$', re.UNICODE)

class UserNameField(StrippedNonEmptyCharField):
    def __init__(self,db_model=User, db_field='username', must_exist=False,skip_clean=False,label=_lazy('Username'),**kw):
        self.must_exist = must_exist
        self.skip_clean = skip_clean
        self.db_model = db_model
        self.db_field = db_field
        error_messages={'required':_lazy('Choose a username.'),
                        'taken':_lazy('That username is taken. Please choose another one.'),
                        'forbidden':_lazy("That username isn't allowed. Please choose another one."),
                        'missing':_lazy('There is no member with this username.'),
                        'multiple-taken':_lazy('That username is taken. Please choose another one.'),
                        'invalid':_lazy('Use only letters, numbers, spaces, hyphens (-) and underscores (_).'),
                        'toshort':lazy(lambda: _('Use at least %(count)d characters.') % {'count': int(settings.MIN_USERNAME_LENGTH)}, str)()
                    }
        if 'error_messages' in kw:
            error_messages.update(kw['error_messages'])
            del kw['error_messages']
        super().__init__(max_length=30,
                widget=forms.TextInput(attrs=login_form_widget_attrs),
                label=label,
                error_messages=error_messages,
                **kw
                )

    def clean(self,username):
        """ validate username """
        if self.skip_clean:
            return username
        if hasattr(self, 'user_instance') and isinstance(self.user_instance, User):
            if username == self.user_instance.username:
                return username
        try:
            username = super().clean(username)
        except forms.ValidationError:
            raise forms.ValidationError(self.error_messages['required'])
        if len(username) < settings.MIN_USERNAME_LENGTH:
            raise forms.ValidationError(self.error_messages['toshort'])
        if self.required and not username_re.match(username):
            raise forms.ValidationError(self.error_messages['invalid'])
        if username in settings.RESERVED_USERNAMES:
            raise forms.ValidationError(self.error_messages['forbidden'])
        try:
            user = self.db_model.objects.get(
                    **{'%s' % self.db_field : username}
            )
            if user:
                if self.must_exist:
                    return username
                else:
                    raise forms.ValidationError(self.error_messages['taken'])
        except self.db_model.DoesNotExist:
            if self.must_exist:
                raise forms.ValidationError(self.error_messages['missing'])
            else:
                return username
        except self.db_model.MultipleObjectsReturned:
            raise forms.ValidationError(self.error_messages['multiple-taken'])

class UserEmailField(forms.EmailField):
    def __init__(self,skip_clean=False,**kw):
        self.skip_clean = skip_clean
        super().__init__(widget=forms.EmailInput(attrs=dict(login_form_widget_attrs,
            maxlength=200)), label=_lazy('Email address'),
            error_messages={'required':_lazy('Enter your email address.'),
                            'invalid':_lazy('Enter an email address like name@example.com.'),
                            'taken':_lazy('There is already an account with this email address. Sign in instead, '
                                          'or ask for a sign-in link if you forgot your password.'),
                            },
            **kw
            )

    def clean(self,email):
        """ validate if email exist in database
        from legacy register
        return: raise error if it exist """
        email = super().clean(email.strip())
        if self.skip_clean:
            return email
        if settings.EMAIL_UNIQUE:
            try:
                User.objects.get(email = email)
                raise forms.ValidationError(self.error_messages['taken'])
            except User.DoesNotExist:
                return email
            except User.MultipleObjectsReturned:
                raise forms.ValidationError(self.error_messages['taken'])
        else:
            return email

class UserRealNameField(StrippedNonEmptyCharField):
    def __init__(self, db_model=User, db_field='real_name', must_exist=True, skip_clean=False, label=_lazy('Your name'),**kw):
        self.must_exist = must_exist
        self.skip_clean = skip_clean
        self.db_model = db_model
        self.db_field = db_field
        error_messages={'required':_lazy('Enter your name.')
                    }
        if 'error_messages' in kw:
            error_messages.update(kw['error_messages'])
            del kw['error_messages']
        super().__init__(max_length=100,
                widget=forms.TextInput(attrs=login_form_widget_attrs),
                label=label,
                error_messages=error_messages,
                **kw
                )

    def clean(self, real_name):
        if self.skip_clean:
            return real_name
        try:
            return super().clean(real_name)
        except forms.ValidationError:
            raise forms.ValidationError(self.error_messages['required'])

class UserNameValidationField(StrippedNonEmptyCharField):
    def __init__(self,**kw):
       error_messages={'required':_lazy('Real name is required')
                    }
       super().__init__(max_length=0,
                widget=forms.TextInput(attrs=login_form_widget_attrs),
                label="Validation user",
                error_messages=error_messages,
                **kw
                )
       super().__init__(required=False, widget=forms.TextInput(attrs=dict(login_form_widget_attrs,
            maxlength=200, tabindex='-1', autocomplete='off')), label="Human test",
            error_messages={
                            'invalid':_lazy('please probe you are not a robot by not filling this')
                            },
            **kw
            )



    # Honeypot: humans leave it empty. StrippedNonEmptyCharField.clean() doesn't
    # call validate(), and the field must not be required.
    def clean(self, value):
        value = (value or '').strip()
        if value:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
        return value

class PasswordRule(str):
    """One line of the password rules; `key` tells osqa.password.js which
    rule it is ('length', 'common', 'similar', 'breach'), so the strength
    meter ticks the same rules the server checks."""
    def __new__(cls, text, key=''):
        rule = super().__new__(cls, text)
        rule.key = key
        return rule


def password_rules():
    """The AUTH_PASSWORD_VALIDATORS rules in a few plain words each, for the
    help text of new password fields (the length first)."""
    from django.contrib.auth.password_validation import (
        CommonPasswordValidator, MinimumLengthValidator, NumericPasswordValidator,
        UserAttributeSimilarityValidator, get_default_password_validators)
    from forum.utils.passwords import BreachedPasswordValidator, MaximumLengthValidator
    rules = []
    for validator in get_default_password_validators():
        if isinstance(validator, MinimumLengthValidator):
            rules.insert(0, PasswordRule(ngettext('At least %(count)d character', 'At least %(count)d characters',
                                                  validator.min_length) % {'count': validator.min_length}, 'length'))
        elif isinstance(validator, MaximumLengthValidator):
            continue  # far above what people type; the field's maxlength says it
        elif isinstance(validator, CommonPasswordValidator):
            rules.append(PasswordRule(_('Not a common password'), 'common'))
        elif isinstance(validator, NumericPasswordValidator):
            rules.append(PasswordRule(_('Not only numbers'), 'numeric'))
        elif isinstance(validator, UserAttributeSimilarityValidator):
            rules.append(PasswordRule(_('Not too close to your username or email'), 'similar'))
        elif isinstance(validator, BreachedPasswordValidator):
            if bool(settings.PASSWORD_BREACH_CHECK):
                rules.append(PasswordRule(validator.get_help_text(), 'breach'))
        else:
            rules.append(PasswordRule(validator.get_help_text()))
    return rules


class DescribedFieldsMixin:
    """Links each input to its help text and error message (aria-describedby,
    ids "<input id>-help" and "<input id>-error", as auth/field.html renders
    them) and marks the inputs with errors (aria-invalid)."""

    def describe_fields(self):
        errors = self._errors or {}
        for name, field in self.fields.items():
            if errors:  # the error summary gets the focus instead
                field.widget.attrs.pop('autofocus', None)
            auto_id = self[name].auto_id
            ids = []
            if name in errors:
                ids.append('%s-error' % auto_id)
                field.widget.attrs['aria-invalid'] = 'true'
            if field.help_text or getattr(field, 'rules', None):
                ids.append('%s-help' % auto_id)
            if ids:
                field.widget.attrs['aria-describedby'] = ' '.join(ids)

    def full_clean(self):
        super().full_clean()
        self.describe_fields()


class SetPasswordForm(forms.Form):
    password1 = forms.CharField(widget=forms.PasswordInput(attrs=dict(login_form_widget_attrs, autocomplete='new-password')),
                                label=_lazy('Password'),
                                error_messages={'required':_lazy('Choose a password.')},
                                )
    password2 = forms.CharField(widget=forms.PasswordInput(attrs=dict(login_form_widget_attrs, autocomplete='new-password')),
                                label=_lazy('Confirm password'),
                                error_messages={'required':_lazy('Type your password again.'),
                                                'nomatch':_lazy("The passwords don't match. Type the same password in both boxes.")},
                                )

    def __init__(self, data=None, user=None, *args, **kwargs):
        super().__init__(data, *args, **kwargs)
        self.password_user = user
        # The rules are listed under the field and ticked as the person types (osqa.password.js).
        rules = password_rules()
        self.fields['password1'].rules = rules
        self.fields['password1'].help_text = ''.join('%s. ' % rule.rstrip('.') for rule in rules).strip()
        from forum.utils.passwords import MAX_LENGTH, min_length
        self.fields['password1'].widget.attrs.update({
            'maxlength': MAX_LENGTH, 'data-password-meter': '', 'data-min-length': min_length(),
            'data-user-inputs': 'id_username id_email', 'autocapitalize': 'none', 'spellcheck': 'false'})
        if user is not None:
            self.fields['password1'].widget.attrs['data-user-words'] = ' '.join(
                w for w in (getattr(user, 'username', ''), (getattr(user, 'email', '') or '').split('@')[0]) if w)
        self.fields['password2'].widget.attrs.update({'maxlength': MAX_LENGTH, 'autocapitalize': 'none',
                                                      'spellcheck': 'false'})

    def clean_password1(self):
        """Checks the password against AUTH_PASSWORD_VALIDATORS (length, common
        passwords, similarity to the username or email)."""
        password = self.cleaned_data['password1']
        user = self.password_user
        if user is None:  # signing up: compare with what was typed in the form
            from django.contrib.auth.models import User as DjangoUser
            user = DjangoUser(username=self.data.get('username', ''), email=self.data.get('email', ''))
        password_validation.validate_password(password, user)
        return password

    def clean_password2(self):
        """
        Validates that the two password inputs match.

        """
        if 'password1' in self.cleaned_data:
            if self.cleaned_data['password1'] == self.cleaned_data['password2']:
                self.password = self.cleaned_data['password2']
                self.cleaned_data['password'] = self.cleaned_data['password2']
                return self.cleaned_data['password2']
            else:
                del self.cleaned_data['password2']
                raise forms.ValidationError(self.fields['password2'].error_messages['nomatch'])
        else:
            return self.cleaned_data['password2']

class SimpleCaptchaForm(forms.Form):
    fields = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        spam_fields = call_all_handlers('create_anti_spam_field')
        if spam_fields:
            spam_fields = dict(spam_fields)
            for name, field in spam_fields.items():
                self.fields[name] = field

            self._anti_spam_fields = spam_fields.keys()
        else:
            self._anti_spam_fields = []
