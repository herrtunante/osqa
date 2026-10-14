from django.urls import re_path
from django.utils.translation import gettext as _

from . import views

urlpatterns = [
    re_path(r'^%s$' % 'ai/duplicates/', views.duplicate_check, name='ai_duplicate_check'),
    re_path(r'^%s$' % 'ai/assistant/', views.assistant_answer, name='ai_assistant'),
]
