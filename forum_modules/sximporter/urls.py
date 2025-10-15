from django.urls import re_path as url, include
from django.utils.translation import gettext as _

from views import sximporter

urlpatterns = [url(r'^%s%s$' % (_('admin/'), _('sximporter/')),  sximporter, name='sximporter'),
]