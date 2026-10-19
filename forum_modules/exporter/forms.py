from django import forms
from django.utils.translation import gettext_lazy as _lazy

FORMAT_CHOICES = (
('tgz', _lazy('tar.gz')),
('zip', _lazy('zip'))
)

class ExporterForm(forms.Form):
    file_format = forms.ChoiceField(widget=forms.Select, choices=FORMAT_CHOICES, initial='zip',
                                       label=_lazy('File format'), help_text=_lazy("File format of the compressed backup"), required=True)
    anon_data = forms.BooleanField(label=_lazy('Anonymized data'), help_text=_lazy('Don\'t export user data and make all content anonymous'), required=False)
    uplodaded_files = forms.BooleanField(label=_lazy('Uploaded files'), help_text=_lazy('Include uploaded files in the backup'), required=False, initial=True)
    import_skins_folder = forms.BooleanField(label=_lazy('Skins folder'), help_text=_lazy('Include skins folder in the backup'), required=False, initial=False)

class ImporterForm(forms.Form):
    dump = forms.FileField(label=_lazy('Backup file'), help_text=_lazy('A backup made with the XML data exporter (zip or tar.gz)'), required=True)
