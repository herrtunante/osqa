from django.urls import re_path as url, include
from django.http import  HttpResponse
import settings

urlpatterns = [(r'^robots.txt$',  lambda r: HttpResponse(settings.ROBOTS_FILE.value, content_type='text/plain')),
]