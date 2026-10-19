"""The pages around the JSON API (forum/api/): the documentation at /api/,
rendered from the endpoint registry like the OpenAPI document, and the
personal access tokens page of an account (/account/<id>/api-tokens/)."""
import json

from django import forms
from django.contrib import messages
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.html import escape
from django.utils.translation import gettext as _, gettext_lazy as _lazy

from forum import settings
from forum.api import openapi
from forum.api.core import BASE_PATH, ENDPOINTS, PROBLEMS
from forum.api.schemas import COMPONENTS
from forum.http_responses import HttpResponseUnauthorized
from forum.models import ApiToken, User
from forum.models.api import MAX_TOKENS_PER_USER
from forum.utils import accounts, security_log, sudo

EXPIRY_DAYS = {'30': 30, '90': 90, '365': 365, 'never': None}


# ---- the documentation ---------------------------------------------------------

def _type_text(schema):
    """A short description of a schema's type, with links to the objects."""
    if '$ref' in schema:
        name = schema['$ref'].rsplit('/', 1)[-1]
        return {'text': name, 'anchor': 'schema-%s' % name}
    if 'anyOf' in schema:
        kinds = [s for s in schema['anyOf'] if s.get('type') != 'null']
        text = ' or '.join(_type_text(s)['text'] for s in kinds)
        found = _type_text(kinds[0]) if len(kinds) == 1 else {'text': text}
        if len(kinds) < len(schema['anyOf']):
            return dict(found, text=found['text'] + ' or null')
        return found
    if 'enum' in schema:
        return {'text': ' | '.join(str(v) for v in schema['enum'])}
    if schema.get('type') == 'array':
        inner = _type_text(schema.get('items', {}))
        return dict(inner, text='list of %s' % inner['text'])
    if schema.get('type') == 'object' and 'properties' in schema and len(schema['properties']) <= 4:
        return {'text': 'object {%s}' % ', '.join(schema['properties'])}
    return {'text': schema.get('format') or schema.get('type', 'any')}


def _objects():
    shown = []
    for name, schema in COMPONENTS.items():
        parts = schema.get('allOf', [schema])
        fields, based_on = [], None
        for part in parts:
            if '$ref' in part:
                based_on = part['$ref'].rsplit('/', 1)[-1]
                continue
            required = set(part.get('required', ()))
            for field, field_schema in part.get('properties', {}).items():
                fields.append({'name': field, 'type': _type_text(field_schema), 'optional': field not in required,
                               'description': field_schema.get('description', '')})
        shown.append({'name': name, 'description': schema.get('description', ''), 'fields': fields,
                      'based_on': based_on})
    return shown


def _curl(ep, path):
    url = '%s%s' % (settings.APP_BASE_URL, path)
    example = {p.name: p.example for p in ep.query_params if p.example is not None}
    if example and ep.method == 'GET':
        url += '?' + '&'.join('%s=%s' % (k, str(v).replace(' ', '+')) for k, v in example.items())
    lines = ['curl%s "%s"' % ('' if ep.method == 'GET' else ' -X %s' % ep.method, url)]
    if ep.auth in ('read', 'write'):
        lines.append('  -H "Authorization: Bearer $OSQA_TOKEN"')
    if ep.body:
        lines.append('  -H "Content-Type: application/json"')
        lines.append("  -d '%s'" % json.dumps(ep.body_example or {}, ensure_ascii=False))
    return ' \\\n'.join(lines)


def _operations():
    groups = []
    for name, text in openapi.GROUPS:
        items = []
        for ep in ENDPOINTS:
            if ep.group != name:
                continue
            path = openapi.openapi_path(ep.path)
            example_path = path
            for p in ep.path_params:
                example_path = example_path.replace('{%s}' % p.name, str(p.example if p.example is not None else 1))
            response = ep.response if isinstance(ep.response, str) else None
            items.append({
                'ep': ep, 'path': path, 'anchor': 'op-%s' % ep.operation_id, 'auth': openapi.AUTH_TEXT[ep.auth],
                'params': [{'p': p, 'type': _type_text(p.schema)} for p in ep.params],
                'body': {'text': ep.body, 'anchor': 'schema-%s' % ep.body} if ep.body else None,
                'body_example': json.dumps(ep.body_example, indent=2, ensure_ascii=False) if ep.body_example else '',
                'response': {'text': response, 'anchor': 'schema-%s' % response} if response else None,
                'errors': [(slug, PROBLEMS[slug][0]) for slug in ep.errors],
                'curl': _curl(ep, example_path),
            })
        if items:
            groups.append({'name': name, 'text': text, 'items': items})
    return groups


def docs(request):
    if not bool(settings.API_ENABLED):
        raise Http404()
    user = request.user
    return render(request, 'api/docs.html', {
        'site_url': settings.APP_BASE_URL,
        'base_url': '%s%s' % (settings.APP_BASE_URL, BASE_PATH),
        'openapi_url': '%s%s/openapi.json' % (settings.APP_BASE_URL, BASE_PATH),
        'groups': _operations(),
        'objects': _objects(),
        'problems': [{'slug': slug, 'status': status, 'title': title} for slug, (status, title) in PROBLEMS.items()],
        'tokens_url': reverse('user_api_tokens', kwargs={'id': user.id}) if user.is_authenticated else '',
        'writes_enabled': bool(settings.API_WRITES_ENABLED),
        'limit_anonymous': int(settings.API_RATE_LIMIT_ANONYMOUS),
        'limit_token': int(settings.API_RATE_LIMIT_TOKEN),
        'limit_writes': int(settings.API_WRITE_RATE_LIMIT),
        'cors_origins': str(settings.API_CORS_ORIGINS or '').split(),
        'example_question': '%s%s/questions/1' % (settings.APP_BASE_URL, BASE_PATH),
    })


# ---- personal access tokens ---------------------------------------------------------

class ApiTokenForm(forms.Form):
    name = forms.CharField(label=_lazy("Name"), max_length=64,
                           help_text=_lazy("What the token is for, e.g. “Weekly report script”."))
    access = forms.ChoiceField(label=_lazy("Access"), widget=forms.RadioSelect, initial='read', choices=(
        ('read', _lazy("Read: questions, answers, tags and members, as you see them")),
        ('write', _lazy("Read and write: also ask, answer, comment, vote, accept and follow as you")),
    ))
    expires = forms.ChoiceField(label=_lazy("Expires"), initial='90', choices=(
        ('30', _lazy("in 30 days")), ('90', _lazy("in 90 days")), ('365', _lazy("in a year")),
        ('never', _lazy("never")),
    ))

    def __init__(self, *args, writes=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not writes:
            self.fields['access'].choices = self.fields['access'].choices[:1]


def _token_owner(request, id):
    """(account, error response): the owner sees and manages their tokens, an
    administrator sees and revokes them."""
    if not bool(settings.API_ENABLED):
        raise Http404()
    account = get_object_or_404(User, id=id)
    if not request.user.is_authenticated or not (request.user == account or request.user.is_superuser):
        return None, HttpResponseUnauthorized(request)
    return account, None


def tokens_page(request, id):
    account, refused = _token_owner(request, id)
    if refused:
        return refused
    own = request.user == account
    writes = bool(settings.API_WRITES_ENABLED)
    form = ApiTokenForm(writes=writes)
    new_token = None
    if request.method == 'POST':
        if not own:
            return HttpResponseUnauthorized(request)
        refused = sudo.require_recent_auth(request, reverse('user_api_tokens', kwargs={'id': account.id}))
        if refused:
            return refused
        form = ApiTokenForm(request.POST, writes=writes)
        if account.api_tokens.count() >= MAX_TOKENS_PER_USER:
            form.add_error(None, _("You have %d tokens, the most an account can have. Revoke one you no longer "
                                   "use first.") % MAX_TOKENS_PER_USER)
        if form.is_valid():
            scopes = ('read', 'write') if form.cleaned_data['access'] == 'write' else ('read',)
            token, new_token = ApiToken.objects.create_token(account, form.cleaned_data['name'].strip(), scopes,
                                                             EXPIRY_DAYS[form.cleaned_data['expires']])
            security_log.event('api_token_created', request, token_id=token.id, scopes=token.scopes)
            accounts.security_notice(request, account, 'api_token', name=token.name)
            form = ApiTokenForm(writes=writes)
    response = render(request, 'users/api_tokens.html', {
        'view_user': account,
        'own': own,
        'form': form,
        'tokens': list(account.api_tokens.all()),
        'new_token': new_token,
        'max_tokens': MAX_TOKENS_PER_USER,
        'writes_enabled': writes,
    })
    # The new token is in this page only: never stored by the browser or a proxy.
    response['Cache-Control'] = 'no-store'
    return response


def revoke_token(request, id, token_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    account, refused = _token_owner(request, id)
    if refused:
        return refused
    token = get_object_or_404(ApiToken, id=token_id, user=account)
    security_log.event('api_token_revoked', request, token_id=token.id, account=account)
    token.delete()
    # Messages are shown unescaped (base_content.html).
    messages.info(request, _("The token “%s” was revoked: programs using it can no longer reach the API.")
                  % escape(token.name))
    return HttpResponseRedirect(reverse('user_api_tokens', kwargs={'id': account.id}))
