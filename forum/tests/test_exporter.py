import io
import os
import shutil
import tempfile
import zipfile
from unittest import mock

from django.apps import apps
from django.core.cache import cache
from django.db import connection
from django.urls import reverse

from forum.models import (Action, ActionRepute, Answer, Award, Badge, Comment, KeyValue, MarkedTag, Node, NodeRevision,
                          NodeState, Question, Tag, User, Vote)
from forum_modules.exporter import settings as exporter_settings
from forum_modules.exporter.exporter import CACHE_KEY

from .base import OSQATestCase


def run_now(target, *args):
    target(*args)


class ExporterTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.storage = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        exporter_settings.EXPORTER_BACKUP_STORAGE.set_value(self.storage)
        self.addCleanup(setattr, exporter_settings.EXPORTER_BACKUP_STORAGE, '_temp', None)

        # Export and import run in a background thread, which can't see the
        # test transaction; run them inline.
        patcher = mock.patch('forum_modules.exporter.views.run_in_thread', run_now)
        patcher.start()
        self.addCleanup(patcher.stop)


class AccessTest(ExporterTestCase):
    def test_requires_superuser(self):
        urls = ['/admin/tools/exporter/', '/admin/tools/importer/', '/admin/exporter/state/',
                '/admin/exporter/running/', '/admin/exporter/download/?file=x.zip']

        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.post('/admin/tools/exporter/', {'file_format': 'zip'}).status_code, 401)

        self.login(self.create_user('alice'))
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post('/admin/tools/exporter/', {'file_format': 'zip'}).status_code, 403)
        self.assertEqual(os.listdir(self.storage), [])

    def test_download_only_serves_backups(self):
        self.login(self.create_user('admin', is_superuser=True))
        with open(os.path.join(self.storage, 'site.zip'), 'wb') as f:
            f.write(b'zip data')

        response = self.client.get('/admin/exporter/download/?file=site.zip')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'zip data')

        for name in ('../site.zip', '../../settings.py', 'backup.inf', '/etc/passwd', ''):
            self.assertEqual(self.client.get('/admin/exporter/download/', {'file': name}).status_code, 404, name)

    def test_import_rejects_invalid_files(self):
        self.login(self.create_user('admin', is_superuser=True))

        for name, content in (('dump.zip', b'not a backup'), ('dump.zip', self.zip({'users.xml': '<users />'}))):
            dump = io.BytesIO(content)
            dump.name = name
            response = self.client.post('/admin/tools/importer/', {'dump': dump})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'class="error"')
        self.assertIsNone(cache.get(CACHE_KEY))

    def zip(self, files):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            for name, content in files.items():
                z.writestr(name, content)
        return data.getvalue()


class ExportImportTest(ExporterTestCase):
    def populate(self):
        self.admin = self.create_user('admin', is_superuser=True, is_staff=True)
        alice = self.create_user('alice', real_name='Alice <A>', about='About & me')
        bob = self.create_user('bob', reputation=1000)

        self.login(alice)
        self.client.post('/questions/ask/', {
            'title': 'How do I back up the whole site?',
            'text': 'Some **body** text that is long enough, with unicode: café ☃',
            'tags': 'backup django',
        })
        question = Question.objects.get(title='How do I back up the whole site?')

        self.login(bob)
        self.client.post('/questions/%d/answer/' % question.id,
                         {'text': 'Use the XML exporter, it has enough characters.'})
        answer = Answer.objects.get(parent=question)
        self.assertTrue(self.command('/comment/%d/' % answer.id, {'comment': 'A comment that is long enough.'})['success'])
        self.assertTrue(self.command('/vote/%d/up/' % question.id)['success'])
        MarkedTag(user=bob, tag=Tag.objects.get(name='django'), reason='good').save()

        self.login(alice)
        self.assertTrue(self.command('/accept_answer/%d/' % answer.id)['success'])

    def snapshot(self):
        cache.clear()

        def node(n):
            return (n.node_type, n.title, n.body, n.tagnames, n.score, n.author.username, n.marked,
                    n.parent and n.parent.title, n.abs_parent and n.abs_parent.title, n.state_string,
                    n.active_revision.revision, n.extra_count)

        return {
            'users': sorted((u.username, u.email, u.password, u.reputation, u.real_name, u.about, u.bronze,
                             u.is_superuser or u.is_staff) for u in User.objects.all()),
            'nodes': sorted(node(n) for n in Node.objects.all()),
            'revisions': sorted((r.node.title, r.revision, r.body, r.author.username) for r in NodeRevision.objects.all()),
            'tags': sorted((t.name, t.used_count, t.created_by.username,
                            sorted(n.title for n in Question.objects.filter(tags=t))) for t in Tag.objects.all()),
            'votes': sorted((v.user.username, v.node.title, v.node.node_type, v.value) for v in Vote.objects.all()),
            'awards': sorted((a.badge.cls, a.user.username, a.action.action_type) for a in Award.objects.all()),
            'actions': sorted((a.action_type, a.user.username, a.node and a.node.node_type, a.canceled)
                              for a in Action.objects.all()),
            'states': sorted((s.node.node_type, s.state_type, s.action.action_type) for s in NodeState.objects.all()),
            'reputes': sorted((r.user.username, r.value, r.action.action_type) for r in ActionRepute.objects.all()),
            'marked_tags': sorted((m.user.username, m.tag.name, m.reason) for m in MarkedTag.objects.all()),
        }

    def clear_site(self):
        # Empty every table but the settings and badge definitions.
        keep = (KeyValue, Badge)
        tables = [m._meta.db_table for m in apps.get_app_config('forum').get_models(include_auto_created=True)
                  if m not in keep and not m._meta.proxy]
        tables += ['auth_user_groups', 'auth_user_user_permissions', 'auth_user']
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute('DELETE FROM %s' % connection.ops.quote_name(table))
        Badge.objects.update(awarded_count=0)
        cache.clear()
        self.client.logout()

    def test_export_and_import_round_trip(self):
        self.populate()
        from forum.models import TagSynonym
        django_tag = Tag.objects.get(name='django')
        django_tag.description = 'The **Django** web framework.'
        django_tag.save()
        TagSynonym(source_name='dj', target=django_tag).save()
        before = self.snapshot()
        old_question = Question.objects.get()
        old_url = old_question.get_absolute_url()

        # The data the test relies on is there.
        self.assertEqual(Question.objects.count(), 1)
        self.assertEqual(Answer.objects.count(), 1)
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(len(before['votes']), 1)
        self.assertIn(('Supporter', 'bob', 'award'), before['awards'])
        self.assertIn(('answer', 'accepted', 'acceptanswer'), before['states'])

        self.login(self.admin)
        response = self.client.post('/admin/tools/exporter/', {'file_format': 'zip', 'uplodaded_files': ''})
        self.assertRedirects(response, reverse('exporter_running', kwargs={'mode': 'exporter'}),
                             fetch_redirect_response=False)

        state = self.client.get('/admin/exporter/state/').json()
        self.assertFalse(state['running'])
        self.assertFalse(state['errors'])
        fname = state['state']['overall']['fname']
        self.assertTrue(fname.endswith('.zip'))
        self.assertEqual(state['state']['nodes']['parsed'], 3)

        self.assertCleanPage(self.client.get('/admin/exporter/running/'))
        self.assertContains(self.client.get('/admin/tools/exporter/'), fname)

        response = self.client.get('/admin/exporter/download/', {'file': fname})
        self.assertEqual(response.status_code, 200)
        backup = b''.join(response.streaming_content)
        with zipfile.ZipFile(io.BytesIO(backup)) as z:
            self.assertEqual(sorted(z.namelist()), ['actions.xml', 'awards.xml', 'backup.inf', 'nodes.xml',
                                                    'settings.xml', 'states.xml', 'tags.xml', 'users.xml'])

        # Restore into an empty site, as its first (super) user.
        self.clear_site()
        restorer = self.create_user('restorer', is_superuser=True)
        self.login(restorer)

        dump = io.BytesIO(backup)
        dump.name = 'backup.zip'
        response = self.client.post('/admin/tools/importer/', {'dump': dump})
        self.assertRedirects(response, reverse('exporter_running', kwargs={'mode': 'importer'}),
                             fetch_redirect_response=False)

        state = self.client.get('/admin/exporter/state/').json()
        self.assertFalse(state['running'])
        self.assertFalse(state['errors'])
        self.assertEqual(os.listdir(os.path.join(os.path.dirname(exporter_settings.__file__), 'tmp')), ['README'])

        after = self.snapshot()
        restorer_row = [u for u in after['users'] if u[0] == 'restorer']
        after['users'].remove(restorer_row[0])

        for key in before:
            self.assertEqual(after[key], before[key], key)

        # Imported users can log in with their old password.
        self.assertTrue(self.client.login(username='alice', password='secret-pass'))

        question = Question.objects.get(title='How do I back up the whole site?')
        answer = Answer.objects.get(parent=question)
        self.assertTrue(answer.nis.accepted)
        self.assertEqual(Comment.objects.get().parent.id, answer.id)
        self.assertEqual(Comment.objects.get().abs_parent.id, question.id)
        self.assertEqual(question.answer_count, 1)
        self.assertEqual(question.accepted_count, 1)
        self.assertEqual(User.objects.get(username='bob').subscription_settings.notify_answers, True)

        page = self.client.get(question.get_absolute_url())
        self.assertCleanPage(page)
        self.assertContains(page, 'café ☃')
        self.assertContains(page, 'A comment that is long enough.')

        # Tag descriptions and synonyms survive.
        self.assertEqual(Tag.objects.get(name='django').description, 'The **Django** web framework.')
        self.assertEqual(TagSynonym.objects.get().target.name, 'django')
        self.assertEqual(TagSynonym.objects.get().source_name, 'dj')

        # Links with the question's old id still work.
        self.assertNotEqual(question.id, old_question.id)
        self.assertContains(self.client.get(old_url), 'caf\u00e9 \u2603')

    def test_import_merges_users_with_the_same_email(self):
        self.populate()
        self.login(self.admin)
        self.client.post('/admin/tools/exporter/', {'file_format': 'tgz'})
        fname = self.client.get('/admin/exporter/state/').json()['state']['overall']['fname']
        self.assertTrue(fname.endswith('.tar.gz'))

        users, nodes = User.objects.count(), Node.objects.count()
        alice_rep = User.objects.get(username='alice').reputation

        with open(os.path.join(self.storage, fname), 'rb') as dump:
            response = self.client.post('/admin/tools/importer/', {'dump': dump})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.client.get('/admin/exporter/state/').json()['errors'])

        cache.clear()
        # Users are matched by email; all content is added again.
        self.assertEqual(User.objects.count(), users)
        self.assertEqual(Node.objects.count(), nodes * 2)
        self.assertEqual(User.objects.get(username='alice').reputation, alice_rep * 2)
        self.assertEqual(Tag.objects.get(name='django').used_count, 2)
        self.assertEqual(Question.objects.filter(title='How do I back up the whole site?').count(), 2)
