import socket
from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.contrib.admin.widgets import FilteredSelectMultiple, AdminDateWidget
from .qanda import TitleField, EditorField
from forum import settings
from forum.models.node import NodeMetaClass
from forum.models import User

class IPListField(forms.CharField):
    def clean(self, value):
        ips = [ip.strip() for ip in value.strip().strip(',').split(',')]
        iplist = []

        if len(ips) < 1:
            raise forms.ValidationError(_('Please input at least one ip address'))

        for ip in ips:
            try:
                socket.inet_aton(ip)
            except socket.error:
                raise forms.ValidationError(_('Invalid ip address: %s' % ip))

            if not len(ip.split('.')) == 4:
                raise forms.ValidationError(_('Please use the dotted quad notation for the ip addresses'))

            iplist.append(ip)

        return iplist

class MaintenanceModeForm(forms.Form):
    ips = IPListField(label=_lazy('Allow ips'),
                      help_text=_lazy('Comma separated list of ips allowed to access the site while in maintenance'),
                      required=True,
                      widget=forms.TextInput(attrs={'class': 'longstring'}))

    message = forms.CharField(label=_lazy('Message'),
                              help_text=_lazy('A message to display to your site visitors while in maintainance mode'),
                              widget=forms.Textarea)


TEMPLATE_CHOICES = (
('default', _lazy('Default')),
('sidebar', _lazy('Default with sidebar')),
('none', _lazy('None')),
)

RENDER_CHOICES = (
('markdown', _lazy('Markdown')),
('html', _lazy('HTML')),
('escape', _lazy('Escaped'))
)

class UrlFieldWidget(forms.TextInput):
    def render(self, name, value, attrs=None, renderer=None):
        if not value:
            value = ''

        return """
                <input class="url_field" type="text" name="%(name)s" value="%(value)s" />
                <a class="url_field_anchor" target="_blank" href="%(app_url)s"></a>
            """  % {'name': name, 'value': value, 'app_url': settings.APP_URL}


class PageForm(forms.Form):
    def __init__(self, page, *args, **kwargs):
        if page:
            initial = page.extra
            initial.update(dict(title=page.title, content=page.body))
            super(PageForm, self).__init__(initial=initial, *args, **kwargs)
        else:
            super(PageForm, self).__init__(*args, **kwargs)


    title  = forms.CharField(label=_lazy('Title'), max_length=255, widget=forms.TextInput(attrs={'class': 'longstring'}),
                             initial='New page')
    path  = forms.CharField(label=_lazy('Page URL'), widget=UrlFieldWidget, initial='pages/new/')

    content = forms.CharField(label=_lazy('Page Content'), widget=forms.Textarea(attrs={'rows': 30}))
    mimetype = forms.CharField(label=_lazy('Mime Type'), initial='text/html')

    render = forms.ChoiceField(widget=forms.RadioSelect, choices=RENDER_CHOICES, initial='markdown',
                               label=_lazy('Render Mode'))

    template = forms.ChoiceField(widget=forms.RadioSelect, choices=TEMPLATE_CHOICES, initial='default',
                                 label=_lazy('Template'))
    sidebar = forms.CharField(label=_lazy('Sidebar Content'), widget=forms.Textarea(attrs={'rows': 20}), required=False)
    sidebar_wrap = forms.BooleanField(label=_lazy("Wrap sidebar block"), initial=True, required=False)
    sidebar_render = forms.ChoiceField(widget=forms.RadioSelect, choices=RENDER_CHOICES, initial='markdown',
                                       label=_lazy('Sidebar Render Mode'))

    comments = forms.BooleanField(label=_lazy("Allow comments"), initial=False, required=False)

from forum.forms.auth import SimpleRegistrationForm
from forum.forms.general import SetPasswordForm


class CreateUserForm(SimpleRegistrationForm, SetPasswordForm):
    validate_email = forms.BooleanField(required=False, label=_lazy('send validation email'))

    def __init__(self, *args, **kwargs):
        super(CreateUserForm, self).__init__(*args, **kwargs)
        # Keep only these fields, in this order (fields.keyOrder was removed in Django 1.7).
        self.fields = dict((name, self.fields[name]) for name in
                           ['username', 'email', 'validate_email', 'password1', 'password2'])
        self.fields['email'].label = _('email address')


    
