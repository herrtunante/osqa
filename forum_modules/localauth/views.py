from forum.models import User
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from .forms import ClassicRegisterForm
from forum import settings
from forum.views.auth import login_and_forward, signin_providers, welcome_message
from forum.actions import UserJoinsAction
from forum.utils import accounts, security_log
from forum.utils.antispam import signup_limit_reached, count_signup


def _address_taken(email):
    return bool(settings.EMAIL_UNIQUE) and User.objects.filter(email__iexact=email).exists()


def register(request):
    """Sign-up with a username, email address and password. The new member
    gets an email with a link to confirm the address (double opt-in); until
    then EMAIL_CONFIRMATION_MODE decides what they can do. When they must
    confirm before signing in, an address that already has an account gets
    the same answer as a new one (and its owner an email), so the form doesn't
    tell anyone which addresses have accounts."""
    hide_taken = accounts.confirmation_mode() == 'signin'
    if request.method == 'POST':
        form = ClassicRegisterForm(request.POST, check_email_taken=not hide_taken)

        if signup_limit_reached(request):
            form.add_error(None, _("Too many accounts have been created from your network recently. Please try again later."))
        elif form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password1']
            email = form.cleaned_data['email']

            if hide_taken and _address_taken(email):
                count_signup(request)
                if not accounts.over_limit(request, 'signup-taken', email.lower()):
                    owners = list(User.objects.filter(email__iexact=email, is_active=True))
                    accounts.send(owners, 'auth/account_exists_email.html', {})
                security_log.event('signup_address_taken', request)
                return HttpResponseRedirect(reverse('auth_check_email'))

            user_ = User(username=username, email=email)
            user_.set_password(password)

            if User.objects.all().count() == 0:
                user_.is_superuser = True
                user_.is_staff = True
            else:
                user_.email_confirmation_required = True

            user_.save()
            count_signup(request)
            UserJoinsAction(user=user_, ip=request.META['REMOTE_ADDR']).save()

            if accounts.must_confirm_to_sign_in(user_):
                return HttpResponseRedirect(reverse('auth_check_email'))
            return login_and_forward(request, user_, None, welcome_message(user_))
    else:
        form = ClassicRegisterForm(initial={'next':'/'})

    return render(request, 'auth/complete.html', {
        'form1': form,
        # "Continue with Google" also creates an account.
        'button_providers': signin_providers(request)['button_providers'],
        'must_confirm_to_sign_in': hide_taken,
        })
