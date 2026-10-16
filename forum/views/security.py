"""The account security page (passkeys, two-step verification, recovery
codes), the passkey calls of media/js/osqa.passkeys.js and the verification
step of signing in. Logic in forum/utils/two_factor.py and forum/utils/passkeys.py."""
import datetime
import json
import logging

from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from forum.http_responses import HttpResponseUnauthorized
from forum.models import Passkey, User
from forum.utils import passkeys, security_log, two_factor

MAX_BODY = 64 * 1024


def _json_body(request):
    if len(request.body) > MAX_BODY:
        return None
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _security_url(user):
    return reverse('user_security', kwargs={'id': user.id})


def _own_user(request):
    if not request.user.is_authenticated:
        return None
    return request.user


def _paused_message():
    return _("Too many wrong codes. Wait %(minutes)s minutes and try again.") % {
        'minutes': two_factor.lockout_minutes()}


def _proof_ok(request, user, purpose):
    """For turning two-step verification off or new recovery codes: a current
    code (or recovery code) or the account password. Returns an error or None."""
    code = request.POST.get('code', '').strip()
    password = request.POST.get('password', '')
    if code:
        result = two_factor.check_code(request, user, code, purpose)
        if result == two_factor.PAUSED:
            return _paused_message()
        return None if result in (two_factor.OK_TOTP, two_factor.OK_RECOVERY) else _("That code is not right.")
    if password and user.has_usable_password():
        if two_factor.paused(request, user):
            return _paused_message()
        if user.check_password(password):
            return None
        two_factor.count_failure(request, user, purpose)
        return _("That password is not right.")
    return _("Enter a code from your authenticator app or your password.")


def security_page(request, id):
    view_user = get_object_or_404(User, id=id)
    if not request.user.is_authenticated or not (request.user == view_user or request.user.is_superuser):
        return HttpResponseUnauthorized(request)
    own = request.user == view_user
    return render(request, 'users/security.html', {
        'view_user': view_user,
        'own': own,
        'passkeys': view_user.passkeys.all(),
        'totp_enabled': two_factor.is_enabled(view_user),
        'recovery_left': two_factor.recovery_codes_left(view_user),
        'has_password': view_user.has_usable_password(),
        'staff_required': own and two_factor.staff_must_set_up(request),
        'max_passkeys': passkeys.MAX_PASSKEYS,
    })


# -- authenticator app ---------------------------------------------------------

@never_cache
def totp_setup(request):
    user = _own_user(request)
    if user is None:
        return HttpResponseRedirect(reverse('auth_signin'))
    if two_factor.is_enabled(user):
        return HttpResponseRedirect(_security_url(user))
    dev = two_factor.setup_device(user)
    error = None
    if request.method == 'POST':
        result = two_factor.check_code(request, user, request.POST.get('code', ''), 'setup', recovery=False, dev=dev)
        if result == two_factor.OK_TOTP:
            type(dev).objects.filter(pk=dev.pk).update(confirmed_at=datetime.datetime.now())
            codes = two_factor.new_recovery_codes(user)
            # This session has just shown the second factor.
            request.session[two_factor.METHOD_SESSION_KEY] = 'totp'
            security_log.event('two_factor_enabled', request, account=user)
            return render(request, 'users/recovery_codes.html', {
                'view_user': user, 'codes': codes, 'just_enabled': True})
        if result == two_factor.PAUSED:
            error = _paused_message()
        else:
            error = _("That code is not right. Check that the time on your phone is correct and enter the "
                      "newest code.")
    raw = two_factor.decrypt_secret(dev.secret)
    uri = two_factor.provisioning_uri(raw, user.username)
    return render(request, 'users/two_factor_setup.html', {
        'view_user': user,
        'qr_svg': two_factor.qr_svg(uri, _("QR code for your authenticator app")),
        'secret': two_factor.secret_text(raw),
        'otpauth_uri': uri,
        'error': error,
    })


@require_POST
def totp_disable(request, id):
    view_user = get_object_or_404(User, id=id)
    if not request.user.is_authenticated or not (request.user == view_user or request.user.is_superuser):
        return HttpResponseUnauthorized(request)
    if request.user == view_user:
        error = _proof_ok(request, view_user, 'disable')
        if error:
            messages.error(request, error)
            return HttpResponseRedirect(_security_url(view_user))
    two_factor.disable(view_user)
    security_log.event('two_factor_disabled', request, logging.WARNING, account=view_user)
    if request.user == view_user:
        request.session.pop(two_factor.METHOD_SESSION_KEY, None)
        messages.info(request, _("Two-step verification is off."))
    else:
        messages.info(request, _("Two-step verification is off for %s.") % view_user.username)
    return HttpResponseRedirect(_security_url(view_user))


@never_cache
@require_POST
def recovery_codes(request):
    user = _own_user(request)
    if user is None:
        return HttpResponseRedirect(reverse('auth_signin'))
    if not two_factor.is_enabled(user):
        return HttpResponseRedirect(_security_url(user))
    error = _proof_ok(request, user, 'recovery_codes')
    if error:
        messages.error(request, error)
        return HttpResponseRedirect(_security_url(user))
    codes = two_factor.new_recovery_codes(user)
    security_log.event('recovery_codes_regenerated', request, account=user)
    return render(request, 'users/recovery_codes.html', {'view_user': user, 'codes': codes, 'just_enabled': False})


# -- passkeys --------------------------------------------------------------------

PASSKEY_ERRORS = {
    'challenge': lambda: _("This request has expired. Please try again."),
    'too_many': lambda: _("You have too many passkeys. Remove one first."),
    'exists': lambda: _("This passkey is already registered."),
    'unknown': lambda: _("This passkey is not registered on this site. Sign in another way and add it on your "
                         "security page."),
    'invalid': lambda: _("The passkey could not be verified. Please try again."),
}


@require_POST
def passkey_register_options(request):
    user = _own_user(request)
    if user is None:
        return _error(_('Please sign in first.'), 403)
    return JsonResponse(passkeys.registration_options(request, user))


@require_POST
def passkey_register(request):
    user = _own_user(request)
    if user is None:
        return _error(_('Please sign in first.'), 403)
    data = _json_body(request)
    if data is None or not isinstance(data.get('credential'), dict):
        return _error(_('Invalid request.'))
    name = str(data.get('name') or '').strip()[:64] or _("Passkey")
    try:
        passkeys.register(request, user, data['credential'], name)
    except passkeys.PasskeyError as e:
        return _error(PASSKEY_ERRORS[str(e)]())
    messages.info(request, _("Passkey added. Next time you can sign in with it."))
    return JsonResponse({'redirect': _security_url(user)})


@require_POST
def passkey_remove(request, id):
    passkey = get_object_or_404(Passkey, id=id)
    if not request.user.is_authenticated or not (request.user == passkey.user or request.user.is_superuser):
        return HttpResponseUnauthorized(request)
    owner = passkey.user
    passkey.delete()
    security_log.event('passkey_removed', request, account=owner, passkey=id)
    messages.info(request, _("The passkey \"%s\" was removed.") % passkey.name)
    return HttpResponseRedirect(_security_url(owner))


@require_POST
def passkey_auth_options(request):
    return JsonResponse(passkeys.authentication_options(request))


@require_POST
def passkey_auth(request):
    from forum.views.auth import login_and_forward
    data = _json_body(request)
    if data is None or not isinstance(data.get('credential'), dict):
        return _error(_('Invalid request.'))
    try:
        passkey = passkeys.authenticate(request, data['credential'])
    except passkeys.PasskeyError as e:
        return _error(PASSKEY_ERRORS[str(e)]())
    user = passkey.user
    two_factor.clear_pending(request)
    security_log.event('passkey_login', request, account=user, passkey=passkey.id)
    response = login_and_forward(request, user, two_factor_method='passkey')
    return JsonResponse({'redirect': response.get('Location') or reverse('index')})


# -- the verification step of signing in -------------------------------------------

@never_cache
def verify(request):
    from forum.views.auth import login_and_forward
    user, data = two_factor.pending(request)
    if user is None:
        if request.method == 'POST':
            request.session['auth_error'] = _("Your sign-in took too long. Please sign in again.")
        return HttpResponseRedirect(reverse('auth_signin'))
    error = None
    if request.method == 'POST':
        result = two_factor.check_code(request, user, request.POST.get('code', ''), 'signin')
        if result in (two_factor.OK_TOTP, two_factor.OK_RECOVERY):
            two_factor.clear_pending(request)
            left = two_factor.recovery_codes_left(user)
            if two_factor.AFTER_RESET_PASSWORD in data['after']:
                user.set_unusable_password()
                user.save()
            response = login_and_forward(request, user, data['forward'], data['message'], two_factor_method=result)
            if result == two_factor.OK_RECOVERY:
                messages.warning(request, _("You signed in with a recovery code. You have %(left)s left; you can "
                                            "make new ones on your security page.") % {'left': left})
            return response
        if result == two_factor.PAUSED:
            error = _paused_message()
        else:
            error = _("That code is not right. Enter the newest code from your app, or a recovery code.")
    return render(request, 'auth/two_factor_verify.html', {
        'error': error,
        'has_passkeys': user.passkeys.exists(),
    })


def require_staff_two_factor(view):
    """Decorator for admin views: staff who must have two-step verification
    (setting TWO_FACTOR_REQUIRED_FOR_STAFF) and don't are sent to set it up."""
    def wrapper(request, *args, **kwargs):
        if two_factor.staff_must_set_up(request):
            security_log.event('two_factor_required_for_staff', request, path=request.path)
            messages.warning(request, _("Turn on two-step verification to use the admin pages."))
            return HttpResponseRedirect(_security_url(request.user))
        return view(request, *args, **kwargs)
    wrapper.__name__ = getattr(view, '__name__', 'wrapper')
    wrapper.__doc__ = getattr(view, '__doc__', None)
    return wrapper

