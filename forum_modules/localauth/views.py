from forum.models import User
from django.shortcuts import render
from django.utils.translation import gettext as _

from .forms import ClassicRegisterForm
from forum.views.auth import login_and_forward
from forum.actions import UserJoinsAction
from forum.utils.antispam import signup_limit_reached, count_signup

def register(request):
    if request.method == 'POST':
        form = ClassicRegisterForm(request.POST)

        if signup_limit_reached(request):
            form.add_error(None, _("Too many accounts have been created from your network recently. Please try again later."))
        elif form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password1']
            email = form.cleaned_data['email']

            user_ = User(username=username, email=email)
            user_.set_password(password)

            if User.objects.all().count() == 0:
                user_.is_superuser = True
                user_.is_staff = True

            user_.save()
            count_signup(request)
            UserJoinsAction(user=user_, ip=request.META['REMOTE_ADDR']).save()

            return login_and_forward(request, user_, None, _("A welcome email has been sent to your email address. "))
    else:
        form = ClassicRegisterForm(initial={'next':'/'})

    return render(request, 'auth/complete.html', {
        'form1': form
        })
