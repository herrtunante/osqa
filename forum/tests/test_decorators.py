from forum.modules import decorate

from .base import OSQATestCase


class Greeter(object):
    def greet(self, name):
        return 'hello %s' % name


def shout(text):
    return text


class DecorateTest(OSQATestCase):
    def test_decorate_method_on_class(self):
        original = Greeter.__dict__['greet']
        self.addCleanup(setattr, Greeter, 'greet', original)

        @decorate(Greeter.greet)
        def loud(origin, self, name):
            return origin(self, name).upper()

        self.assertEqual(Greeter().greet('bob'), 'HELLO BOB')

    def test_decorate_method_without_origin(self):
        original = Greeter.__dict__['greet']
        self.addCleanup(setattr, Greeter, 'greet', original)

        @decorate(Greeter.greet, needs_origin=False)
        def replacement(self, name):
            return 'hi %s' % name

        self.assertEqual(Greeter().greet('bob'), 'hi bob')

    def test_decorate_module_function(self):
        import sys
        module = sys.modules[__name__]
        self.addCleanup(setattr, module, 'shout', shout)

        @decorate(shout)
        def exclaim(origin, text):
            return origin(text) + '!'

        self.assertEqual(module.shout('hey'), 'hey!')

    def test_decorate_manager_method_like_fulltext_modules(self):
        # mysqlfulltext/pgfulltext replace the site search this way.
        from forum.models import Question
        from forum.models.question import QuestionManager
        original = QuestionManager.__dict__['search']
        self.addCleanup(setattr, QuestionManager, 'search', original)

        @decorate(QuestionManager.search, needs_origin=False)
        def search(self, keywords, *args, **kwargs):
            return False, self.filter(title='never matches')

        Question(author=self.create_user('alice'), title='anything at all', body='Body text', tagnames='one').save()
        self.assertEqual(list(Question.objects.search('anything')[1]), [])
