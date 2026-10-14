# -*- coding: utf-8 -*-

import datetime
import logging
import urllib.parse
from urllib.parse import urlparse

from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from forum.utils.urls import root_relative_reverse
from django.http import HttpResponseRedirect, Http404
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.encoding import smart_str
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import login, logout, update_session_auth_hash
from django.views.decorators.http import require_POST

from django.contrib import messages

from .writers import manage_pending_data

from forum.actions import EmailValidationAction
from forum.utils import html
from forum.views.decorators import login_required
from forum.modules import decorate
from forum.forms import PublicRegistrationForm, TemporaryLoginRequestForm, ChangePasswordForm, SetPasswordForm
from forum.http_responses import HttpResponseUnauthorized
from forum.utils.mail import send_template_email
from forum.utils.redact import redact_meta
from forum.authentication.base import InvalidAuthentication
from forum.authentication import AUTH_PROVIDERS
from forum.models import User, AuthKeyUserAssociation, ValidationHash
from forum.actions import UserJoinsAction, UserLoginAction
from forum.utils.antispam import signup_limit_reached, count_signup
from forum.utils import security_log, throttle
from forum import settings

from .vars import ON_SIGNIN_SESSION_ATTR, PENDING_SUBMISSION_SESSION_ATTR

def signin_page(request):
    referer = request.META.get('HTTP_REFERER', '/')

    # If the referer is equal to the sign up page, e. g. if the previous login attempt was not successful we do not
    # change the sign in URL. The user should go to the same page.
    # The referer is sent by the browser and may point to another site; only
    # remember it as the post-login destination if it is on this site.
    on_this_site = url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure())

    if on_this_site and not referer.replace(settings.APP_URL, '') == reverse('auth_signin'):
        request.session[ON_SIGNIN_SESSION_ATTR] = referer

    # Providers that need configuration (e.g. OAuth client keys) define is_enabled().
    all_providers = [provider.context for provider in AUTH_PROVIDERS.values()
                     if provider.context and getattr(provider.context, 'is_enabled', lambda: True)()]

    sort = lambda c: c.weight
    can_show = lambda c: not request.user.is_authenticated or c.show_to_logged_in_user

    bigicon_providers = sorted([
    context for context in all_providers if context.mode == 'BIGICON' and can_show(context)
    ], key=sort)

    smallicon_providers = sorted([
    context for context in all_providers if context.mode == 'SMALLICON' and can_show(context)
    ], key=sort)

    top_stackitem_providers = sorted([
    context for context in all_providers if context.mode == 'TOP_STACK_ITEM' and can_show(context)
    ], key=sort)

    stackitem_providers = sorted([
    context for context in all_providers if context.mode == 'STACK_ITEM' and can_show(context)
    ], key=sort)

    try:
        msg = request.session['auth_error']
        del request.session['auth_error']
    except:
        msg = None

    return render(
            request,
            'auth/signin.html',
            {
            'msg': msg,
            'all_providers': all_providers,
            'bigicon_providers': bigicon_providers,
            'top_stackitem_providers': top_stackitem_providers,
            'stackitem_providers': stackitem_providers,
            'smallicon_providers': smallicon_providers,
            })

def login_paused(request, username):
    limit = int(settings.LOGIN_FAILURES_LIMIT)
    return limit > 0 and (throttle.count('login-user', username) >= limit or
                          throttle.count('login-ip', throttle.client_ip(request)) >= limit * 4)


def count_login_failure(request, username):
    seconds = int(settings.LOGIN_LOCKOUT_MINUTES) * 60
    throttle.hit('login-user', username, seconds)
    throttle.hit('login-ip', throttle.client_ip(request), seconds)


def prepare_provider_signin(request, provider):
    # Django 1.7+: request.REQUEST removed, use request.GET for query parameters
    force_email_request = request.GET.get('validate_email', 'yes') == 'yes'
    request.session['force_email_request'] = force_email_request

    if provider in AUTH_PROVIDERS:
        provider_class = AUTH_PROVIDERS[provider].consumer

        try:
            request_url = provider_class.prepare_authentication_request(request,
                                                                        root_relative_reverse('auth_provider_done',
                                                                                kwargs={'provider': provider}))

            return HttpResponseRedirect(request_url)
        except NotImplementedError as e:
            return process_provider_signin(request, provider)
        except InvalidAuthentication as e:
            request.session['auth_error'] = e.message

        return HttpResponseRedirect(reverse('auth_signin'))
    else:
        raise Http404()


def process_provider_signin(request, provider):
    if provider in AUTH_PROVIDERS:
        provider_class = AUTH_PROVIDERS[provider].consumer

        # Password sign-ins (local, LDAP) are paused after too many failures.
        username = request.POST.get('username', '').strip() if request.method == 'POST' and \
            'password' in request.POST else None
        if username and login_paused(request, username):
            security_log.event('login_paused', request, logging.WARNING, provider=provider, username=username)
            request.session['auth_error'] = _(
                "Too many failed sign-in attempts. Please wait %(minutes)s minutes, or get a sign-in link by email "
                "with \"forgot password\".") % {'minutes': int(settings.LOGIN_LOCKOUT_MINUTES)}
            return HttpResponseRedirect(reverse('auth_signin'))

        try:
            assoc_key = provider_class.process_authentication_request(request)
        except InvalidAuthentication as e:
            if username:
                count_login_failure(request, username)
                security_log.event('login_failed', request, logging.WARNING, provider=provider, username=username)
            request.session['auth_error'] = e.message
            return HttpResponseRedirect(reverse('auth_signin'))

        if username:
            throttle.reset('login-user', username)

        if request.user.is_authenticated:
            if isinstance(assoc_key, (type, User)):
                if request.user != assoc_key:
                    request.session['auth_error'] = _(
                            "Sorry, these login credentials belong to anoother user. Plese terminate your current session and try again."
                            )
                else:
                    request.session['auth_error'] = _("You are already logged in with that user.")
            else:
                try:
                    assoc = AuthKeyUserAssociation.objects.get(key=assoc_key)
                    if assoc.user == request.user:
                        request.session['auth_error'] = _(
                                "These login credentials are already associated with your account.")
                    else:
                        request.session['auth_error'] = _(
                                "Sorry, these login credentials belong to anoother user. Plese terminate your current session and try again."
                                )
                except:
                    uassoc = AuthKeyUserAssociation(user=request.user, key=assoc_key, provider=provider)
                    uassoc.save()
                    messages.info(request, _('The new credentials are now associated with your account'))
                    return HttpResponseRedirect(reverse('user_authsettings', args=[request.user.id]))

            return HttpResponseRedirect(reverse('auth_signin'))
        else:
            if isinstance(assoc_key, User):
                return login_and_forward(request, assoc_key)

        try:
            assoc = AuthKeyUserAssociation.objects.get(key=assoc_key)
            user_ = assoc.user
            return login_and_forward(request, user_)
        except AuthKeyUserAssociation.DoesNotExist:
            request.session['assoc_key'] = assoc_key
            request.session['auth_provider'] = provider
            return HttpResponseRedirect(reverse('auth_external_register'))

    return HttpResponseRedirect(reverse('auth_signin'))

def external_register(request):
    if request.method == 'POST' and 'bnewaccount' in request.POST:
        form1 = PublicRegistrationForm(request.POST)

        if signup_limit_reached(request):
            form1.add_error(None, _("Too many accounts have been created from your network recently. Please try again later."))
        elif form1.is_valid():
            user_ = User(username=form1.cleaned_data['username'], email=form1.cleaned_data['email'], real_name=form1.cleaned_data['real_name'])
            user_.email_isvalid = request.session.get('auth_validated_email', '') == form1.cleaned_data['email']
            user_.set_unusable_password()

            if User.objects.all().count() == 0:
                user_.is_superuser = True
                user_.is_staff = True

            user_.save()
            count_signup(request)
            UserJoinsAction(user=user_, ip=request.META['REMOTE_ADDR']).save()

            try:
                assoc_key = request.session['assoc_key']
                auth_provider = request.session['auth_provider']
            except:
                request.session['auth_error'] = _(
                        "Oops, something went wrong in the middle of this process. Please try again. Note that you need to have cookies enabled for the authentication to work."
                        )
                logging.error("Missing session data when trying to complete user registration: %s" % ", ".join(
                        ["%s: %s" % (k, v) for k, v in redact_meta(request.META)]))
                return HttpResponseRedirect(reverse('auth_signin'))

            uassoc = AuthKeyUserAssociation(user=user_, key=assoc_key, provider=auth_provider)
            uassoc.save()

            del request.session['assoc_key']
            del request.session['auth_provider']

            return login_and_forward(request, user_, message=_("A welcome email has been sent to your email address. "))
    else:
        auth_provider = request.session.get('auth_provider', None)
        if not auth_provider:
            request.session['auth_error'] = _(
                    "Oops, something went wrong in the middle of this process. Please try again.")
            logging.error("Missing session data when trying to complete user registration: %s" % ", ".join(
                    ["%s: %s" % (k, v) for k, v in redact_meta(request.META)]))
            return HttpResponseRedirect(reverse('auth_signin'))

        provider_class = AUTH_PROVIDERS[auth_provider].consumer

        user_data = provider_class.get_user_data(request.session['assoc_key'])

        if not user_data:
            user_data = request.session.get('auth_consumer_data', {})

        username = user_data.get('username', '')
        email = user_data.get('email', '')
        real_name = user_data.get('real_name', '')

        # Only treat the address as validated when the provider says it verified it.
        if email and user_data.get('email_verified', True):
            request.session['auth_validated_email'] = email

        form1 = PublicRegistrationForm(initial={
        'next': '/',
        'username': username,
        'email': email,
        'real_name': real_name
        })

    provider_context = AUTH_PROVIDERS[request.session['auth_provider']].context

    return render(request, 'auth/complete.html', {
    'form1': form1,
    'provider':provider_context and mark_safe(provider_context.human_name) or _('unknown'),
    'login_type':provider_context.id,
    'gravatar_faq_url':reverse('faq') + '#gravatar',
    })

def request_temp_login(request):
    if request.method == 'POST':
        form = TemporaryLoginRequestForm(request.POST)

        if form.is_valid():
            email = form.cleaned_data['email']
            limit = int(settings.PASSWORD_RESETS_PER_HOUR)
            ip = throttle.client_ip(request)
            if limit > 0 and (throttle.hit('templogin-email', email, 3600) > limit or
                              throttle.hit('templogin-ip', ip, 3600) > limit * 4):
                security_log.event('temp_login_limited', request, logging.WARNING, email=email)
                messages.error(request, _("Too many sign-in links were requested. Please try again in an hour."))
                return render(request, 'auth/temp_login_request.html', {'form': form})

            for u in form.user_cache:
                if u.is_suspended():
                    continue
                # A fresh link each time; the previous one stops working.
                ValidationHash.objects.filter(user=u, type='templogin').delete()
                hash = ValidationHash.objects.create_new(u, 'templogin', [u.id])
                send_template_email([u], "auth/temp_login_email.html", {'temp_login_code': hash})
                security_log.event('temp_login_requested', request, account=u)

            messages.info(request, _("If this address belongs to an account, we have sent it an email with a sign-in "
                                     "link. The link works once, for 24 hours."))
            return HttpResponseRedirect(reverse('index'))
    else:
        form = TemporaryLoginRequestForm()

    return render(request, 'auth/temp_login_request.html', {'form': form})

def temp_signin(request, user, code):
    user = get_object_or_404(User, id=user)

    if (ValidationHash.objects.validate(code, user, 'templogin', [user.id])):
        
        # If the user requests temp_signin he must have forgotten his password. So we mark it as unusable.
        user.set_unusable_password()
        user.save()
        security_log.event('temp_login_used', request, account=user)
        
        return login_and_forward(request, user, reverse('user_authsettings', kwargs={'id': user.id}),
                                 _(
                                         "You are logged in with a temporary access key, please take the time to fix your issue with authentication."
                                         ))
    else:
        raise Http404()

def send_validation_email(request):
    if not request.user.is_authenticated:
        return HttpResponseUnauthorized(request)
    else:
        # We check if there are some old validation hashes. If there are -- we delete them.
        try:
            hash = ValidationHash.objects.get(user=request.user, type='email')
            hash.delete()
        except:
            pass

        # We don't care if there are previous cashes in the database... In every case we have to create a new one
        hash = ValidationHash.objects.create_new(request.user, 'email', [request.user.email])

        additional_get_params = urllib.parse.urlencode(dict([k, v.encode('utf-8')] for k, v in request.GET.items()))
        send_template_email([request.user], "auth/mail_validation.html", {
            'validation_code': hash,
            'additional_get_params' : additional_get_params
        })

        messages.info(request, _("A message with an email validation link was just sent to your address."))
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

        

def validate_email(request, user, code):
    user = get_object_or_404(User, id=user)

    if (ValidationHash.objects.validate(code, user, 'email', [user.email])):
        EmailValidationAction(user=user, ip=request.META['REMOTE_ADDR']).save()
        return login_and_forward(request, user, reverse('index'), _("Thank you, your email is now validated."))
    else:
        return render(request, 'auth/mail_already_validated.html', { 'user' : user })

def auth_settings(request, id):
    user_ = get_object_or_404(User, id=id)

    if not (request.user.is_superuser or request.user == user_):
        return HttpResponseUnauthorized(request)

    auth_keys = user_.auth_keys.all()

    if request.user.is_superuser or (not user_.has_usable_password()):
        FormClass = SetPasswordForm
    else:
        FormClass = ChangePasswordForm

    if request.POST:
        form = FormClass(request.POST, user=user_)
        if form.is_valid():
            is_new_pass = not user_.has_usable_password()
            user_.set_password(form.cleaned_data['password1'])
            user_.save()
            security_log.event('password_set' if is_new_pass else 'password_changed', request, account=user_)
            if request.user == user_:
                # Other sessions of this account are signed out; this one stays.
                update_session_auth_hash(request, user_)

            if is_new_pass:
                messages.info(request, _("New password set"))
                if not request.user.is_superuser:
                    form = ChangePasswordForm(user=user_)
            else:
                messages.info(request, _("Your password was changed"))

            return HttpResponseRedirect(reverse('user_authsettings', kwargs={'id': user_.id}))
    else:
        form = FormClass(user=user_)

    auth_keys_list = []

    for k in auth_keys:
        provider = AUTH_PROVIDERS.get(k.provider, None)

        if provider is not None:
            name =  "%s: %s" % (provider.context.human_name, provider.context.readable_key(k))
        else:
            from forum.authentication.base import ConsumerTemplateContext
            "unknown: %s" % ConsumerTemplateContext.readable_key(k)

        auth_keys_list.append({
        'name': name,
        'id': k.id
        })

    return render(request, 'auth/auth_settings.html', {
    'view_user': user_,
    "can_view_private": (user_ == request.user) or request.user.is_superuser,
    'form': form,
    'has_password': user_.has_usable_password(),
    'auth_keys': auth_keys_list,
    'allow_local_auth': AUTH_PROVIDERS.get('local', None),
    })

@require_POST
def remove_external_provider(request, id):
    association = get_object_or_404(AuthKeyUserAssociation, id=id)
    if not (request.user.is_superuser or request.user == association.user):
        return HttpResponseUnauthorized(request)

    messages.info(request, _("You removed the association with %s") % association.provider)
    association.delete()
    return HttpResponseRedirect(reverse('user_authsettings', kwargs={'id': association.user.id}))

def login_and_forward(request, user, forward=None, message=None):
    if user.is_suspended():
        return forward_suspended_user(request, user)

    user.backend = "django.contrib.auth.backends.ModelBackend"
    login(request, user)

    # Store the login action
    UserLoginAction(user=user, ip=request.META['REMOTE_ADDR']).save()

    if message is None:
        message = _("Welcome back %s, you are now logged in") % smart_str(user.username)

    messages.info(request, message)

    if not forward:
        forward = request.session.get(ON_SIGNIN_SESSION_ATTR, reverse('index'))

    pending_data = request.session.get(PENDING_SUBMISSION_SESSION_ATTR, None)

    if pending_data and (user.email_isvalid or pending_data['type'] not in settings.REQUIRE_EMAIL_VALIDATION_TO):
        submission_time = datetime.datetime.fromisoformat(pending_data['time'])
        if submission_time < datetime.datetime.now() - datetime.timedelta(minutes=int(settings.HOLD_PENDING_POSTS_MINUTES)):
            del request.session[PENDING_SUBMISSION_SESSION_ATTR]
        elif submission_time < datetime.datetime.now() - datetime.timedelta(minutes=int(settings.WARN_PENDING_POSTS_MINUTES)):
            messages.info(request, (_("You have a %s pending submission.") % pending_data['data_name']) + " %s, %s, %s" % (
                html.hyperlink(reverse('manage_pending_data', kwargs={'action': _('save')}), _("save it")),
                html.hyperlink(reverse('manage_pending_data', kwargs={'action': _('review')}), _("review")),
                html.hyperlink(reverse('manage_pending_data', kwargs={'action': _('cancel')}), _("cancel"))
            ))
        else:
            return manage_pending_data(request, _('save'), forward)

    additional_get_params = urllib.parse.urlencode(dict([k, v.encode('utf-8')] for k, v in request.GET.items()))

    parsed_forward = urlparse(forward)

    # If there is already some parsed query in the URL then change the forward URL
    if parsed_forward.query:
        forward_url = forward + "&%s" % additional_get_params
    else:
        forward_url = forward + "?%s" % additional_get_params

    return HttpResponseRedirect(forward_url)

def forward_suspended_user(request, user, show_private_msg=True):
    message = _("Sorry, but this account is suspended")
    if show_private_msg:
        msg_type = 'privatemsg'
    else:
        msg_type = 'publicmsg'

    suspension = user.suspension
    if suspension:
        message += (":<br />" + suspension.extra.get(msg_type, ''))

    messages.info(request, message)
    return HttpResponseRedirect(reverse('index'))

@decorate.withfn(login_required)
def signout(request):
    logout(request)
    return HttpResponseRedirect(reverse('index'))
