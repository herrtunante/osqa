from django.core.cache import cache
from django.http import HttpResponseNotAllowed, JsonResponse

from .client import is_configured
from .duplicates import find_duplicates
from .settings import AI_DUPLICATE_DETECTION, AI_DUPLICATE_CHECKS_PER_HOUR


def enabled():
    return is_configured() and bool(AI_DUPLICATE_DETECTION)


def rate_limiter(request):
    """Counts uncached duplicate checks per user (or IP) in an hourly window."""
    who = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR', '')
    key = 'ai-dup-rate:%s' % who

    def may_call_api():
        used = cache.get(key, 0)
        if used >= int(AI_DUPLICATE_CHECKS_PER_HOUR):
            return False
        if used:
            cache.incr(key)
        else:
            cache.set(key, 1, 3600)
        return True

    return may_call_api


def duplicate_check(request):
    """Existing questions that ask the same thing as the title being written."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    title = ' '.join(request.POST.get('title', '').split())[:300]
    body = request.POST.get('text', '')[:5000]
    if not enabled() or len(title) < 15 or len(title.split()) < 3:
        return JsonResponse({'duplicates': []})

    duplicates = find_duplicates(title, body, rate_limiter(request))
    return JsonResponse({'duplicates': [{
        'id': question.id,
        'title': question.title,
        'url': question.get_absolute_url(),
        'answers': question.answer_count,
        'accepted': question.accepted_count > 0,
        'reason': reason,
    } for question, reason in duplicates]})
