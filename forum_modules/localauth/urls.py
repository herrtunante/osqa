from django.urls import re_path as url, include
from django.utils.translation import gettext as _
from . import views as app

urlpatterns = [url(r'^%s%s%s$' % ('account/', 'local/',  'register/'), app.register, name='auth_local_register'),
]