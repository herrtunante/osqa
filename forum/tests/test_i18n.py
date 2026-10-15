import json

from django.urls import reverse
from django.utils import translation

from forum import settings
from forum.models import Question

from .base import OSQATestCase


def tr(lang, text):
    with translation.override(lang):
        return translation.gettext(text)


class InterfaceLanguageTest(OSQATestCase):
    def test_catalogues_exist(self):
        for lang in ('es', 'fr', 'pt'):
            self.assertNotEqual(tr(lang, 'unanswered'), 'unanswered', lang)
            self.assertNotEqual(tr(lang, 'Mark all as read'), 'Mark all as read', lang)

    def test_browser_language_is_used(self):
        page = self.client.get('/', HTTP_ACCEPT_LANGUAGE='es')
        self.assertContains(page, '<html lang="es"')
        # Tab titles and header links are defined at import time (lazy strings).
        self.assertContains(page, tr('es', 'unanswered'))
        self.assertContains(page, tr('es', 'login'))
        self.assertContains(self.client.get('/', HTTP_ACCEPT_LANGUAGE='de'), '<html lang="en"')

    def test_language_switcher(self):
        page = self.client.get('/')
        self.assertContains(page, 'name="language"')
        self.assertContains(page, '<option value="pt" lang="pt">Português</option>', html=True)
        response = self.client.post(reverse('set_language'), {'language': 'fr', 'next': '/questions/'})
        self.assertRedirects(response, '/questions/', fetch_redirect_response=False)
        page = self.client.get('/questions/')
        self.assertContains(page, '<html lang="fr"')
        self.assertContains(page, tr('fr', 'ask a question'))

    def test_urls_and_forms_in_every_language(self):
        alice = self.create_user('alice', email_isvalid=True, reputation=100)
        self.login(alice)
        for lang in ('es', 'fr', 'pt'):
            with translation.override(lang):
                self.assertEqual(reverse('questions'), '/questions/')
                self.assertEqual(reverse('ask'), '/questions/ask/')
            # Form labels are translated per request, not when the form class is defined.
            page = self.client.get(reverse('ask'), HTTP_ACCEPT_LANGUAGE=lang)
            self.assertContains(page, '<html lang="%s"' % lang)
            # (the title's help is written in ask.html; the tags' help comes from the form)
            self.assertContains(page, tr(lang, 'Tags are short keywords, with no spaces within. At least %(min)s and up to '
                                               '%(max)s tags can be used.') % {'min': settings.FORM_MIN_NUMBER_OF_TAGS,
                                                                               'max': settings.FORM_MAX_NUMBER_OF_TAGS})

        self.client.post(reverse('ask'), {'title': 'How do I export survey data to CSV?',
                                          'text': 'Question body that is long enough.', 'tags': 'collect'},
                         HTTP_ACCEPT_LANGUAGE='es')
        question = Question.objects.get()
        self.assertEqual(self.client.get(question.get_absolute_url(), HTTP_ACCEPT_LANGUAGE='pt').status_code, 200)

    def test_ajax_errors_are_json_in_any_language(self):
        question = Question(author=self.create_user('alice'), title='A question title', body='Body text',
                            tagnames='one')
        question.save()
        response = self.client.post('/vote/%d/up/' % question.id, HTTP_ACCEPT_LANGUAGE='es', **self.AJAX)
        data = json.loads(response.content)
        self.assertFalse(data['success'])
        self.assertTrue(data['error_message'])

    def test_sign_up_page_translated(self):
        page = self.client.get('/account/local/register/', HTTP_ACCEPT_LANGUAGE='pt')
        self.assertContains(page, tr('pt', 'Confirm password'))

    def test_greeting_cookie_still_set(self):
        """request.COOKIES.set() (legacy cookie middleware) still reaches the response."""
        response = self.client.get('/')
        self.assertEqual(response.cookies['greeting_set'].value, 'True')
        self.assertEqual(response.cookies['greeting_set']['samesite'], 'Lax')
