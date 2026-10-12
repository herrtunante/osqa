from django.urls import re_path
from django.utils.translation import gettext as _

from .views import state, running, download

urlpatterns = [re_path(r'^%s%s%s$' % (_('admin/'), _('exporter/'), _('state/')),  state, name='exporter_state'),
    re_path(r'^%s(?P<mode>\w+)/%s$' % (_('admin/'), _('running/')),  running, name='exporter_running'),
    re_path(r'^%s%s%s$' % (_('admin/'), _('exporter/'), _('download/')),  download, name='exporter_download'),
]