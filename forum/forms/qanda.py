import re
from datetime import date
from django import forms
from forum.models import *
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.utils.functional import lazy

from django.utils.encoding import smart_str
from .general import DescribedFieldsMixin, NextUrlField, UserNameField

from forum import settings, REQUEST_HOLDER
from forum.utils.email_frequency import FREQUENCY_CHOICES

from forum.modules import call_all_handlers

import logging

class TitleField(forms.CharField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.required = True
        self.max_length = 255
        self.widget = forms.TextInput(attrs={'size' : 70, 'autocomplete' : 'off', 'maxlength' : self.max_length})
        self.label  = _lazy('title')
        self.help_text = _lazy('please enter a descriptive title for your question')
        self.initial = ''

    def clean(self, value):
        super().clean(value)

        if len(value) < settings.FORM_MIN_QUESTION_TITLE:
            raise forms.ValidationError(_('title must be at least %s characters') % settings.FORM_MIN_QUESTION_TITLE)
        return value

class EditorField(forms.CharField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # The editor sits under the toolbar without a visible <label>.
        self.widget = forms.Textarea(attrs={'id': 'editor', 'aria-label': _lazy('content')})
        self.label  = _lazy('content')
        self.help_text = ''
        self.initial = ''


class QuestionEditorField(EditorField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.required = not bool(settings.FORM_EMPTY_QUESTION_BODY)


    def clean(self, value):
        super().clean(value)

        if not bool(settings.FORM_EMPTY_QUESTION_BODY) and (len(re.sub('[ ]{2,}', ' ', value)) < settings.FORM_MIN_QUESTION_BODY):
            raise forms.ValidationError(_('question content must be at least %s characters') % settings.FORM_MIN_QUESTION_BODY)

        return value

class AnswerEditorField(EditorField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.required = True

    def clean(self, value):
        super().clean(value)

        if len(re.sub('[ ]{2,}', ' ', value)) < settings.FORM_MIN_QUESTION_BODY:
            raise forms.ValidationError(_('answer content must be at least %s characters') % settings.FORM_MIN_QUESTION_BODY)

        return value


class TagNamesField(forms.CharField):
    def __init__(self, user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.required = True
        self.widget = forms.TextInput(attrs={'size' : 50, 'autocomplete' : 'off'})
        self.max_length = 255
        self.label  = _lazy('tags')
        #self.help_text = _lazy('please use space to separate tags (this enables autocomplete feature)')
        # Formatted when shown, in the visitor's language and with the current settings.
        self.help_text = lazy(lambda: _('Tags are short keywords, with no spaces within. At least %(min)s and up to %(max)s tags can be used.') % {
            'min': settings.FORM_MIN_NUMBER_OF_TAGS, 'max': settings.FORM_MAX_NUMBER_OF_TAGS
        }, str)()
        self.initial = ''
        self.user = user

    def clean(self, value):
        super().clean(value)

        value = super().clean(value)
        data = value.strip().lower()

        split_re = re.compile(r'[ ,]+')
        list = {}
        for tag in split_re.split(data):
            list[tag] = tag

        if len(list) > settings.FORM_MAX_NUMBER_OF_TAGS or len(list) < settings.FORM_MIN_NUMBER_OF_TAGS:
            raise forms.ValidationError(_('please use between %(min)s and %(max)s tags') % { 'min': settings.FORM_MIN_NUMBER_OF_TAGS, 'max': settings.FORM_MAX_NUMBER_OF_TAGS})

        list_temp = []
        tagname_re = re.compile(r'^[\w+#\.-]+$', re.UNICODE)
        for key,tag in list.items():
            if len(tag) > settings.FORM_MAX_LENGTH_OF_TAG or len(tag) < settings.FORM_MIN_LENGTH_OF_TAG:
                raise forms.ValidationError(_('please use between %(min)s and %(max)s characters in you tags') % { 'min': settings.FORM_MIN_LENGTH_OF_TAG, 'max': settings.FORM_MAX_LENGTH_OF_TAG})
            if not tagname_re.match(tag):
                raise forms.ValidationError(_('please use following characters in tags: letters , numbers, and characters \'.#-_\''))
            # only keep one same tag
            if tag not in list_temp and len(tag.strip()) > 0:
                list_temp.append(tag)

        # Synonyms ("gis-analysis") are stored as their target tag ("gis").
        from forum.models import TagSynonym
        list_temp = TagSynonym.resolve(list_temp)

        if settings.LIMIT_TAG_CREATION and not self.user.can_create_tags():
            existent = Tag.objects.filter(name__in=list_temp).values_list('name', flat=True)

            if len(existent) < len(list_temp):
                unexistent = [n for n in list_temp if n not in existent]
                raise forms.ValidationError(_("You don't have enough reputation to create new tags. The following tags do not exist yet: %s") %
                        ', '.join(unexistent))


        return ' '.join(list_temp)

class WikiField(forms.BooleanField):
    def __init__(self, disabled=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.required = False
        self.label  = _lazy('community wiki')
        self.help_text = _lazy('if you choose community wiki option, the question and answer do not generate points and name of author will not be shown')
        if disabled:
            self.widget=forms.CheckboxInput(attrs={'disabled': "disabled"})
    def clean(self,value):
        return value

class SummaryField(forms.CharField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.required = False
        self.widget = forms.TextInput(attrs={'size' : 50, 'autocomplete' : 'off'})
        self.max_length = 300
        self.label  = _lazy('update summary:')
        self.help_text = _lazy('enter a brief summary of your revision (e.g. fixed spelling, grammar, improved style, this field is optional)')


class FeedbackForm(forms.Form):
    message = forms.CharField(label=_lazy('Your message'), max_length=800,widget=forms.Textarea(attrs={'cols':60}))
    next = NextUrlField()

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not user.is_authenticated:
            self.fields['name'] = forms.CharField(label=_('Your name'), required=False)
            self.fields['email'] = forms.EmailField(label=_('Email (not shared with anyone)'), required=True)

        # Create anti spam fields
        spam_fields = call_all_handlers('create_anti_spam_field')
        if spam_fields:
            spam_fields = dict(spam_fields)
            for name, field in spam_fields.items():
                self.fields[name] = field

            self._anti_spam_fields = spam_fields.keys()
        else:
            self._anti_spam_fields = []



class AskForm(DescribedFieldsMixin, forms.Form):
    title  = TitleField()
    text   = QuestionEditorField()

    def __init__(self, data=None, user=None, *args, **kwargs):
        super().__init__(data, *args, **kwargs)

        self.fields['tags']   = TagNamesField(user)

        if not user.is_authenticated or (int(user.reputation) < settings.CAPTCHA_IF_REP_LESS_THAN and not (user.is_superuser or user.is_staff)):
            spam_fields = call_all_handlers('create_anti_spam_field')
            if spam_fields:
                spam_fields = dict(spam_fields)
                for name, field in spam_fields.items():
                    self.fields[name] = field

                self._anti_spam_fields = spam_fields.keys()
            else:
                self._anti_spam_fields = []

        if settings.WIKI_ON:
            self.fields['wiki'] = WikiField()
        self.describe_fields()

class AnswerForm(forms.Form):
    text   = AnswerEditorField()
    wiki   = WikiField()

    def __init__(self, data=None, user=None, *args, **kwargs):
        super().__init__(data, *args, **kwargs)

        if not user.is_authenticated or (int(user.reputation) < settings.CAPTCHA_IF_REP_LESS_THAN and not (user.is_superuser or user.is_staff)):
            spam_fields = call_all_handlers('create_anti_spam_field')
            if spam_fields:
                spam_fields = dict(spam_fields)
                for name, field in spam_fields.items():
                    self.fields[name] = field

                self._anti_spam_fields = spam_fields.keys()
            else:
                self._anti_spam_fields = []

        if settings.WIKI_ON:
            self.fields['wiki'] = WikiField()

class RetagQuestionForm(DescribedFieldsMixin, forms.Form):
    tags   = TagNamesField()
    # initialize the default values
    def __init__(self, question, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tags'].initial = question.tagnames
        self.describe_fields()

class RevisionForm(forms.Form):
    """
    Lists revisions of a Question or Answer
    """
    revision = forms.ChoiceField(widget=forms.Select(attrs={'style' : 'width:520px'}))

    def __init__(self, post, *args, **kwargs):
        super().__init__(*args, **kwargs)

        revisions = post.revisions.all().values_list('revision', 'author__username', 'revised_at', 'summary').order_by('-revised_at')

        date_format = '%c'
        self.fields['revision'].choices = [
            (r[0], '%s - %s (%s) %s' % (r[0], smart_str(r[1]), r[2].strftime(date_format), r[3]))
            for r in revisions]

        self.fields['revision'].initial = post.active_revision.revision

class EditQuestionForm(DescribedFieldsMixin, forms.Form):
    title  = TitleField()
    text   = QuestionEditorField()
    summary = SummaryField()

    def __init__(self, question, user, revision=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if revision is None:
            revision = question.active_revision

        self.fields['title'].initial = revision.title
        self.fields['text'].initial = revision.body

        self.fields['tags'] = TagNamesField(user)
        self.fields['tags'].initial = revision.tagnames

        if not user.is_authenticated or (int(user.reputation) < settings.CAPTCHA_IF_REP_LESS_THAN and not (user.is_superuser or user.is_staff)):
            spam_fields = call_all_handlers('create_anti_spam_field')
            if spam_fields:
                spam_fields = dict(spam_fields)
                for name, field in spam_fields.items():
                    self.fields[name] = field

                self._anti_spam_fields = spam_fields.keys()
            else:
                self._anti_spam_fields = []

        if settings.WIKI_ON:
            self.fields['wiki'] = WikiField(disabled=(question.nis.wiki and not user.can_cancel_wiki(question)), initial=question.nis.wiki)
        self.describe_fields()

class EditAnswerForm(DescribedFieldsMixin, forms.Form):
    text = AnswerEditorField()
    summary = SummaryField()

    def __init__(self, answer, user, revision=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if revision is None:
            revision = answer.active_revision

        self.fields['text'].initial = revision.body

        if not user.is_authenticated or (int(user.reputation) < settings.CAPTCHA_IF_REP_LESS_THAN and not (user.is_superuser or user.is_staff)):
            spam_fields = call_all_handlers('create_anti_spam_field')
            if spam_fields:
                spam_fields = dict(spam_fields)
                for name, field in spam_fields.items():
                    self.fields[name] = field

                self._anti_spam_fields = spam_fields.keys()
            else:
                self._anti_spam_fields = []

        if settings.WIKI_ON:
            self.fields['wiki'] = WikiField(disabled=(answer.nis.wiki and not user.can_cancel_wiki(answer)), initial=answer.nis.wiki)
        self.describe_fields()

class EditUserForm(DescribedFieldsMixin, forms.Form):
    email = forms.EmailField(label='Email', help_text=_lazy('this email does not have to be linked to gravatar'), required=True, max_length=75, widget=forms.TextInput(attrs={'size' : 35}))
    realname = forms.CharField(label=_lazy('Real name'), required=False, max_length=255, widget=forms.TextInput(attrs={'size' : 35}))
    website = forms.URLField(label=_lazy('Website'), required=False, assume_scheme='http', max_length=255, widget=forms.TextInput(attrs={'size' : 35}))
    city = forms.CharField(label=_lazy('Location'), required=False, max_length=255, widget=forms.TextInput(attrs={'size' : 35}))
    birthday = forms.DateField(label=_lazy('Date of birth'), help_text=_lazy('will not be shown, used to calculate age, format: YYYY-MM-DD'), required=False, widget=forms.TextInput(attrs={'size' : 35}))
    about = forms.CharField(label=_lazy('Profile'), required=False, widget=forms.Textarea(attrs={'cols' : 60}))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.EDITABLE_SCREEN_NAME or (REQUEST_HOLDER.request.user.is_authenticated and REQUEST_HOLDER.request.user.is_superuser):
            self.fields['username'] = UserNameField(label=_('Screen name'))
            self.fields['username'].initial = user.username
            self.fields['username'].user_instance = user
        self.fields['email'].initial = user.email
        self.fields['realname'].initial = user.real_name
        self.fields['website'].initial = user.website
        self.fields['city'].initial = user.location

        if user.date_of_birth is not None:
            self.fields['birthday'].initial = user.date_of_birth

        self.fields['about'].initial = user.about
        self.user = user
        self.fields['email'].widget.attrs.update(autocomplete='email', autocapitalize='none', spellcheck='false')
        self.describe_fields()

    def clean_email(self):
        if self.user.email != self.cleaned_data['email']:
            if settings.EMAIL_UNIQUE:
                if 'email' in self.cleaned_data:
                    from forum.models import User
                    try:
                        User.objects.get(email = self.cleaned_data['email'])
                    except User.DoesNotExist:
                        return self.cleaned_data['email']
                    except User.MultipleObjectsReturned:
                        logging.error("Found multiple users sharing the same email: %s" % self.cleaned_data['email'])

                    raise forms.ValidationError(_('this email has already been registered, please use another one'))
        return self.cleaned_data['email']


NOTIFICATION_CHOICES = (
    ('i', _lazy('Instantly')),
    #('d', _('Daily')),
    #('w', _('Weekly')),
    ('n', _lazy('No notifications')),
)

class SubscriptionSettingsForm(forms.ModelForm):
    member_joins = forms.ChoiceField(widget=forms.RadioSelect, choices=NOTIFICATION_CHOICES)
    new_question = forms.ChoiceField(widget=forms.RadioSelect, choices=NOTIFICATION_CHOICES)
    new_question_watched_tags = forms.ChoiceField(widget=forms.RadioSelect, choices=NOTIFICATION_CHOICES)
    subscribed_questions = forms.ChoiceField(widget=forms.RadioSelect, choices=NOTIFICATION_CHOICES)
    # How often activity emails come (forum/utils/email_frequency.py). Its radio cards are
    # outside the <form> element on the page, joined to it with the form attribute.
    email_frequency = forms.ChoiceField(widget=forms.RadioSelect(attrs={'form': 'email-settings-form'}),
                                        choices=FREQUENCY_CHOICES, required=False)

    class Meta:
        model = SubscriptionSettings
        # enable_notifications (the master switch) has its own buttons on the page.
        fields = ['email_frequency', 'member_joins', 'new_question', 'new_question_watched_tags',
                  'subscribed_questions', 'questions_viewed', 'all_questions_watched_tags', 'all_questions',
                  'notify_answers', 'notify_comments_own_post', 'notify_comments', 'notify_accepted',
                  'send_digest', 'notify_reply_to_comments', 'mentions_immediate']

    def clean_email_frequency(self):
        # Left out of the post (an old page): keep what was chosen.
        return self.cleaned_data.get('email_frequency') or self.instance.email_frequency

class UserPreferencesForm(forms.Form):
    sticky_sorts = forms.BooleanField(required=False, initial=False)
    # osqa.shortcuts.js; off: data-shortcuts="off" on <html> (forum/utils/display.py).
    keyboard_shortcuts = forms.BooleanField(required=False, initial=True)



