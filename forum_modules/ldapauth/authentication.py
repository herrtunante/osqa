import ldap
import ldap.filter

from forum.authentication.base import AuthenticationConsumer, ConsumerTemplateContext, InvalidAuthentication
from forum.models import User
from forum.actions import UserJoinsAction
from django.utils.translation import gettext as _

from . import settings


def _ldap_connect():
    ldapo = ldap.initialize(str(settings.LDAP_SERVER))
    ldapo.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
    if settings.LDAP_USE_TLS:
        ldapo.start_tls_s()
    return ldapo

def _text(value):
    # python-ldap returns attribute values as bytes.
    return value.decode('utf-8') if isinstance(value, bytes) else value

class LDAPAuthConsumer(AuthenticationConsumer):

    def process_authentication_request(self, request):
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        #an empty password will cause ldap to try an anonymous bind. This is picked up here
        if not username or not password:
            raise InvalidAuthentication(_('Login failed. Please enter valid username and password (both are case-sensitive)'))

        # Escape the user name so it can't change the search filter.
        uid = str(settings.LDAP_USER_MASK) % ldap.filter.escape_filter_chars(username)

        try:
            ldapo = _ldap_connect()
            ldapo.simple_bind_s(str(settings.LDAP_BIND_DN), str(settings.LDAP_BIND_SECRET))
            search = ldapo.search_s(str(settings.LDAP_BASE_DN), ldap.SCOPE_SUBTREE, uid)
            ldapo.unbind_s()
        except ldap.LDAPError:
            #could not bind using credentials specified in ldap config
            raise InvalidAuthentication(_('Login failed - LDAP bind error. Please contact your system administrator'))

        # Skip search referrals, which have no DN.
        search = [(dn, attrs) for (dn, attrs) in search if dn]

        if len(search) != 1:
            #could not find user (or the filter is ambiguous)
            raise InvalidAuthentication(_('Login failed. Please enter valid username and password (both are case-sensitive)'))

        entry_dn, userinfo = search[0]
        user_dn = _text(userinfo.get(str(settings.LDAP_DN), [entry_dn])[0])

        #now try to bind as selected user; should raise exception if bind fails
        try:
            ldapo = _ldap_connect()
            ldapo.simple_bind_s(user_dn, password)
            ldapo.unbind_s()
        except ldap.LDAPError:
            #could not bind as user - password is incorrect
            raise InvalidAuthentication(_('Login failed. Please enter valid username and password (both are case-sensitive)'))

        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            _user = User( username = _text(userinfo[str(settings.LDAP_UID)][0]),
                          email = _text(userinfo[str(settings.LDAP_MAIL)][0]),
                          real_name = _text(userinfo[str(settings.LDAP_NAME)][0]) )
            _user.email_isvalid = True
            _user.set_unusable_password()
            _user.save()
            UserJoinsAction(user=_user, ip=request.META['REMOTE_ADDR']).save()
            return _user

class LDAPAuthContext(ConsumerTemplateContext):
    mode = 'STACK_ITEM'
    weight = 1000
    human_name = 'LDAP authentication'
    stack_item_template = 'modules/ldapauth/loginform.html'
    show_to_logged_in_user = False
