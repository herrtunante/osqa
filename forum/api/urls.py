"""URL patterns of /api/v1/, one per path of the endpoint registry."""
from django.urls import re_path

from . import endpoints  # noqa: F401  (registers the endpoints)
from .core import dispatch, path_regex, route_name, routes


def urlpatterns_for_registry():
    patterns = []
    for path, methods in routes().items():
        params = next(iter(methods.values())).params
        patterns.append(re_path(path_regex(path, params), dispatch, {'route': path}, name=route_name(path)))
    return patterns


urlpatterns = urlpatterns_for_registry()
