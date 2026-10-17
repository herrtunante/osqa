"""The JSON API in the browser: the API tokens page (create a token, copy it
once, use it, revoke it) on a desktop and on a phone in dark mode, the
documentation at /api/ (reached from the footer), and every /api/v1/ endpoint
called with fetch() from a page, as a program running in a browser would."""
import json
import os

from forum.models import ApiToken, QuestionSubscription

from .base import ARTIFACTS, E2ETestCase

# fetch() in the page: [status, content type, body text].
FETCH = '''async ([url, method, token, body]) => {
    const headers = {};
    if (token) headers['Authorization'] = 'Bearer ' + token;
    if (body !== null) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, {method: method, headers: headers, credentials: 'omit',
                                       body: body === null ? undefined : body});
    return [response.status, response.headers.get('Content-Type') || '', await response.text()];
}'''


class ApiTest(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.asker = self.create_user('asker', reputation=100, email_isvalid=True)
        self.member = self.create_user('member', reputation=500, email_isvalid=True)
        self.question = self.create_question(self.asker, 'How do I export Collect survey data to CSV?',
                                             tags='collect export')
        self.answer = self.create_answer(self.member, self.question, 'Use **Export > CSV** in the designer.')
        self.create_comment(self.member, self.question, 'Which version of Collect do you use?')

    def call(self, page, path, method='GET', token=None, body=None):
        status, kind, text = page.evaluate(FETCH, ['/api/v1' + path, method, token,
                                                   None if body is None else json.dumps(body)])
        self.assertLess(status, 500, '%s %s: %s' % (method, path, text[:300]))
        if status >= 400:
            # Chromium logs every 4xx answer to fetch() as a console error; these are expected.
            page.problems[:] = [p for p in page.problems if '/api/v1' + path.split('?')[0] not in p]
        return status, kind, (json.loads(text) if text else None)

    def create_token(self, page, name, write=True):
        page.fill('#id_name', name)
        page.check('input[name=access][value=%s]' % ('write' if write else 'read'))
        page.select_option('#id_expires', '30')
        page.click('button:has-text("Create token")')
        page.wait_for_load_state('load')
        self.check_page(page)
        return page.input_value('#api-new-token')

    def test_token_page_create_copy_use_revoke(self):
        page = self.open(self.member, permissions=['clipboard-read', 'clipboard-write'])
        self.visit(page, '/account/%d/security/' % self.member.id)
        page.click('.settings-nav a:has-text("API tokens")')
        page.wait_for_load_state('load')
        self.check_page(page)
        self.assertEqual(page.locator('.settings-nav a[aria-current=page]').inner_text(), 'API tokens')
        self.assertTrue(page.locator('text=No tokens yet.').is_visible())

        raw = self.create_token(page, 'Weekly report')
        self.assertTrue(raw.startswith('osqa_'))
        page.click('[data-copy-target="#api-new-token"]')
        page.wait_for_selector('.api-token-status:has-text("Copied")')
        self.assertEqual(page.evaluate('navigator.clipboard.readText()'), raw)
        self.assertIn('Weekly report', page.locator('.api-tokens').inner_text())

        # The token works...
        status, _, me = self.call(page, '/me', token=raw)
        self.assertEqual((status, me['user']['username'], me['token']['scopes']), (200, 'member', ['read', 'write']))
        status, _, data = self.call(page, '/questions/%d/follow' % self.question.id, 'POST', raw)
        self.assertEqual((status, data['following']), (200, True))

        # ...is never shown again, and stops working once revoked.
        self.visit(page, '/account/%d/api-tokens/' % self.member.id)
        self.assertEqual(page.locator('#api-new-token').count(), 0)
        self.assertNotIn(raw, page.content())
        self.assertIn('last used', page.locator('.api-tokens').inner_text())
        page.click('.api-tokens button:has-text("Revoke")')
        page.wait_for_load_state('load')
        self.check_page(page)
        self.assertTrue(page.locator('text=No tokens yet.').is_visible())
        self.assertFalse(ApiToken.objects.exists())
        status, kind, problem = self.call(page, '/me', token=raw)
        self.assertEqual((status, kind, problem['title']), (401, 'application/problem+json; charset=utf-8',
                                                             'Invalid token'))

    def test_token_page_on_a_phone_in_dark_mode(self):
        page = self.open(self.member, device='mobile', dark=True)
        self.visit(page, '/account/%d/api-tokens/' % self.member.id)
        raw = self.create_token(page, 'Phone script with a rather long name to wrap', write=False)
        self.assertEqual(ApiToken.objects.get().scopes, 'read')
        field = page.locator('#api-new-token').bounding_box()
        self.assertLessEqual(field['x'] + field['width'], 390)
        background = page.evaluate("getComputedStyle(document.body).backgroundColor")
        self.assertNotEqual(background, 'rgb(255, 255, 255)')
        os.makedirs(ARTIFACTS, exist_ok=True)
        page.screenshot(path=os.path.join(ARTIFACTS, 'api-tokens-phone-dark.png'), full_page=True)
        status, _, problem = self.call(page, '/questions/%d/follow' % self.question.id, 'POST', raw)
        self.assertEqual((status, problem['status']), (403, 403))
        self.assertIn('scope', problem['detail'])

    def test_docs_page(self):
        for device, dark in (('desktop', False), ('mobile', True)):
            page = self.open(device=device, dark=dark)
            self.visit(page, '/')
            page.click('.footerLinks a:has-text("API")')
            page.wait_for_load_state('load')
            self.check_page(page, '/api/ on %s' % device)
            self.assertEqual(page.locator('h1').inner_text(), 'API documentation')
            self.assertTrue(page.locator('#op-askQuestion').is_visible())
            page.click('.api-index a[href="#op-getQuestion"]')
            self.assertTrue(page.evaluate("location.hash") == '#op-getQuestion')
            self.assertIn('/api/v1/openapi.json', page.locator('a:has-text("OpenAPI 3.1 document")').get_attribute(
                'href'))
            if device == 'mobile':
                os.makedirs(ARTIFACTS, exist_ok=True)
                page.screenshot(path=os.path.join(ARTIFACTS, 'api-docs-phone-dark.png'))

    def test_every_endpoint_from_a_browser(self):
        token = ApiToken.objects.create_token(self.asker, 'e2e', ('read', 'write'))[1]
        page = self.open()
        self.visit(page, '/api/')
        q, a = self.question.id, self.answer.id
        reads = ['/', '/openapi.json', '/questions?sort=newest&limit=5', '/questions/%d' % q,
                 '/questions/%d/answers' % q, '/answers/%d' % a, '/search?q=export', '/tags', '/tags/collect',
                 '/users?sort=name', '/users/%d' % self.member.id, '/badges']
        for path in reads:
            status, kind, data = self.call(page, path)
            self.assertEqual((status, kind), (200, 'application/json; charset=utf-8'), path)
        self.assertEqual(self.call(page, '/me', token=token)[2]['user']['username'], 'asker')

        status, _, question = self.call(page, '/questions', 'POST', token, {
            'title': 'Which projection does Collect Earth use?', 'text': 'I need to know the projection of the plots.',
            'tags': ['collect']})
        self.assertEqual(status, 201)
        status, _, answer = self.call(page, '/questions/%d/answers' % q, 'POST', token,
                                      {'text': 'Answering my own question: the designer exports CSV.'})
        self.assertEqual(status, 201)
        self.assertEqual(self.call(page, '/posts/%d/comments' % a, 'POST', token, {'text': 'Thanks, that worked.'})[0],
                         201)
        self.assertEqual(self.call(page, '/posts/%d/vote' % a, 'POST', token, {'vote': 'up'})[2]['score'], 1)
        self.assertTrue(self.call(page, '/answers/%d/accept' % a, 'POST', token)[2]['accepted'])
        self.assertFalse(self.call(page, '/answers/%d/accept' % a, 'DELETE', token)[2]['accepted'])
        self.assertTrue(self.call(page, '/questions/%d/follow' % question['id'], 'POST', token)[2]['following'])
        self.assertFalse(self.call(page, '/questions/%d/follow' % q, 'DELETE', token)[2]['following'])
        self.assertFalse(QuestionSubscription.objects.filter(user=self.asker, question_id=q).exists())
        self.assertEqual(answer['question_id'], q)

        # The new question is on the site.
        self.visit(page, '/questions/%d/' % question['id'])
        self.assertIn('Which projection does Collect Earth use?', page.locator('h1').inner_text())
