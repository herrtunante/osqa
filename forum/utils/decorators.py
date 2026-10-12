import json
from django.http import HttpResponse, HttpResponseForbidden, Http404

def ajax_login_required(view_func):
    def wrap(request,*args,**kwargs):
        if request.user.is_authenticated:
            return view_func(request,*args,**kwargs)
        else:
            return HttpResponseForbidden(json.dumps({'login_required':True}), content_type='application/json')
    return wrap

def ajax_method(view_func):
    def wrap(request,*args,**kwargs):
        if not request.headers.get('x-requested-with') == 'XMLHttpRequest':
            raise Http404
        retval = view_func(request,*args,**kwargs)
        if isinstance(retval, HttpResponse):
            retval['Content-Type'] = 'application/json'
            return retval
        else:
            return HttpResponse(json.dumps(retval), content_type='application/json')
    return wrap
            
