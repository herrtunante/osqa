import json
from django.http import HttpResponseForbidden

def ajax_login_required(view_func):
    def wrap(request,*args,**kwargs):
        if request.user.is_authenticated:
            return view_func(request,*args,**kwargs)
        else:
            return HttpResponseForbidden(json.dumps({'login_required':True}), content_type='application/json')
    return wrap

