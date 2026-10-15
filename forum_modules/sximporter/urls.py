from django.urls import re_path as url

from .views import sximporter

urlpatterns = [url(r'^%s%s$' % ('admin/', 'sximporter/'),  sximporter, name='sximporter'),
]
