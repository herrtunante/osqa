from django.http import HttpResponseRedirect, HttpResponse, Http404, HttpResponsePermanentRedirect
from django.template.defaultfilters import slugify

from forum.views import readers
from forum.modules import decorate
from forum.models import Question

from . import settings
import logging

@decorate(readers.question, needs_origin=True)
def match_question_slug(origin, request, id, slug='', answer=None):
    try:
        id = int(id)
    except:
        raise Http404()

    merged_nodes = settings.MERGE_MAPPINGS and settings.MERGE_MAPPINGS.get('merged_nodes', {}) or {}

    if id in merged_nodes:
        # An old link to a question imported from a backup: the id is now used
        # by another question (with another slug), or by none at all.
        try:
            question = Question.objects.get(id=id)

            if slug != slugify(question.title):
                return origin(request, merged_nodes[id], slug, answer)

        except Question.DoesNotExist:
            return origin(request, merged_nodes[id], slug, answer)

    return origin(request, id, slug, answer)