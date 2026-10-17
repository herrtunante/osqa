"""The JSON API (/api/v1/, forum/api/), its personal access tokens
(forum/models/api.py, the API tokens page) and its documentation (/api/,
/api/v1/openapi.json).

Reads must show exactly what the site shows (held and deleted posts), writes
must go through the same Action classes and rules as the web site (the same
reputation changes), and the OpenAPI document must describe exactly the
routes that exist, with response bodies that match its schemas."""
import datetime
import html
import json
import logging
import re
from unittest import mock
from urllib.parse import urlsplit

from django import forms
from django.core.cache import cache
from django.test import override_settings
from django.urls import resolve, reverse

from forum import settings
from forum.actions import AcceptAnswerAction, CloseAction, DeleteAction, SuspendAction
from forum.api import core
from forum.api.openapi import document
from forum.api.schemas import COMPONENTS
from forum.models import (Action, ActionRepute, Answer, ApiToken, Comment, Node, Question, QuestionSubscription, Tag,
                          TagSynonym, User)
from forum.models.api import token_digest

from .base import OSQATestCase
from .test_query_budgets import QueryBudgetMixin

API = '/api/v1'
BODY = 'I need the survey data in R. Which menu exports it as CSV?'


def fresh(node):
    return Node.objects.get(id=node.id)


# ---- a small JSON Schema checker (the parts of 2020-12 the schemas use) ----------------

def check_schema(instance, schema, where='$'):
    if '$ref' in schema:
        return check_schema(instance, COMPONENTS[schema['$ref'].rsplit('/', 1)[-1]], where)
    for part in schema.get('allOf', ()):
        check_schema(instance, part, where)
    if 'anyOf' in schema:
        errors = []
        for option in schema['anyOf']:
            try:
                check_schema(instance, option, where)
                break
            except AssertionError as e:
                errors.append(str(e))
        else:
            raise AssertionError('%s matches none of the options: %s' % (where, '; '.join(errors)))
    if 'enum' in schema:
        assert instance in schema['enum'], '%s: %r not in %r' % (where, instance, schema['enum'])
    kind = schema.get('type')
    types = {'string': str, 'integer': int, 'boolean': bool, 'object': dict, 'array': list, 'null': type(None)}
    if kind:
        ok = isinstance(instance, types[kind]) and not (kind == 'integer' and isinstance(instance, bool))
        assert ok, '%s: %r is not %s' % (where, instance, kind)
    if kind == 'string' and schema.get('format') in ('uri',):
        assert re.match(r'^https?://', instance), '%s: %r is not an absolute URL' % (where, instance)
    if kind == 'string' and schema.get('format') == 'date-time':
        assert re.match(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$', instance), '%s: %r' % (where, instance)
    if kind == 'object':
        for name in schema.get('required', ()):
            assert name in instance, '%s: %s is missing' % (where, name)
        for name, sub in schema.get('properties', {}).items():
            if name in instance:
                check_schema(instance[name], sub, '%s.%s' % (where, name))
        if isinstance(schema.get('additionalProperties'), dict):
            for name, value in instance.items():
                check_schema(value, schema['additionalProperties'], '%s.%s' % (where, name))
    if kind == 'array':
        for i, item in enumerate(instance):
            check_schema(item, schema.get('items', {}), '%s[%d]' % (where, i))


class ApiTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.asker = self.create_user('asker', email_isvalid=True, reputation=100)
        self.member = self.create_user('member', email_isvalid=True, reputation=500)
        self.newbie = self.create_user('newbie', email_isvalid=True, reputation=1)
        self.mod = self.create_user('mod', email_isvalid=True, reputation=10, is_staff=True)
        self.question = self.ask_web(self.asker, 'How do I export survey data to CSV files?', 'collect export')
        self.answer = self.answer_web(self.member, self.question, 'Use **File > Export > CSV** in the designer.')
        self.comment = self.comment_web(self.member, self.question, 'Which version of Collect do you use?')
        self.client.logout()

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    # The web site, for comparison.
    def ask_web(self, user, title, tags, text=BODY):
        self.login(user)
        response = self.client.post(reverse('ask'), {'title': title, 'text': text, 'tags': tags})
        self.assertEqual(response.status_code, 302, getattr(response, 'content', b'')[:300])
        return Question.objects.filter(title=title).latest('id')

    def answer_web(self, user, question, text):
        self.login(user)
        self.client.post(reverse('answer', args=[question.id]), {'text': text})
        return Answer.objects.filter(parent=question).latest('id')

    def comment_web(self, user, post, text):
        self.login(user)
        result = self.command(reverse('comment', args=[post.id]), {'comment': text})
        self.assertTrue(result['success'], result)
        return Comment.objects.filter(parent=post).latest('id')

    # The API.
    def token(self, user, scopes=('read', 'write'), days=None):
        return ApiToken.objects.create_token(user, 'test', scopes, days)[1]

    def headers(self, token=None, **extra):
        if token:
            extra['HTTP_AUTHORIZATION'] = 'Bearer %s' % token
        return extra

    def get(self, path, token=None, **extra):
        return self.client.get(API + path, **self.headers(token, **extra))

    def send(self, method, path, data=None, token=None, **extra):
        body = json.dumps(data if data is not None else {})
        return getattr(self.client, method)(API + path, body, content_type='application/json',
                                            **self.headers(token, **extra))

    def post(self, path, data=None, token=None, **extra):
        return self.send('post', path, data, token, **extra)

    def assertProblem(self, response, status, slug):
        self.assertEqual(response.status_code, status, response.content[:500])
        self.assertEqual(response['Content-Type'], 'application/problem+json; charset=utf-8')
        data = json.loads(response.content)
        self.assertEqual(data['type'], 'http://testserver/api/#problem-%s' % slug)
        self.assertEqual(data['status'], status)
        self.assertTrue(data['title'])
        check_schema(data, {'$ref': '#/components/schemas/Problem'})
        return data

    def ok(self, response, status=200, schema=None):
        self.assertEqual(response.status_code, status, response.content[:800])
        data = json.loads(response.content)
        if schema:
            check_schema(data, {'$ref': '#/components/schemas/%s' % schema})
        return data

    def ids(self, response):
        return [item['id'] for item in self.ok(response)['data']]


# ---- tokens --------------------------------------------------------------------

class TokenModelTest(ApiTestCase):
    def test_only_a_digest_is_stored(self):
        token, raw = ApiToken.objects.create_token(self.member, 'Script', ('read', 'write'), 30)
        self.assertTrue(raw.startswith('osqa_'))
        self.assertGreaterEqual(len(raw), 40)
        row = ApiToken.objects.filter(id=token.id).values()[0]
        self.assertNotIn(raw, [str(v) for v in row.values()])
        self.assertEqual(row['token_hash'], token_digest(raw))
        self.assertEqual(row['prefix'], raw[:11])
        self.assertEqual(ApiToken.objects.find(raw), token)
        self.assertIsNone(ApiToken.objects.find(raw + 'x'))
        self.assertIsNone(ApiToken.objects.find('Bearer'))
        self.assertEqual(token.scope_list, ['read', 'write'])
        self.assertFalse(token.is_expired)
        self.assertAlmostEqual((token.expires - datetime.datetime.now()).days, 29, delta=1)

    def test_read_is_the_default_scope_and_two_tokens_differ(self):
        a, raw_a = ApiToken.objects.create_token(self.member, 'A', ())
        b, raw_b = ApiToken.objects.create_token(self.member, 'B', ('write', 'admin'))
        self.assertEqual(a.scopes, 'read')
        self.assertEqual(b.scopes, 'write')
        self.assertNotEqual(raw_a, raw_b)
        self.assertIsNone(a.expires)

    def test_deleted_accounts_lose_their_tokens(self):
        from forum.views.account import anonymise_user
        self.token(self.member)
        anonymise_user(self.member)
        self.assertFalse(ApiToken.objects.filter(user=self.member).exists())


class TokenPageTest(ApiTestCase):
    def url(self, user=None):
        return reverse('user_api_tokens', kwargs={'id': (user or self.member).id})

    def test_only_the_owner_and_administrators(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)
        self.login(self.asker)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.login(self.member)
        response = self.client.get(self.url())
        self.assertCleanPage(response)
        self.assertContains(response, 'Create a token')
        self.assertContains(response, reverse('api_docs'))

    def test_create_shows_the_token_once(self):
        self.login(self.member)
        with self.assertLogs('forum.security', logging.INFO) as logs:
            response = self.client.post(self.url(), {'name': 'Weekly report', 'access': 'write', 'expires': '30'})
        self.assertCleanPage(response)
        self.assertEqual(response['Cache-Control'], 'no-store')
        token = ApiToken.objects.get(user=self.member)
        self.assertEqual((token.name, token.scopes), ('Weekly report', 'read write'))
        raw = re.search(r'value="(osqa_[\w-]+)"', response.content.decode()).group(1)
        self.assertEqual(token.token_hash, token_digest(raw))
        self.assertIn('event=api_token_created', logs.output[0])
        self.assertNotIn(raw, ''.join(logs.output))
        # Never again.
        response = self.client.get(self.url())
        self.assertNotContains(response, raw)
        self.assertContains(response, token.prefix)
        self.assertContains(response, 'Weekly report')
        self.assertContains(response, 'never used')
        self.ok(self.get('/me', raw), schema='Me')
        self.assertContains(self.client.get(self.url()), 'last used')

    def test_invalid_form_and_the_limit(self):
        self.login(self.member)
        response = self.client.post(self.url(), {'name': '', 'access': 'read', 'expires': '30'})
        self.assertContains(response, 'aria-invalid="true"')
        self.assertFalse(ApiToken.objects.exists())
        self.client.post(self.url(), {'name': 'x', 'access': 'admin', 'expires': '30'})
        self.client.post(self.url(), {'name': 'x', 'access': 'read', 'expires': '7'})
        self.assertFalse(ApiToken.objects.exists())
        with mock.patch('forum.views.api.MAX_TOKENS_PER_USER', 1):
            self.client.post(self.url(), {'name': 'one', 'access': 'read', 'expires': 'never'})
            response = self.client.post(self.url(), {'name': 'two', 'access': 'read', 'expires': 'never'})
        self.assertEqual(ApiToken.objects.count(), 1)
        self.assertIsNone(ApiToken.objects.get().expires)
        self.assertContains(response, 'the most an account can have')

    def test_without_writes_only_read_tokens(self):
        self.set(settings.API_WRITES_ENABLED, False)
        self.login(self.member)
        self.assertNotContains(self.client.get(self.url()), 'value="write"')
        self.client.post(self.url(), {'name': 'w', 'access': 'write', 'expires': '30'})
        self.assertFalse(ApiToken.objects.exists())

    def test_revoke(self):
        raw = self.token(self.member)
        token = ApiToken.objects.get()
        self.login(self.member)
        revoke = reverse('user_api_token_revoke', kwargs={'id': self.member.id, 'token_id': token.id})
        self.assertEqual(self.client.get(revoke).status_code, 405)
        self.login(self.asker)
        self.assertEqual(self.client.post(revoke).status_code, 403)
        self.login(self.member)
        with self.assertLogs('forum.security', logging.INFO) as logs:
            response = self.client.post(revoke)
        self.assertRedirects(response, self.url(), fetch_redirect_response=False)
        self.assertIn('event=api_token_revoked', logs.output[0])
        self.assertFalse(ApiToken.objects.exists())
        self.assertProblem(self.get('/me', raw), 401, 'invalid-token')

    def test_administrators_see_and_revoke_but_dont_create(self):
        admin = self.create_user('admin', is_superuser=True, is_staff=True)
        self.token(self.member)
        self.login(admin)
        response = self.client.get(self.url())
        self.assertContains(response, 'Revoke')
        self.assertNotContains(response, 'Create a token')
        self.assertEqual(self.client.post(self.url(), {'name': 'x', 'access': 'read', 'expires': '30'}).status_code,
                         403)
        token = ApiToken.objects.get()
        self.client.post(reverse('user_api_token_revoke', kwargs={'id': self.member.id, 'token_id': token.id}))
        self.assertFalse(ApiToken.objects.exists())

    def test_linked_from_the_settings_and_hidden_when_the_api_is_off(self):
        self.login(self.member)
        response = self.client.get(reverse('user_security', kwargs={'id': self.member.id}))
        self.assertContains(response, self.url())
        self.set(settings.API_ENABLED, False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse('user_security', kwargs={'id': self.member.id})), self.url())


# ---- authentication -------------------------------------------------------------

class AuthenticationTest(ApiTestCase):
    def test_reading_needs_no_token(self):
        self.ok(self.get('/questions/%d' % self.question.id), schema='Question')

    def test_bad_tokens(self):
        for header in ('Bearer osqa_nope', 'Bearer ', 'Basic dXNlcjpwYXNz', 'Bearer not-a-token'):
            response = self.client.get(API + '/questions', HTTP_AUTHORIZATION=header)
            self.assertProblem(response, 401, 'invalid-token')
            self.assertIn('Bearer realm="api"', response['WWW-Authenticate'])

    def test_expired_token(self):
        raw = self.token(self.member, days=1)
        ApiToken.objects.update(expires=datetime.datetime.now() - datetime.timedelta(minutes=1))
        data = self.assertProblem(self.get('/me', raw), 401, 'invalid-token')
        self.assertIn('expired', data['detail'])

    def test_suspended_and_inactive_accounts(self):
        raw = self.token(self.member)
        SuspendAction(user=self.mod, extra={'bantype': 'indefinitely'}).save(data=dict(suspended=self.member))
        self.assertProblem(self.get('/me', raw), 403, 'account-suspended')
        self.assertProblem(self.post('/questions/%d/follow' % self.question.id, token=raw), 403, 'account-suspended')

    def test_scopes(self):
        read = self.token(self.member, ('read',))
        data = self.ok(self.get('/me', read), schema='Me')
        self.assertEqual(data['token']['scopes'], ['read'])
        self.assertEqual(data['user']['username'], 'member')
        response = self.post('/questions/%d/follow' % self.question.id, token=read)
        self.assertProblem(response, 403, 'insufficient-scope')
        self.assertIn('insufficient_scope', response['WWW-Authenticate'])
        self.assertFalse(QuestionSubscription.objects.filter(user=self.member, question=self.question,
                                                             auto_subscription=False).exists())

    def test_me_needs_a_token(self):
        response = self.get('/me')
        self.assertProblem(response, 401, 'unauthorized')
        self.assertEqual(response['WWW-Authenticate'], 'Bearer realm="api"')

    def test_the_session_cookie_is_never_used(self):
        # Signed in on the site: the API still sees an anonymous visitor, and refuses to write.
        self.login(self.member)
        self.assertProblem(self.get('/me'), 401, 'unauthorized')
        response = self.post('/questions/%d/follow' % self.question.id)
        self.assertProblem(response, 401, 'unauthorized')
        response = self.client.post(API + '/questions/%d/follow' % self.question.id)  # a form post, no CSRF token
        self.assertProblem(response, 401, 'unauthorized')
        self.assertFalse(QuestionSubscription.objects.filter(user=self.member, question=self.question,
                                                             auto_subscription=False).exists())

    def test_last_used_is_recorded(self):
        raw = self.token(self.member)
        self.get('/questions', raw, REMOTE_ADDR='10.1.2.3')
        token = ApiToken.objects.get()
        self.assertIsNotNone(token.last_used)
        self.assertEqual(token.last_used_ip, '10.1.2.3')

    def test_signin_confirmation_mode(self):
        raw = self.token(self.newbie)
        User.objects.filter(id=self.newbie.id).update(email_isvalid=False, email_confirmation_required=True)
        cache.clear()
        self.set(settings.EMAIL_CONFIRMATION_MODE, 'signin')
        self.assertProblem(self.get('/me', raw), 403, 'email-confirmation-required')


# ---- reading --------------------------------------------------------------------

class ReadTest(ApiTestCase):
    def test_question_detail(self):
        accepted = self.answer_web(self.mod, self.question, 'The command line exporter works too, see the docs.')
        AcceptAnswerAction(user=self.asker, node=accepted, ip='127.0.0.1').save()
        response = self.get('/questions/%d' % self.question.id)
        data = self.ok(response, schema='Question')
        self.assertEqual(data['title'], self.question.title)
        self.assertEqual(data['tags'], ['collect', 'export'])
        self.assertEqual(data['status'], 'accepted')
        self.assertEqual(data['markdown'], BODY)
        self.assertIn('<p>', data['html'])
        self.assertEqual(data['url'], 'http://testserver' + self.question.get_absolute_url())
        self.assertEqual(data['api_url'], 'http://testserver/api/v1/questions/%d' % self.question.id)
        self.assertEqual([a['id'] for a in data['answers']], [accepted.id, self.answer.id])
        self.assertTrue(data['answers'][0]['accepted'])
        self.assertEqual(data['answers'][1]['markdown'], 'Use **File > Export > CSV** in the designer.')
        self.assertIn('<strong>File &gt; Export &gt; CSV</strong>', data['answers'][1]['html'])
        self.assertEqual(data['comments'][0]['markdown'], 'Which version of Collect do you use?')
        self.assertEqual(data['author']['username'], 'asker')
        self.assertNotIn('my_vote', data)
        self.assertEqual(response['Content-Type'], 'application/json; charset=utf-8')
        self.assertEqual(response['Cache-Control'], 'public, no-cache')

    def test_answers(self):
        self.comment_web(self.asker, self.answer, 'Thanks, that worked for me.')
        data = self.ok(self.get('/questions/%d/answers' % self.question.id), schema='AnswerList')
        self.assertIsNone(data['next'])
        self.assertEqual(data['data'][0]['comments'][0]['markdown'], 'Thanks, that worked for me.')
        answer = self.ok(self.get('/answers/%d' % self.answer.id), schema='Answer')
        self.assertEqual(answer['question_id'], self.question.id)
        self.assertEqual(len(answer['comments']), 1)
        self.assertProblem(self.get('/answers/%d' % self.question.id), 404, 'not-found')
        self.assertProblem(self.get('/questions/%d' % self.answer.id), 404, 'not-found')

    def test_my_vote_and_following_with_a_token(self):
        raw = self.token(self.asker)
        self.login(self.asker)
        self.command(reverse('vote_post', args=[self.answer.id, 'up']))
        self.client.logout()
        data = self.ok(self.get('/questions/%d' % self.question.id, raw), schema='Question')
        self.assertIsNone(data['my_vote'])
        self.assertTrue(data['following'])  # askers follow their questions
        self.assertEqual(data['answers'][0]['my_vote'], 'up')

    def test_held_posts(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'before')
        held = self.ask_web(self.newbie, 'Where can I download the zanzibar toolkit?', 'collect')
        held_answer = self.answer_web(self.newbie, self.question, 'Use the quokka exporter, it works well enough.')
        self.client.logout()
        self.assertTrue(fresh(held).nis.pending_review)
        others = (None, self.token(self.member))
        mine = (self.token(self.newbie), self.token(self.mod))
        for token in others:
            self.assertNotIn(held.id, self.ids(self.get('/questions', token)))
            self.assertProblem(self.get('/questions/%d' % held.id, token), 404, 'not-found')
            self.assertProblem(self.get('/answers/%d' % held_answer.id, token), 404, 'not-found')
            answers = self.ok(self.get('/questions/%d' % self.question.id, token))['answers']
            self.assertNotIn(held_answer.id, [a['id'] for a in answers])
        for token in mine:
            self.assertIn(held.id, self.ids(self.get('/questions', token)))
            data = self.ok(self.get('/questions/%d' % held.id, token), schema='Question')
            self.assertTrue(data['held_for_review'])
            answers = self.ok(self.get('/questions/%d' % self.question.id, token))['answers']
            self.assertIn(held_answer.id, [a['id'] for a in answers])
        self.assertNotIn(held.id, self.ids(self.get('/search?q=zanzibar', mine[0])))

    def test_deleted_posts(self):
        DeleteAction(user=self.mod, node=fresh(self.answer), ip='127.0.0.1').save()
        DeleteAction(user=self.mod, node=fresh(self.comment), ip='127.0.0.1').save()
        data = self.ok(self.get('/questions/%d' % self.question.id))
        self.assertEqual((data['answers'], data['comments']), ([], []))
        self.assertProblem(self.get('/answers/%d' % self.answer.id), 404, 'not-found')
        DeleteAction(user=self.mod, node=fresh(self.question), ip='127.0.0.1').save()
        self.assertNotIn(self.question.id, self.ids(self.get('/questions')))
        self.assertProblem(self.get('/questions/%d' % self.question.id), 404, 'not-found')
        self.assertProblem(self.get('/questions/%d' % self.question.id, self.token(self.member)), 404, 'not-found')
        # Their authors and moderators see deleted questions on the site, and here.
        self.assertTrue(self.ok(self.get('/questions/%d' % self.question.id, self.token(self.asker)))['deleted'])
        data = self.ok(self.get('/questions/%d' % self.question.id, self.token(self.mod)))
        self.assertTrue(data['deleted'])
        self.assertNotIn(self.question.id, self.ids(self.get('/questions', self.token(self.mod))))

    def test_list_filters_and_sorting(self):
        other = self.ask_web(self.member, 'Can SEPAL process Sentinel-2 time series?', 'sepal')
        Question.objects.filter(id=other.id).update(score=5)
        Question.objects.filter(id=self.question.id).update(
            added_at=datetime.datetime.now() - datetime.timedelta(days=10),
            last_activity_at=datetime.datetime.now() - datetime.timedelta(days=10))
        cache.clear()
        self.assertEqual(self.ids(self.get('/questions?sort=newest')), [other.id, self.question.id])
        self.assertEqual(self.ids(self.get('/questions?sort=votes')), [other.id, self.question.id])
        self.assertEqual(self.ids(self.get('/questions?tags=export')), [self.question.id])
        self.assertEqual(self.ids(self.get('/questions?tags=collect,export')), [self.question.id])
        self.assertEqual(self.ids(self.get('/questions?tags=nosuchtag')), [])
        self.assertEqual(self.ids(self.get('/questions?status=unanswered')), [other.id])
        self.assertEqual(self.ids(self.get('/questions?status=answered')), [self.question.id])
        since = (datetime.datetime.now() - datetime.timedelta(days=2)).date().isoformat()
        self.assertEqual(self.ids(self.get('/questions?since=%s' % since)), [other.id])
        self.assertEqual(self.ids(self.get('/questions?since=%sT00:00:00Z' % since)), [other.id])
        TagSynonym.objects.create(source_name='csv', target=Tag.objects.get(name='export'))
        self.assertEqual(self.ids(self.get('/questions?tags=csv')), [self.question.id])
        for bad in ('sort=best', 'status=open', 'since=yesterday', 'limit=0', 'limit=101', 'limit=x'):
            data = self.assertProblem(self.get('/questions?' + bad), 400, 'bad-request')
            self.assertIn(bad.split('=')[0], data['errors'])

    def test_cursor_pagination(self):
        for i in range(5):
            Question(author=self.member, title='Question number %d about exports' % i, tagnames='collect',
                     body='A question body that is long enough.').save()
        for sort in ('active', 'newest', 'votes'):
            seen, url = [], API + '/questions?limit=2&sort=%s' % sort
            while url:
                response = self.client.get(url)
                data = self.ok(response, schema='QuestionPage')
                self.assertLessEqual(len(data['data']), 2)
                seen += [q['id'] for q in data['data']]
                url = data['next']
                if url:
                    self.assertEqual(response['Link'], '<%s>; rel="next"' % url)
                    self.assertTrue(url.startswith('http://testserver/api/v1/questions?'))
                    url = url[len('http://testserver'):]
                else:
                    self.assertFalse(response.has_header('Link'))
            self.assertEqual(len(seen), 6, sort)
            self.assertEqual(len(set(seen)), 6, sort)
        # A new question doesn't shift the pages already read.
        first = self.ok(self.get('/questions?limit=2&sort=newest'))
        Question(author=self.member, title='A brand new question about exports', tagnames='collect',
                 body='A question body that is long enough.').save()
        second = self.ok(self.client.get(first['next'][len('http://testserver'):]))
        self.assertNotIn(second['data'][0]['id'], [q['id'] for q in first['data']])
        self.assertProblem(self.get('/questions?cursor=garbage'), 400, 'bad-request')
        cursor = urlsplit(first['next']).query.split('cursor=')[1]
        self.assertProblem(self.get('/questions?sort=votes&cursor=%s' % cursor), 400, 'bad-request')

    def test_tags(self):
        TagSynonym.objects.create(source_name='csv', target=Tag.objects.get(name='export'))
        data = self.ok(self.get('/tags'), schema='TagPage')
        export = [t for t in data['data'] if t['name'] == 'export'][0]
        self.assertEqual(export['synonyms'], ['csv'])
        self.assertEqual(export['count'], 1)
        self.assertEqual([t['name'] for t in self.ok(self.get('/tags?sort=name'))['data']], ['collect', 'export'])
        self.assertEqual([t['name'] for t in self.ok(self.get('/tags?q=exp'))['data']], ['export'])
        self.assertEqual(self.ok(self.get('/tags/csv'), schema='Tag')['name'], 'export')
        self.assertEqual(self.ok(self.get('/tags/collect'))['synonyms'], [])
        self.assertProblem(self.get('/tags/nosuchtag'), 404, 'not-found')
        names = [t['name'] for t in self.ok(self.get('/tags?sort=name&limit=1'))['data']]
        self.assertEqual(names, ['collect'])

    def test_users(self):
        self.member.website = 'https://example.org'
        self.member.location = 'Rome'
        self.member.save()
        self.newbie.website = 'https://spam.example'
        self.newbie.save()
        data = self.ok(self.get('/users'), schema='UserPage')
        self.assertEqual(data['data'][0]['username'], 'member')
        self.assertNotIn('email', json.dumps(data))
        self.assertNotIn('@example.com', json.dumps(data))
        member = self.ok(self.get('/users/%d' % self.member.id), schema='User')
        self.assertEqual((member['website'], member['location']), ('https://example.org', 'Rome'))
        self.assertIsNone(self.ok(self.get('/users/%d' % self.newbie.id))['website'])
        self.assertTrue(self.ok(self.get('/users/%d' % self.mod.id))['moderator'])
        self.assertEqual([u['username'] for u in self.ok(self.get('/users?q=ask'))['data']], ['asker'])
        names = [u['username'] for u in self.ok(self.get('/users?sort=name'))['data']]
        self.assertEqual(names, sorted(names))
        self.assertProblem(self.get('/users/99999'), 404, 'not-found')

    def test_badges(self):
        data = self.ok(self.get('/badges'), schema='BadgeList')
        self.assertTrue(data['data'])
        self.assertIsNone(data['next'])
        types = [b['type'] for b in data['data']]
        self.assertEqual(types, sorted(types, key=['gold', 'silver', 'bronze'].index))

    def test_search(self):
        self.ask_web(self.member, 'Can SEPAL process Sentinel-2 time series?', 'sepal')
        self.client.logout()
        data = self.ok(self.get('/search?q=export+csv'), schema='QuestionPage')
        self.assertEqual(data['data'][0]['id'], self.question.id)
        self.assertEqual(self.ids(self.get('/search?q=sentinel&status=answered')), [])
        self.assertEqual(self.ids(self.get('/search?q=sentinel&tags=collect')), [])
        self.assertProblem(self.get('/search'), 400, 'bad-request')
        self.assertProblem(self.get('/search?q=x&limit=500'), 400, 'bad-request')

    def test_etag(self):
        path = '/questions/%d' % self.question.id
        response = self.get(path)
        etag = response['ETag']
        self.assertTrue(re.match(r'^"[0-9a-f]{32}"$', etag))
        not_modified = self.get(path, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(not_modified.status_code, 304)
        self.assertEqual(not_modified.content, b'')
        self.assertEqual(not_modified['ETag'], etag)
        self.login(self.member)
        self.command(reverse('vote_post', args=[self.question.id, 'up']))
        self.client.logout()
        changed = self.get(path, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed['ETag'], etag)
        for detail in ('/answers/%d' % self.answer.id, '/tags/collect', '/users/%d' % self.member.id):
            tag = self.get(detail)['ETag']
            self.assertEqual(self.get(detail, HTTP_IF_NONE_MATCH=tag).status_code, 304)
        # Lists have no ETag (their next links matter more than revalidating).
        self.assertFalse(self.get('/questions').has_header('ETag'))

    def test_head_options_and_unknown_methods(self):
        response = self.client.head(API + '/questions/%d' % self.question.id)
        self.assertEqual((response.status_code, response.content), (200, b''))
        response = self.send('put', '/questions/%d' % self.question.id)
        data = self.assertProblem(response, 405, 'method-not-allowed')
        self.assertEqual(response['Allow'], 'GET, HEAD, OPTIONS')
        self.assertIn('PUT', data['detail'])
        response = self.client.options(API + '/questions')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['Allow'], 'GET, HEAD, OPTIONS, POST')
        self.assertEqual(self.client.get(API + '/nothing-here').status_code, 404)

    def test_index_and_openapi(self):
        data = self.ok(self.get('/'), schema='Index')
        self.assertEqual(data['openapi'], 'http://testserver/api/v1/openapi.json')
        self.assertEqual(data['docs'], 'http://testserver/api/')
        self.assertEqual(self.ok(self.get('/openapi.json'))['openapi'], '3.1.0')

    def test_turned_off(self):
        self.set(settings.API_ENABLED, False)
        self.assertProblem(self.get('/questions'), 503, 'api-disabled')
        self.assertEqual(self.client.get(reverse('api_docs')).status_code, 404)
        self.assertNotContains(self.client.get('/'), 'href="/api/"')

    def test_server_errors_are_problems(self):
        with mock.patch('forum.api.endpoints.D.question_summary', side_effect=RuntimeError('boom')), \
                self.assertLogs('forum.api', logging.ERROR), self.assertLogs('django.request', logging.ERROR):
            self.assertProblem(self.get('/questions'), 500, 'server-error')


class CorsTest(ApiTestCase):
    def test_any_origin_by_default(self):
        response = self.get('/questions', HTTP_ORIGIN='https://example.org')
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        self.assertIn('Link', response['Access-Control-Expose-Headers'])
        self.assertFalse(response.has_header('Access-Control-Allow-Credentials'))
        self.assertIn('Origin', response['Vary'])
        self.assertFalse(self.get('/questions').has_header('Access-Control-Allow-Origin'))

    def test_configured_origins(self):
        self.set(settings.API_CORS_ORIGINS, 'https://a.example https://b.example')
        response = self.get('/questions', HTTP_ORIGIN='https://b.example')
        self.assertEqual(response['Access-Control-Allow-Origin'], 'https://b.example')
        self.assertFalse(self.get('/questions', HTTP_ORIGIN='https://evil.example').has_header(
            'Access-Control-Allow-Origin'))
        self.set(settings.API_CORS_ORIGINS, '')
        self.assertFalse(self.get('/questions', HTTP_ORIGIN='https://a.example').has_header(
            'Access-Control-Allow-Origin'))

    def test_preflight_allows_reading_only(self):
        response = self.client.options(API + '/questions', HTTP_ORIGIN='https://example.org',
                                       HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
                                       HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        self.assertEqual(response['Access-Control-Allow-Methods'], 'GET, HEAD, OPTIONS')
        self.assertIn('Authorization', response['Access-Control-Allow-Headers'])
        self.assertFalse(response.has_header('Access-Control-Allow-Credentials'))
        response = self.client.options(API + '/questions', HTTP_ORIGIN='https://example.org',
                                       HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST')
        self.assertFalse(response.has_header('Access-Control-Allow-Origin'))
        raw = self.token(self.member)
        response = self.post('/questions/%d/follow' % self.question.id, token=raw, HTTP_ORIGIN='https://example.org')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.has_header('Access-Control-Allow-Origin'))
        self.assertEqual(response['Cache-Control'], 'no-store')


class RateLimitTest(ApiTestCase):
    def test_anonymous_per_ip(self):
        self.set(settings.API_RATE_LIMIT_ANONYMOUS, 2)
        first = self.get('/questions')
        self.assertEqual(first['X-RateLimit-Limit'], '2')
        self.assertEqual(first['X-RateLimit-Remaining'], '1')
        self.assertTrue(1 <= int(first['X-RateLimit-Reset']) <= 60)
        self.assertEqual(self.get('/tags')['X-RateLimit-Remaining'], '0')
        with mock.patch('forum.api.core.metrics.count') as count:
            response = self.get('/questions')
        data = self.assertProblem(response, 429, 'rate-limited')
        self.assertIn('2 per minute', data['detail'])
        self.assertTrue(1 <= int(response['Retry-After']) <= 60)
        self.assertEqual(response['X-RateLimit-Remaining'], '0')
        count.assert_called_with('osqa_api_rate_limited_total', kind='ip')
        # Another address has its own count; a token has its own limit.
        self.assertEqual(self.get('/questions', REMOTE_ADDR='10.9.9.9').status_code, 200)
        self.assertEqual(self.get('/questions', self.token(self.member)).status_code, 200)

    def test_per_token_and_writes(self):
        self.set(settings.API_RATE_LIMIT_TOKEN, 3)
        self.set(settings.API_WRITE_RATE_LIMIT, 1)
        raw, other = self.token(self.member), self.token(self.asker)
        self.assertEqual(self.post('/questions/%d/follow' % self.question.id, token=raw).status_code, 200)
        response = self.post('/questions/%d/follow' % self.question.id, token=raw)
        self.assertProblem(response, 429, 'rate-limited')
        self.assertEqual(self.get('/questions', raw).status_code, 200)
        self.assertProblem(self.get('/questions', raw), 429, 'rate-limited')
        self.assertEqual(self.get('/questions', other)['X-RateLimit-Remaining'], '2')

    def test_no_limit(self):
        self.set(settings.API_RATE_LIMIT_ANONYMOUS, 0)
        self.assertFalse(self.get('/questions').has_header('X-RateLimit-Limit'))


# ---- writing --------------------------------------------------------------------

class WriteTest(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.raw = self.token(self.member)

    def test_ask(self):
        response = self.post('/questions', {'title': 'Which projection does Collect Earth use?',
                                            'text': BODY, 'tags': ['collect', 'Projection']}, self.raw)
        data = self.ok(response, 201, schema='Question')
        question = Question.objects.get(id=data['id'])
        self.assertEqual(response['Location'], 'http://testserver/api/v1/questions/%d' % question.id)
        self.assertEqual(question.author, self.member)
        self.assertEqual(question.tagname_list(), ['collect', 'projection'])
        self.assertEqual(data['markdown'], BODY)
        action = Action.objects.get(action_type='ask', node=question)
        self.assertEqual((action.user, action.ip), (self.member, '127.0.0.1'))
        self.assertFalse(data['held_for_review'])
        self.assertTrue(QuestionSubscription.objects.filter(user=self.member, question=question).exists())
        self.assertFalse(response.cookies)  # no messages cookie, no session

    def test_ask_validation(self):
        data = self.assertProblem(self.post('/questions', {'title': 'Short', 'text': BODY, 'tags': 'collect'},
                                            self.raw), 422, 'validation-error')
        self.assertIn('title', data['errors'])
        data = self.assertProblem(self.post('/questions', {'title': 'A good long title here', 'text': BODY},
                                            self.raw), 422, 'validation-error')
        self.assertIn('tags', data['errors'])
        self.assertProblem(self.post('/questions', {'title': 3, 'text': BODY, 'tags': 'x'}, self.raw),
                           422, 'validation-error')
        self.assertProblem(self.post('/questions', {'title': 'A good long title here', 'text': BODY, 'tags': 5},
                                     self.raw), 422, 'validation-error')
        self.assertProblem(self.client.post(API + '/questions', 'title=x', content_type='text/plain',
                                            HTTP_AUTHORIZATION='Bearer ' + self.raw), 415, 'unsupported-media-type')
        self.assertProblem(self.client.post(API + '/questions', '{bad', content_type='application/json',
                                            HTTP_AUTHORIZATION='Bearer ' + self.raw), 400, 'bad-request')
        self.assertProblem(self.client.post(API + '/questions', '[1]', content_type='application/json',
                                            HTTP_AUTHORIZATION='Bearer ' + self.raw), 400, 'bad-request')
        self.assertEqual(Question.objects.count(), 1)

    def test_ask_rules(self):
        new = {'title': 'Which projection does Collect Earth use?', 'text': BODY, 'tags': 'collect'}
        # Email confirmation, as on the web.
        User.objects.filter(id=self.member.id).update(email_isvalid=False)
        cache.clear()
        self.set(settings.REQUIRE_EMAIL_VALIDATION_TO, ['ask'])
        self.assertProblem(self.post('/questions', new, self.raw), 403, 'email-confirmation-required')
        self.set(settings.REQUIRE_EMAIL_VALIDATION_TO, [])
        # The hourly limit.
        self.set(settings.QUESTIONS_PER_HOUR, 1)
        self.ok(self.post('/questions', new, self.raw), 201)
        response = self.post('/questions', dict(new, title='Another question about projections'), self.raw)
        self.assertProblem(response, 429, 'posting-limit')
        self.assertEqual(response['Retry-After'], '3600')
        # Members a CAPTCHA would be shown to post on the site.
        self.set(settings.QUESTIONS_PER_HOUR, 0)
        self.set(settings.CAPTCHA_IF_REP_LESS_THAN, 10)
        with mock.patch('forum.forms.qanda.call_all_handlers', return_value=[('recaptcha', forms.CharField())]):
            self.assertProblem(self.post('/questions', dict(new, title='Yet another question here'),
                                         self.token(self.newbie)), 403, 'captcha-required')
        with mock.patch('forum.api.endpoints.call_all_handlers', return_value=[True]) as spam:
            self.assertProblem(self.post('/questions', dict(new, title='Cheap watches for everybody'), self.raw),
                               422, 'spam')
        spam.assert_called_once()
        self.assertEqual(spam.call_args[0][0], 'api_is_spam')

    def test_first_posts_are_reviewed(self):
        self.set(settings.FIRST_POSTS_MODERATION, 'before')
        raw = self.token(self.newbie)
        data = self.ok(self.post('/questions', {'title': 'Where can I download the zanzibar toolkit?', 'text': BODY,
                                                'tags': 'collect'}, raw), 201)
        self.assertTrue(data['held_for_review'])
        self.assertTrue(Action.objects.filter(action_type='queueforreview', node_id=data['id']).exists())
        self.assertProblem(self.get('/questions/%d' % data['id']), 404, 'not-found')
        comment = self.ok(self.post('/posts/%d/comments' % data['id'], {'text': 'An update on my own question.'}, raw),
                          201, schema='Comment')
        self.assertTrue(comment['held_for_review'])

    def test_answer(self):
        response = self.post('/questions/%d/answers' % self.question.id,
                             {'text': 'The command line exporter also writes CSV files.'}, self.token(self.mod))
        data = self.ok(response, 201, schema='Answer')
        answer = Answer.objects.get(id=data['id'])
        self.assertEqual((answer.author, answer.parent_id), (self.mod, self.question.id))
        self.assertEqual(response['Location'], 'http://testserver/api/v1/answers/%d' % answer.id)
        self.assertEqual(fresh(self.question).leaf.answer_count, 2)
        self.assertTrue(Action.objects.filter(action_type='answer', node=answer, user=self.mod).exists())
        self.assertProblem(self.post('/questions/%d/answers' % self.question.id, {'text': 'short'}, self.raw),
                           422, 'validation-error')
        self.assertProblem(self.post('/questions/99999/answers', {'text': 'x' * 40}, self.raw), 404, 'not-found')

    def test_closed_questions_take_no_answers(self):
        CloseAction(node=fresh(self.question), user=self.mod, extra='duplicate', ip='127.0.0.1').save(
            data=dict(duplicate_of=None, move_answers=False))
        self.assertProblem(self.post('/questions/%d/answers' % self.question.id, {'text': 'x' * 40}, self.raw),
                           409, 'conflict')

    def test_comment(self):
        data = self.ok(self.post('/posts/%d/comments' % self.answer.id, {'text': 'Thanks @asker, try it.'}, self.raw),
                       201, schema='Comment')
        comment = Comment.objects.get(id=data['id'])
        self.assertEqual((comment.author, comment.parent_id), (self.member, self.answer.id))
        self.assertTrue(Action.objects.filter(action_type='comment', node=comment).exists())
        # Not enough reputation to comment on someone else's post; fine on one's own question's answers.
        newbie = self.token(self.newbie)
        self.assertProblem(self.post('/posts/%d/comments' % self.question.id, {'text': 'A newbie comment here'},
                                     newbie), 403, 'forbidden')
        self.assertProblem(self.post('/posts/%d/comments' % self.question.id, {'text': 'x'}, self.raw),
                           422, 'validation-error')
        self.assertProblem(self.post('/posts/%d/comments' % self.question.id, {'text': 'x' * 1000}, self.raw),
                           422, 'validation-error')
        self.assertProblem(self.post('/posts/%d/comments' % self.comment.id, {'text': 'On a comment, no.'}, self.raw),
                           404, 'not-found')
        self.ok(self.post('/posts/%d/comments' % self.answer.id, {'text': 'Thanks, I will try it.'},
                          self.token(self.asker)), 201)

    def test_votes_change_reputation_as_on_the_web(self):
        second = self.answer_web(self.mod, self.question, 'The command line exporter works too, see the docs.')
        voter = self.create_user('voter', email_isvalid=True, reputation=500)
        raw = self.token(voter)
        before = User.objects.get(id=self.member.id).reputation
        self.login(voter)
        self.command(reverse('vote_post', args=[self.answer.id, 'up']))
        self.client.logout()
        web_gain = User.objects.get(id=self.member.id).reputation - before
        self.assertGreater(web_gain, 0)

        before = User.objects.get(id=self.mod.id).reputation
        data = self.ok(self.post('/posts/%d/vote' % second.id, {'vote': 'up'}, raw), schema='Vote')
        self.assertEqual((data['vote'], data['score']), ('up', 1))
        self.assertEqual(User.objects.get(id=self.mod.id).reputation - before, web_gain)
        self.assertEqual(sorted(ActionRepute.objects.filter(action__node=second).values_list('value', flat=True)),
                         sorted(ActionRepute.objects.filter(action__node=self.answer).values_list('value', flat=True)))
        # The same vote again changes nothing; "none" takes it back, "down" changes it.
        self.assertEqual(self.ok(self.post('/posts/%d/vote' % second.id, {'vote': 'up'}, raw))['score'], 1)
        self.assertEqual(self.ok(self.post('/posts/%d/vote' % second.id, {'vote': 'none'}, raw))['score'], 0)
        self.assertEqual(User.objects.get(id=self.mod.id).reputation, before)
        data = self.ok(self.post('/posts/%d/vote' % second.id, {'vote': 'down'}, raw))
        self.assertEqual((data['vote'], data['score']), ('down', -1))
        self.assertEqual(self.ok(self.post('/posts/%d/vote' % second.id, {'vote': 'up'}, raw))['score'], 1)
        self.assertEqual(Action.objects.filter(node=second, action_type__in=('voteup', 'votedown'),
                                               canceled=False).count(), 1)

    def test_vote_rules(self):
        own = self.post('/posts/%d/vote' % self.answer.id, {'vote': 'up'}, self.raw)
        self.assertProblem(own, 403, 'forbidden')
        data = self.assertProblem(self.post('/posts/%d/vote' % self.answer.id, {'vote': 'down'},
                                            self.token(self.newbie)), 403, 'forbidden')
        self.assertIn('reputation', data['detail'])
        self.assertProblem(self.post('/posts/%d/vote' % self.question.id, {'vote': 'sideways'}, self.raw),
                           422, 'validation-error')
        self.assertProblem(self.post('/posts/%d/vote' % self.comment.id, {'vote': 'up'}, self.raw), 404, 'not-found')
        self.set(settings.MAX_VOTES_PER_DAY, 0)
        self.set(settings.USER_REPUTATION_TO_MAX_VOTES, False)
        self.assertProblem(self.post('/posts/%d/vote' % self.question.id, {'vote': 'up'}, self.raw),
                           429, 'posting-limit')
        self.set(settings.MAX_VOTES_PER_DAY, 30)
        self.ok(self.post('/posts/%d/vote' % self.question.id, {'vote': 'up'}, self.raw))
        Action.objects.filter(action_type='voteup').update(action_date=datetime.datetime.now() - datetime.timedelta(
            days=int(settings.DENY_UNVOTE_DAYS) + 1))
        cache.clear()
        self.assertProblem(self.post('/posts/%d/vote' % self.question.id, {'vote': 'none'}, self.raw),
                           409, 'conflict')

    def test_accept_as_on_the_web(self):
        second = self.answer_web(self.mod, self.question, 'The command line exporter works too, see the docs.')
        other_q = self.ask_web(self.asker, 'Can SEPAL process Sentinel-2 time series?', 'sepal')
        other_a = self.answer_web(self.member, other_q, 'Yes, with the time series recipe in the apps.')
        self.client.logout()
        before = User.objects.get(id=self.member.id).reputation
        self.login(self.asker)
        self.command(reverse('accept_answer', args=[other_a.id]))
        self.client.logout()
        web_gain = User.objects.get(id=self.member.id).reputation - before

        raw = self.token(self.asker)
        self.assertProblem(self.post('/answers/%d/accept' % self.answer.id, token=self.raw), 403, 'forbidden')
        before = User.objects.get(id=self.member.id).reputation
        data = self.ok(self.post('/answers/%d/accept' % self.answer.id, token=raw), schema='Answer')
        self.assertTrue(data['accepted'])
        self.assertEqual(User.objects.get(id=self.member.id).reputation - before, web_gain)
        self.assertTrue(self.ok(self.post('/answers/%d/accept' % self.answer.id, token=raw))['accepted'])
        self.assertEqual(Action.objects.filter(action_type='acceptanswer', canceled=False, node=self.answer).count(), 1)
        self.set(settings.MAXIMUM_ACCEPTED_ANSWERS, 1)
        self.assertProblem(self.post('/answers/%d/accept' % second.id, token=raw), 409, 'conflict')
        response = self.send('delete', '/answers/%d/accept' % self.answer.id, token=raw)
        self.assertFalse(self.ok(response)['accepted'])
        self.assertEqual(User.objects.get(id=self.member.id).reputation, before)
        self.assertEqual(self.ok(self.get('/questions/%d' % self.question.id))['status'], 'answered')
        self.set(settings.DISABLE_ACCEPTING_FEATURE, True)
        self.assertProblem(self.post('/answers/%d/accept' % self.answer.id, token=raw), 403, 'forbidden')

    def test_follow(self):
        data = self.ok(self.post('/questions/%d/follow' % self.question.id, token=self.raw), schema='Following')
        self.assertEqual(data, {'question_id': self.question.id, 'following': True})
        subscription = QuestionSubscription.objects.get(user=self.member, question=self.question)
        self.assertFalse(subscription.auto_subscription)
        self.ok(self.post('/questions/%d/follow' % self.question.id, token=self.raw))
        self.assertEqual(QuestionSubscription.objects.filter(user=self.member, question=self.question).count(), 1)
        data = self.ok(self.send('delete', '/questions/%d/follow' % self.question.id, token=self.raw))
        self.assertFalse(data['following'])
        self.assertFalse(QuestionSubscription.objects.filter(user=self.member, question=self.question).exists())
        self.assertTrue(self.ok(self.get('/questions/%d' % self.question.id, self.token(self.asker)))['following'])

    def test_writes_turned_off(self):
        self.set(settings.API_WRITES_ENABLED, False)
        self.assertProblem(self.post('/questions/%d/follow' % self.question.id, token=self.raw), 403,
                           'writes-disabled')
        self.ok(self.get('/questions', self.raw))

    def test_writes_are_counted(self):
        with mock.patch('forum.api.core.metrics.count') as count:
            self.ok(self.post('/questions/%d/follow' % self.question.id, token=self.raw))
        count.assert_called_with('osqa_api_writes_total', endpoint='followQuestion')


# ---- the documentation ------------------------------------------------------------

class OpenApiTest(ApiTestCase):
    def test_document_structure(self):
        doc = document()
        self.assertEqual(doc['openapi'], '3.1.0')
        self.assertEqual(doc['servers'], [{'url': 'http://testserver'}])
        ids = []

        def refs(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    yield value['$ref']
                for item in value.values():
                    yield from refs(item)
            elif isinstance(value, list):
                for item in value:
                    yield from refs(item)

        for ref in refs(doc):
            self.assertTrue(ref.startswith('#/components/schemas/'), ref)
            self.assertIn(ref.rsplit('/', 1)[-1], doc['components']['schemas'])
        tags = {t['name'] for t in doc['tags']}
        for path, operations in doc['paths'].items():
            self.assertTrue(path.startswith('/api/v1/'), path)
            for method, op in operations.items():
                self.assertIn(method, ('get', 'post', 'delete'))
                ids.append(op['operationId'])
                self.assertTrue(op['summary'])
                self.assertTrue(set(op['tags']) <= tags)
                self.assertTrue(any(code.startswith('2') for code in op['responses']), path)
                names = re.findall(r'\{(\w+)\}', path)
                self.assertEqual(sorted(p['name'] for p in op.get('parameters', ()) if p['in'] == 'path'),
                                 sorted(names), path)
                for code, response in op['responses'].items():
                    self.assertTrue(response['description'], (path, code))
        self.assertEqual(len(ids), len(set(ids)))
        json.dumps(doc)  # serialisable

    def test_documented_routes_exist_and_vice_versa(self):
        from forum.api.urls import urlpatterns
        doc = document()
        documented = set()
        for path, operations in doc['paths'].items():
            concrete = re.sub(r'\{(\w+)\}', lambda m: 'collect' if m.group(1) == 'name' else '1', path)
            match = resolve(concrete)
            self.assertIs(match.func, core.dispatch, path)
            self.assertEqual(core.BASE_PATH + match.kwargs['route'].rstrip('/') if match.kwargs['route'] != '/'
                             else core.BASE_PATH + '/', path.rstrip('/') if path != '/api/v1/' else path)
            documented.add(match.url_name)
            # Every method of the path is served, and nothing else.
            response = self.client.options(concrete)
            self.assertEqual(set(response['Allow'].split(', ')),
                             {m.upper() for m in operations} | {'OPTIONS'} | ({'HEAD'} if 'get' in operations
                                                                              else set()))
        self.assertEqual(documented, {p.name for p in urlpatterns})
        self.assertEqual(len(urlpatterns), len(doc['paths']))

    def test_responses_match_the_schemas(self):
        accepted = self.answer_web(self.mod, self.question, 'The command line exporter works too, see the docs.')
        AcceptAnswerAction(user=self.asker, node=accepted, ip='127.0.0.1').save()
        CloseAction(node=fresh(self.question), user=self.mod, extra='duplicate', ip='127.0.0.1').save(
            data=dict(duplicate_of=None, move_answers=False))
        self.client.logout()
        raw = self.token(self.member)
        doc = document()
        checked = 0
        for path, operations in doc['paths'].items():
            if 'get' not in operations or path.endswith('openapi.json'):
                continue
            object_id = str(self.member.id if '/users/' in path else self.answer.id if '/answers/' in path
                            else self.question.id)
            concrete = re.sub(r'\{(\w+)\}', lambda m, object_id=object_id: 'collect' if m.group(1) == 'name'
                              else object_id, path)
            if path.endswith('/search'):
                concrete += '?q=export'
            response = self.client.get(concrete, HTTP_AUTHORIZATION='Bearer ' + raw)
            self.assertEqual(response.status_code, 200, concrete)
            check_schema(json.loads(response.content), operations['get']['responses']['200']['content'][
                'application/json']['schema'], concrete)
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_docs_page(self):
        response = self.client.get(reverse('api_docs'))
        self.assertCleanPage(response)
        body = html.unescape(response.content.decode())
        for path, operations in document()['paths'].items():
            for op in operations.values():
                self.assertIn('id="op-%s"' % op['operationId'], body)
            self.assertIn(path, body)
        for name in COMPONENTS:
            self.assertIn('id="schema-%s"' % name, body)
        for slug in core.PROBLEMS:
            self.assertIn('id="problem-%s"' % slug, body)
        self.assertIn('curl -X POST "http://testserver/api/v1/questions"', body)
        self.assertIn('/api/v1/openapi.json', body)
        self.assertNotIn('<script src="http', body)
        self.assertIn('Sign in, then create tokens', body)
        self.login(self.member)
        self.assertContains(self.client.get(reverse('api_docs')), reverse('user_api_tokens', args=[self.member.id]))

    def test_linked_from_the_footer_and_llms_txt(self):
        self.assertContains(self.client.get('/'), 'href="/api/"')
        self.assertContains(self.client.get(reverse('llms_txt')), 'http://testserver/api/v1/openapi.json')


@override_settings()
class ApiQueryBudgetTest(QueryBudgetMixin, ApiTestCase):
    def setUp(self):
        super().setUp()
        from forum.settings import base as settings_base
        patcher = mock.patch.object(settings_base, 'TMP_MINICACHE_SECONDS', 3600)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.set(settings.API_RATE_LIMIT_ANONYMOUS, 0)
        for i in range(12):
            author = self.create_user('author%d' % i, email_isvalid=True, reputation=50)
            question = Question(author=author, title='Exporting question number %d' % i, tagnames='collect export',
                                body='A question body that is long enough.')
            question.save()
            Answer(author=self.member, parent=question, body='An answer that is long enough.').save()
            Comment(author=self.asker, parent=question, body='A comment that is long enough.').save()
        for i in range(5):
            answer = Answer(author=self.create_user('answerer%d' % i), parent=self.question,
                            body='Another answer that is long enough.')
            answer.save()
            Comment(author=self.asker, parent=answer, body='A comment that is long enough.').save()

    def test_list(self):
        # Budgets: the visible questions, their authors (object cache) and counts (one grouped query each).
        self.assertQueryBudget(API + '/questions?limit=30', cold=6, warm=3)
        self.assertQueryBudget(API + '/tags', cold=4, warm=4)
        self.assertQueryBudget(API + '/users?limit=30', cold=3, warm=3)

    def test_detail(self):
        # The question, its answers, all comments in one query, their authors (6 answers, 6 comments);
        # with a token also the token, its last use, the reader's votes and following.
        self.assertQueryBudget(API + '/questions/%d' % self.question.id, cold=10, warm=5)
        raw = self.token(self.asker)
        self.assertQueryBudget(API + '/questions/%d' % self.question.id, cold=14, warm=8, label='detail with a token',
                               HTTP_AUTHORIZATION='Bearer ' + raw)
