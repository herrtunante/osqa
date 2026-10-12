from django.urls import re_path as url, include
from django.http import  HttpResponse
from . import settings

urlpatterns = [
    url(r'^robots.txt$', lambda r: HttpResponse(settings.ROBOTS_FILE.value, content_type='text/plain')),
]