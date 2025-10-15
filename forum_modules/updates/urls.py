from django.urls import re_path as url, include
from django.utils.translation import gettext as _

from views import updater_index, updater_check

urlpatterns = [url(r'^%s%s%s$' % (_('admin/'), _('updater/'), _('check/')),  updater_check, name='updater_check'),
]