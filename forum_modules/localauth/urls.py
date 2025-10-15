from django.urls import re_path as url, include
from django.utils.translation import gettext as _
import views as app

urlpatterns = [url(r'^%s%s%s$' % (_('account/'), _('local/'),  _('register/')), app.register, name='auth_local_register'),
]