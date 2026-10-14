from django import forms
from django.contrib.auth import password_validation
import re
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.functional import lazy
from django.utils.safestring import mark_safe
from forum import settings
from forum.models import User
from forum.modules import call_all_handlers
from urllib.parse import unquote
from django.utils.http import url_has_allowed_host_and_scheme

DEFAULT_NEXT = getattr(settings, 'APP_BASE_URL')
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

class StrippedNonEmptyCharField(forms.CharField):
    def clean(self,value):
        value = value.strip()
        if self.required and value == '':
            raise forms.ValidationError(_('this field is required'))
        return value

class NextUrlField(forms.CharField):
    def __init__(self):
        super(NextUrlField,self).__init__(max_length = 255,widget = forms.HiddenInput(),required = False)
    def clean(self,value):
        return clean_next(value)

login_form_widget_attrs = { 'class': 'required login' }
username_re = re.compile(r'^[\-\w\s ]+$', re.UNICODE)

class UserNameField(StrippedNonEmptyCharField):
    def __init__(self,db_model=User, db_field='username', must_exist=False,skip_clean=False,label=_lazy('choose a username'),**kw):
        self.must_exist = must_exist
        self.skip_clean = skip_clean
        self.db_model = db_model 
        self.db_field = db_field
        error_messages={'required':_lazy('user name is required'),
                        'taken':_lazy('sorry, this name is taken, please choose another'),
                        'forbidden':_lazy('sorry, this name is not allowed, please choose another'),
                        'missing':_lazy('sorry, there is no user with this name'),
                        'multiple-taken':_lazy('sorry, we have a serious error - user name is taken by several users'),
                        'invalid':_lazy('user name can only consist of letters, empty space, hyphens and underscore'),
                        'toshort':lazy(lambda: _('user name is to short, please use at least %d characters') % int(settings.MIN_USERNAME_LENGTH), str)()
                    }
        if 'error_messages' in kw:
            error_messages.update(kw['error_messages'])
            del kw['error_messages']
        super(UserNameField,self).__init__(max_length=30,
                widget=forms.TextInput(attrs=login_form_widget_attrs),
                label=label,
                error_messages=error_messages,
                **kw
                )

    def clean(self,username):
        """ validate username """
        if self.skip_clean == True:
            return username
        if hasattr(self, 'user_instance') and isinstance(self.user_instance, User):
            if username == self.user_instance.username:
                return username
        try:
            username = super(UserNameField, self).clean(username)
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
        super(UserEmailField,self).__init__(widget=forms.TextInput(attrs=dict(login_form_widget_attrs,
            maxlength=200)), label=mark_safe(_('your email address')),
            error_messages={'required':_lazy('email address is required'),
                            'invalid':_lazy('please enter a valid email address'),
                            'taken':_lazy('this email is already used by someone else, please choose another'),
                            },
            **kw
            )

    def clean(self,email):
        """ validate if email exist in database
        from legacy register
        return: raise error if it exist """
        email = super(UserEmailField,self).clean(email.strip())
        if self.skip_clean:
            return email
        if settings.EMAIL_UNIQUE == True:
            try:
                user = User.objects.get(email = email)
                raise forms.ValidationError(self.error_messages['taken'])
            except User.DoesNotExist:
                return email
            except User.MultipleObjectsReturned:
                raise forms.ValidationError(self.error_messages['taken'])
        else:
            return email 

class UserRealNameField(StrippedNonEmptyCharField):
    def __init__(self, db_model=User, db_field='real_name', must_exist=True, skip_clean=False, label=_lazy('Your real name'),**kw):
        self.must_exist = must_exist
        self.skip_clean = skip_clean
        self.db_model = db_model
        self.db_field = db_field
        error_messages={'required':_lazy('Real name is required')
                    }
        if 'error_messages' in kw:
            error_messages.update(kw['error_messages'])
            del kw['error_messages']
        super(UserRealNameField,self).__init__(max_length=100,
                widget=forms.TextInput(attrs=login_form_widget_attrs),
                label=label,
                error_messages=error_messages,
                **kw
                )

    def clean(self, real_name):
        if self.skip_clean == True:
            return real_name
        try:
            return super(UserRealNameField, self).clean(real_name)
        except forms.ValidationError:
            raise forms.ValidationError(self.error_messages['required'])

class UserNameValidationField(StrippedNonEmptyCharField):
    def __init__(self,**kw):
       error_messages={'required':_lazy('Real name is required')
                    }
       super(UserNameValidationField,self).__init__(max_length=0,
                widget=forms.TextInput(attrs=login_form_widget_attrs),
                label="Validation user",
                error_messages=error_messages,
                **kw
                )
       super(UserNameValidationField,self).__init__(required=False, widget=forms.TextInput(attrs=dict(login_form_widget_attrs,
            maxlength=200)), label="Human test",
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
        
class UserNameValidationSumField(StrippedNonEmptyCharField):
    def __init__(self,**kw):
       
         super(UserNameValidationSumField,self).__init__(widget=forms.TextInput(attrs=dict(login_form_widget_attrs,
            maxlength=200)), label="What is seven plus seven?",
            error_messages={
                            'invalid':_lazy('please probe you are not a robot by filling this simple sum correctly')
                            },
            **kw
            )

    
    def clean(self, value):
        # Form data is text: compare with '14', not the integer 14.
        value = (value or '').strip()
        if value != '14':
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
        return value
   

class SetPasswordForm(forms.Form):
    password1 = forms.CharField(widget=forms.PasswordInput(attrs=login_form_widget_attrs),
                                label=_lazy('choose password'),
                                help_text=_lazy('At least 8 characters; not a common password and not similar to your '
                                            'username or email.'),
                                error_messages={'required':_lazy('password is required')},
                                )
    password2 = forms.CharField(widget=forms.PasswordInput(attrs=login_form_widget_attrs),
                                label=_lazy('retype password'),
                                error_messages={'required':_lazy('please, retype your password'),
                                                'nomatch':_lazy('sorry, entered passwords did not match, please try again')},
                                )

    def __init__(self, data=None, user=None, *args, **kwargs):
        super(SetPasswordForm, self).__init__(data, *args, **kwargs)
        self.password_user = user

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
        super(SimpleCaptchaForm, self).__init__(*args, **kwargs)

        spam_fields = call_all_handlers('create_anti_spam_field')
        if spam_fields:
            spam_fields = dict(spam_fields)
            for name, field in spam_fields.items():
                self.fields[name] = field

            self._anti_spam_fields = spam_fields.keys()
        else:
            self._anti_spam_fields = []
