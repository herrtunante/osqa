import io
import os
import tempfile
import zipfile
from base64 import b64encode
from xml.sax.saxutils import escape

from django.core.cache import cache

from forum import settings
from forum.models import Action, Answer, Award, Comment, Node, Page, Question, Tag, User, Vote

from .base import OSQATestCase


def table(name, rows):
    """An SX 1.0 dump table: one <row> per dict, one child element per field."""
    xml = ['<?xml version="1.0" encoding="utf-8"?>', '<%s>' % name]
    for row in rows:
        xml.append('<row>%s</row>' % ''.join('<%s>%s</%s>' % (k, escape(str(v)), k) for k, v in row.items()))
    xml.append('</%s>' % name)
    return '\n'.join(xml)


DUMP = {
    'Users': [
        {'Id': -1, 'DisplayName': 'Community', 'CreationDate': '2009-01-01T00:00:00', 'Reputation': 1},
        {'Id': 2, 'DisplayName': 'Owner', 'CreationDate': '2009-01-01T00:00:00', 'Reputation': 100,
         'UserTypeId': 5, 'AboutMe': 'The SX owner'},
        {'Id': 3, 'DisplayName': 'Carol', 'Email': 'carol@example.com', 'CreationDate': '2009-02-01T10:00:00.123',
         'LastAccessDate': '2009-03-01T10:00:00', 'Reputation': 42, 'UserTypeId': 3, 'RealName': 'Carol C',
         'BadgeSummary': '1=0 2=1 3=2', 'OpenId': 'http://carol.myopenid.com/'},
        {'Id': 4, 'DisplayName': 'Dave', 'CreationDate': '2009-02-02T10:00:00', 'Reputation': 7, 'UserTypeId': 3},
        # Same display name as an existing user of the site.
        {'Id': 5, 'DisplayName': 'erin', 'CreationDate': '2009-02-03T10:00:00', 'Reputation': 1, 'UserTypeId': 3},
    ],
    'Tags': [
        {'Id': 1, 'Name': 'python', 'Count': 1, 'UserId': 3},
        {'Id': 2, 'Name': 'xml', 'Count': 1, 'UserId': 3},
    ],
    'Posts': [
        {'Id': 10, 'PostTypeId': 1, 'CreationDate': '2009-02-05T10:00:00', 'Score': 1, 'ViewCount': 5,
         'Body': '<p>How do I parse <b>XML</b> in Python?</p>', 'OwnerUserId': 3, 'Title': 'Parsing XML & stuff',
         'Tags': 'python xml', 'LastActivityUserId': 4, 'LastActivityDate': '2009-02-06T10:00:00'},
        {'Id': 11, 'PostTypeId': 2, 'ParentId': 10, 'CreationDate': '2009-02-06T10:00:00', 'Score': 0,
         'Body': '<p>Use ElementTree.</p>', 'OwnerUserId': 4},
    ],
    'PostComments': [
        {'Id': 1, 'PostId': 11, 'CreationDate': '2009-02-06T11:00:00', 'Text': 'Thanks, that works!', 'UserId': 3},
    ],
    'Posts2Votes': [
        {'Id': 1, 'PostId': 10, 'UserId': 4, 'VoteTypeId': 2, 'CreationDate': '2009-02-06T10:00:00',
         'TargetUserId': 3, 'TargetRepChange': 10},
        {'Id': 2, 'PostId': 11, 'UserId': 3, 'VoteTypeId': 1, 'CreationDate': '2009-02-06T12:00:00'},
    ],
    'Comments2Votes': [
        {'Id': 1, 'PostCommentId': 1, 'UserId': 4, 'VoteTypeId': 2, 'CreationDate': '2009-02-06T12:00:00'},
    ],
    'Badges': [{'Id': 1, 'Name': 'Teacher', 'Class': 3}, {'Id': 2, 'Name': 'SX Only Badge', 'Class': 2}],
    'Users2Badges': [
        {'Id': 1, 'BadgeId': 1, 'UserId': 4, 'Date': '2009-02-07T10:00:00'},
        {'Id': 2, 'BadgeId': 2, 'UserId': 3, 'Date': '2009-02-07T10:00:00'},
    ],
    'FlatPages': [
        {'Id': 1, 'Name': 'About us', 'Url': '/about-us', 'Value': b64encode(b'<p>About the site</p>').decode(),
         'ContentType': 'text/html', 'UseMaster': 'true', 'Active': 'true'},
    ],
    'ThemeTextResources': [{'Name': 'theme.html.name', 'Value': 'Imported &amp; site'}],
}


def make_dump(tables=DUMP, extra=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        for name, rows in tables.items():
            z.writestr('%s.xml' % name, table(name, rows))
        for name, content in (extra or {}).items():
            z.writestr(name, content)
    data.seek(0)
    data.name = 'dump.zip'
    return data


class SXImporterTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.create_user('admin', is_superuser=True, is_staff=True)

    def post_dump(self, dump, user=None, **data):
        self.login(user or self.admin)
        data.setdefault('owneruid', '2')
        return self.client.post('/admin/sximporter/', dict(data, dump=dump))

    def test_import(self):
        self.create_user('erin')
        response = self.post_dump(make_dump())
        self.assertContains(response, 'The dump was imported.')
        cache.clear()

        # The owner's SX account is merged into the importing user.
        admin = User.objects.get(id=self.admin.id)
        self.assertEqual(admin.username, 'Owner')
        self.assertEqual(admin.about, 'The SX owner')
        self.assertTrue(admin.is_superuser)

        carol = User.objects.get(username='Carol')
        dave = User.objects.get(username='Dave')
        self.assertEqual(carol.email, 'carol@example.com')
        self.assertEqual(carol.reputation, 42)
        self.assertEqual((carol.gold, carol.silver, carol.bronze), (0, 1, 2))
        self.assertEqual(carol.real_name, 'Carol C')
        self.assertEqual(carol.auth_keys.get().key, 'http://carol.myopenid.com/')
        self.assertFalse(carol.has_usable_password())
        self.assertEqual(User.objects.get(username='erin 1').reputation, 1)
        self.assertEqual(User.objects.count(), 5)

        question = Question.objects.get()
        self.assertEqual(question.title, 'Parsing XML & stuff')
        self.assertEqual(question.author, carol)
        self.assertEqual(question.score, 1)
        self.assertEqual(question.extra_count, 5)
        self.assertEqual(sorted(question.tagname_list()), ['python', 'xml'])
        self.assertEqual(sorted(question.tags.values_list('name', flat=True)), ['python', 'xml'])
        self.assertEqual(question.active_revision.revision, 1)
        self.assertEqual(question.last_activity_by, dave)

        answer = Answer.objects.get()
        self.assertEqual(answer.parent.id, question.id)
        self.assertEqual(answer.author, dave)
        self.assertTrue(answer.marked)
        self.assertTrue(answer.nis.accepted)
        self.assertEqual(question.accepted_count, 1)
        self.assertEqual(Node.objects.get(id=question.id).extra_ref_id, answer.id)

        comment = Comment.objects.get()
        self.assertEqual(comment.parent.id, answer.id)
        self.assertEqual(comment.abs_parent.id, question.id)
        self.assertEqual(comment.author, carol)
        self.assertEqual(comment.score, 1)

        self.assertEqual(sorted(Vote.objects.values_list('user__username', 'node__node_type', 'value')),
                         [('Dave', 'comment', 1), ('Dave', 'question', 1)])
        self.assertEqual(Tag.objects.get(name='python').used_count, 1)
        self.assertEqual(Tag.objects.get(name='python').created_by, carol)
        self.assertEqual(sorted(Award.objects.values_list('badge__cls', 'user__username')),
                         [('SXOnlyBadge', 'Carol'), ('Teacher', 'Dave')])
        self.assertEqual(Award.objects.get(user=dave).badge.awarded_count, 1)
        self.assertEqual(Action.objects.filter(action_type='userjoins').count(), 3)

        page = Page.objects.get()
        self.assertEqual(page.title, 'About us')
        self.assertTrue(page.nis.published)
        self.assertEqual(settings.STATIC_PAGE_REGISTRY.value['about-us'], page.id)
        self.assertEqual(settings.APP_TITLE.value, 'Imported & site')

        self.client.logout()
        response = self.client.get(question.get_absolute_url())
        self.assertCleanPage(response)
        self.assertContains(response, 'Use ElementTree.')
        self.assertContains(response, 'Thanks, that works!')

    def test_optional_tables_may_be_missing(self):
        tables = dict((k, DUMP[k]) for k in ('Users', 'Tags', 'Posts'))
        self.assertContains(self.post_dump(make_dump(tables)), 'The dump was imported.')
        self.assertEqual(Question.objects.count(), 1)
        self.assertEqual(Answer.objects.count(), 1)

    def test_rejects_member_outside_the_target_folder(self):
        users = User.objects.count()

        for name in ('../evil.xml', '../../evil.xml', 'dir/../../evil.xml', '/tmp/evil.xml', '..\\evil.xml'):
            response = self.post_dump(make_dump(extra={name: '<Users></Users>'}))
            self.assertContains(response, 'invalid file name')
            self.assertNotContains(response, 'The dump was imported.')

        self.assertFalse(os.path.exists(os.path.join(tempfile.gettempdir(), 'evil.xml')))
        self.assertFalse(os.path.exists('/tmp/evil.xml'))
        self.assertEqual(User.objects.count(), users)
        self.assertEqual(Node.objects.count(), 0)

    def test_failed_import_is_rolled_back(self):
        tables = dict(DUMP, Posts=DUMP['Posts'] + [{'Id': 12, 'PostTypeId': 1, 'CreationDate': 'not a date'}])
        response = self.post_dump(make_dump(tables))
        self.assertContains(response, 'The import failed: time data')
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Node.objects.count(), 0)
        self.assertEqual(User.objects.get().username, 'admin')

    def test_requires_superuser(self):
        # Anonymous, even on a site without users.
        User.objects.all().delete()
        cache.clear()
        self.assertEqual(self.client.get('/admin/sximporter/').status_code, 401)
        self.assertEqual(self.client.post('/admin/sximporter/', {'dump': make_dump()}).status_code, 401)
        self.assertEqual(User.objects.count(), 0)

        self.admin = self.create_user('admin', is_superuser=True, is_staff=True)
        self.assertEqual(self.client.post('/admin/sximporter/', {'dump': make_dump()}).status_code, 401)

        # Moderators and normal users.
        for user in (self.create_user('mod', is_staff=True), self.create_user('carl')):
            self.login(user)
            self.assertEqual(self.client.get('/admin/sximporter/').status_code, 403)
            self.assertEqual(self.post_dump(make_dump(), user=user).status_code, 403)

        self.assertEqual(Node.objects.count(), 0)
        self.assertEqual(User.objects.count(), 3)

        self.login(self.admin)
        self.assertContains(self.client.get('/admin/sximporter/'), 'name="dump"')
