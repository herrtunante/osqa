from django.urls import re_path as url, include
from django.utils.translation import gettext as _

from views import stats_index, stats_check

urlpatterns = [url(r'^%s%s%s$' % (_('admin/'), _('stats/'), _('check/')),  stats_check, name='stats_check'),
]