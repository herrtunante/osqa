"""The notification list behind the bell in the header."""
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from forum import notifications
from forum.models import Notification
from forum.modules import decorate
from forum.views.decorators import login_required

PAGE_SIZE = 50


@decorate.withfn(login_required)
def notification_list(request):
    notifications.prune(request.user)
    items = list(Notification.objects.filter(user=request.user)
                 .select_related('actor', 'node', 'node__abs_parent')[:PAGE_SIZE])
    return render(request, 'inbox/list.html', {
        'items': items,
        'unread': sum(1 for item in items if item.is_unread),
    })


@decorate.withfn(login_required)
def notification_open(request, id):
    """Marks one notification read and goes to what it is about."""
    item = get_object_or_404(Notification, id=id, user=request.user)
    notifications.mark_read(request.user, id=item.id)
    if item.kind == Notification.BADGE or item.node is None:
        return HttpResponseRedirect(request.user.get_profile_url())
    return HttpResponseRedirect(item.node.leaf.get_absolute_url())


@require_POST
@decorate.withfn(login_required)
def notification_mark_all_read(request):
    notifications.mark_read(request.user)
    return HttpResponseRedirect(reverse('notifications'))


def notification_count(request):
    """{"unread": n} for the bell's periodic refresh."""
    response = JsonResponse({'unread': notifications.unread_count(request.user)})
    response['Cache-Control'] = 'no-store'
    return response
