from django.urls import re_path, include
from django.utils.translation import gettext as _
from django.conf import settings

urlpatterns = [
    re_path(r'', include('forum.urls')),
]

if 'rosetta' in settings.INSTALLED_APPS:
    urlpatterns = [
        re_path(r'^rosetta/', include('rosetta.urls')),
    ] + urlpatterns

handler404 = 'forum.views.meta.page'
handler500 = 'forum.views.meta.error_handler'
