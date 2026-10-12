import base64
import datetime
import pickle
import zlib

from forum.models import Question, Node, Tag, Badge
from forum.models.action import ActionProxyMetaClass
from forum.models.node import NodeMetaClass
from forum.models.utils import PickledObjectField

from .base import OSQATestCase


class PickledObjectFieldTest(OSQATestCase):
    field = PickledObjectField()

    def roundtrip(self, value):
        return self.field.to_python(self.field.get_db_prep_value(value, None))

    def test_roundtrip(self):
        for value in (True, False, 7, 1.5, 'short', 'x' * 200, {'a': [1, 2]}, datetime.datetime(2020, 1, 2)):
            self.assertEqual(self.roundtrip(value), value)

    def test_stores_text_not_bytes_repr(self):
        self.assertFalse(self.field.get_db_prep_value({'a': 1}, None).startswith("b'"))

    def test_reads_values_written_as_bytes_repr(self):
        stored = "b'%s'" % base64.b64encode(zlib.compress(pickle.dumps(True))).decode()
        self.assertIs(self.field.to_python(stored), True)

    def test_reads_python2_pickles(self):
        py2_datetime = (b'\x80\x02cdatetime\ndatetime\nq\x00U\n\x07\xe4\x01\x02\x00\x00\x00\x00\x00\x00'
                        b'q\x01\x85q\x02Rq\x03.')
        stored = base64.b64encode(zlib.compress(py2_datetime)).decode()
        self.assertEqual(self.field.to_python(stored), datetime.datetime(2020, 1, 2))
        self.assertEqual(self.field.to_python('T[unicode]hello'), 'hello')
        self.assertEqual(self.field.to_python('T[long]5'), 5)


class ModelRegistrationTest(OSQATestCase):
    def test_metaclasses_ran(self):
        self.assertTrue({'question', 'answer', 'comment'} <= set(NodeMetaClass.types))
        self.assertIn('voteup', ActionProxyMetaClass.types)
        self.assertTrue(ActionProxyMetaClass.types['voteup']._meta.proxy)

    def test_badge_save(self):
        Badge(cls='TestBadge', type=Badge.BRONZE).save()


class NodeQueryTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user('alice')
        self.question = Question(author=self.user, title='A question title', body='Body text', tagnames='one two')
        self.question.save()

    def test_get_returns_leaf_type(self):
        self.assertIsInstance(Node.objects.get(id=self.question.id), Question)

    def test_get_respects_filters(self):
        Question.objects.get(id=self.question.id)  # populate the object cache
        with self.assertRaises(Question.DoesNotExist):
            Question.objects.filter(title='something else').get(id=self.question.id)

    def test_tag_manager_filter(self):
        self.assertEqual(Tag.objects.get(name='one').name, 'one')

    def test_denormalized_counts(self):
        self.assertEqual(self.question.answer_count, 0)
        self.assertEqual(list(self.question.answers), [])

    def test_urls_are_strings(self):
        self.assertEqual(self.question.get_absolute_url(), '/questions/%d/a-question-title' % self.question.id)
        self.assertEqual(self.user.get_profile_url(), '/users/%d/alice' % self.user.id)


class MarkdownTest(OSQATestCase):
    def test_body_is_rendered_with_auto_linker(self):
        html = Node._as_markdown('Some **bold** http://example.com/x', 'auto_linker')
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<a href="http://example.com/x">', html)

    def test_limited_syntax_has_no_images(self):
        html = Node._as_markdown('![x][1]\n\n[1]: http://example.com/a.png', 'limitedsyntax')
        self.assertNotIn('<img', html)


class BulkDeleteTest(OSQATestCase):
    def test_queryset_delete_with_children(self):
        from forum.models import Answer
        user = self.create_user('alice')
        question = Question(author=user, title='A question title', body='Body text', tagnames='one')
        question.save()
        Answer(author=user, parent=question, body='An answer long enough').save()

        Question.objects.filter(id=question.id).delete()

        self.assertFalse(Node.objects.filter(id=question.id).exists())
        self.assertFalse(Node.objects.filter(parent_id=question.id).exists())
