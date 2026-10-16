"""Tag management for moderators (forum/utils/tags.py, forum/views/tags.py):
merge, rename, synonyms, descriptions, their Actions and undo, permissions,
and synonyms in the tag suggestions."""
from django.core.cache import cache

from forum.actions import DeleteAction
from forum.models import Action, MarkedTag, Question, SearchDocument, Tag, TagSynonym

from .base import OSQATestCase


class TagManagementTest(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.mod = self.create_user('mod', reputation=1, is_staff=True)
        self.alice = self.create_user('alice', reputation=10000)  # lots of reputation, no moderator
        self.q1 = self.ask('How do I read Collect CSV dates in R?', 'csv collect')
        self.q2 = self.ask('Exporting plots as CSV files', 'csv export')
        self.q3 = self.ask('Exporting a survey to KML', 'export')
        self.q4 = self.ask('A deleted question about CSV', 'csv')
        self.delete(self.q4)
        TagSynonym.objects.create(source_name='comma-separated', target=Tag.objects.get(name='csv'))

    def ask(self, title, tags):
        question = Question(author=self.alice, title=title, tagnames=tags, body='A question body that is long enough.')
        question.save()
        return question

    def delete(self, question):
        from forum import REQUEST_HOLDER
        from django.test import RequestFactory
        request = RequestFactory().get('/')
        request.user = self.mod
        REQUEST_HOLDER.request = request
        DeleteAction(user=self.mod, node=question, ip='127.0.0.1').save()

    def mark(self, user, tag, reason):
        MarkedTag.objects.create(user=user, tag=Tag.objects.get(name=tag), reason=reason)

    def marks(self, user):
        return sorted(MarkedTag.objects.filter(user=user).values_list('tag__name', 'reason'))

    def fresh(self, question):
        cache.clear()
        return Question.objects.get(id=question.id)

    def count(self, name):
        return Tag.objects.filter(name=name).values_list('used_count', flat=True).first()

    # -- merge ---------------------------------------------------------------

    def test_merge_summary_page(self):
        self.login(self.mod)
        response = self.client.get('/tags/csv/merge/', {'into': 'export'})
        self.assertContains(response, '3 questions will move to export.')
        self.assertContains(response, '1 of them already has export')
        self.assertContains(response, '1 of them is deleted or waiting for review.')
        self.assertContains(response, "A merge can't be undone.")
        self.assertContains(response, 'comma-separated')
        self.assertTrue(Tag.objects.filter(name='csv').exists())  # nothing changed yet

    def test_merge(self):
        bob, carol, dave, erin = (self.create_user(name) for name in ('bob', 'carol', 'dave', 'erin'))
        self.mark(bob, 'csv', 'good')
        self.mark(carol, 'csv', 'good')
        self.mark(carol, 'export', 'good')
        self.mark(dave, 'csv', 'bad')
        self.mark(erin, 'csv', 'good')
        self.mark(erin, 'export', 'bad')
        activity = self.fresh(self.q1).last_activity_at
        self.assertEqual(self.count('export'), 2)

        self.login(self.mod)
        response = self.client.post('/tags/csv/merge/', {'into': 'export'})
        self.assertRedirects(response, '/tags/export/', fetch_redirect_response=False)

        # The questions' current tags, without duplicates; deleted ones too.
        self.assertEqual(self.fresh(self.q1).tagnames, 'export collect')
        self.assertEqual(self.fresh(self.q2).tagnames, 'export')
        self.assertEqual(self.fresh(self.q3).tagnames, 'export')
        self.assertEqual(self.fresh(self.q4).tagnames, 'export')
        # History is kept: a new revision, the first one keeps its tags.
        q1 = self.fresh(self.q1)
        self.assertEqual(list(q1.revisions.order_by('revision').values_list('tagnames', flat=True)),
                         ['csv collect', 'export collect'])
        self.assertEqual(q1.active_revision.summary, 'Tag csv merged into export')
        self.assertEqual(q1.last_activity_at, activity)
        self.assertEqual(sorted(q1.tags.values_list('name', flat=True)), ['collect', 'export'])
        # Counts: the deleted question doesn't count.
        self.assertEqual(self.count('export'), 3)
        self.assertEqual(self.count('collect'), 1)
        # No orphans: the old tag is gone, its name and synonyms point to the target.
        self.assertFalse(Tag.objects.filter(name='csv').exists())
        self.assertFalse(Question.objects.filter(tags__name='csv').exists())
        self.assertEqual(dict(TagSynonym.objects.values_list('source_name', 'target__name')),
                         {'csv': 'export', 'comma-separated': 'export'})
        # Followers and ignored marks moved; a member's choice for the target wins.
        self.assertEqual(self.marks(bob), [('export', 'good')])
        self.assertEqual(self.marks(carol), [('export', 'good')])
        self.assertEqual(self.marks(dave), [('export', 'bad')])
        self.assertEqual(self.marks(erin), [('export', 'bad')])
        # The search index has the new tags.
        self.assertEqual(SearchDocument.objects.get(question_id=self.q1.id).tagnames, 'export collect')
        self.assertFalse(SearchDocument.objects.filter(question_id=self.q4.id).exists())
        # Old links redirect; typing the old name gives the target.
        self.assertRedirects(self.client.get('/tags/csv/'), '/tags/export/', status_code=301,
                             fetch_redirect_response=False)
        self.assertEqual(TagSynonym.resolve(['csv', 'comma-separated']), ['export'])
        # Recorded, attributable, not undoable.
        action = Action.objects.get(action_type='mergetag').leaf
        self.assertEqual(action.user, self.mod)
        self.assertEqual(action.extra['questions'], 3)
        self.assertFalse(action.can_undo())
        self.assertIn('merged the tag', action.describe())
        page = self.client.get('/tags/export/edit/')
        self.assertContains(page, 'merged the tag')
        self.assertContains(page, "can't be undone")
        self.assertContains(self.client.get(self.mod.get_profile_url().rstrip('/') + '/recent/'), 'merged the tag')

        # The deleted question, undeleted, counts in the target.
        self.fresh(self.q4).nstate.deleted.cancel(self.mod, '127.0.0.1')
        self.assertEqual(self.count('export'), 4)

    def test_merge_errors(self):
        self.login(self.mod)
        for into, message in (('', 'Enter the name of the tag to merge into.'), ('nothing', 'There is no tag named nothing.'),
                              ('csv', 'A tag cannot be merged into itself.'),
                              ('comma-separated', 'A tag cannot be merged into itself.')):
            with self.subTest(into=into):
                response = self.client.post('/tags/csv/merge/', {'into': into})
                self.assertContains(response, message)
        self.assertTrue(Tag.objects.filter(name='csv').exists())

    def test_merge_into_a_synonym_gives_its_tag(self):
        TagSynonym.objects.create(source_name='kml', target=Tag.objects.get(name='export'))
        self.login(self.mod)
        self.assertContains(self.client.get('/tags/csv/merge/', {'into': 'kml'}), 'Merge csv into export')

    # -- rename --------------------------------------------------------------

    def test_rename_and_undo(self):
        bob = self.create_user('bob')
        self.mark(bob, 'export', 'good')
        TagSynonym.objects.create(source_name='kml', target=Tag.objects.get(name='export'))
        tag_id = Tag.objects.get(name='export').id
        Tag.objects.get(name='export')  # now in the object cache under its name

        self.login(self.mod)
        response = self.client.post('/tags/export/edit/', {'action': 'rename', 'name': 'Data-Export',
                                                            'keep_synonym': '1'})
        self.assertRedirects(response, '/tags/data-export/edit/', fetch_redirect_response=False)
        tag = Tag.objects.get(name='data-export')
        self.assertEqual(tag.id, tag_id)
        self.assertRaises(Tag.DoesNotExist, Tag.objects.get, name='export')
        self.assertEqual(self.fresh(self.q2).tagnames, 'csv data-export')
        self.assertEqual(self.fresh(self.q2).revisions.get(revision=1).tagnames, 'csv export')
        self.assertEqual(self.count('data-export'), 2)
        self.assertEqual(self.marks(bob), [('data-export', 'good')])
        self.assertEqual(dict(TagSynonym.objects.filter(target=tag).values_list('source_name', 'target__name')),
                         {'export': 'data-export', 'kml': 'data-export'})
        self.assertEqual(SearchDocument.objects.get(question_id=self.q3.id).tagnames, 'data-export')
        self.assertRedirects(self.client.get('/tags/export/'), '/tags/data-export/', status_code=301,
                             fetch_redirect_response=False)

        action = Action.objects.get(action_type='renametag').leaf
        self.assertTrue(action.can_undo())
        response = self.client.post('/tags/data-export/edit/', {'action': 'undo', 'id': action.id})
        self.assertRedirects(response, '/tags/export/edit/', fetch_redirect_response=False)
        self.assertEqual(Tag.objects.get(id=tag_id).name, 'export')
        self.assertEqual(self.fresh(self.q2).tagnames, 'csv export')
        self.assertEqual(self.count('export'), 2)
        self.assertFalse(TagSynonym.objects.filter(source_name='export').exists())
        self.assertTrue(TagSynonym.objects.filter(source_name='kml').exists())
        self.assertTrue(Action.objects.get(id=action.id).canceled)
        # Undone once is enough.
        response = self.client.post('/tags/export/edit/', {'action': 'undo', 'id': action.id})
        self.assertContains(response, 'This change can no longer be undone.')

    def test_rename_without_synonym(self):
        self.login(self.mod)
        self.client.post('/tags/export/edit/', {'action': 'rename', 'name': 'exports'})
        self.assertFalse(TagSynonym.objects.filter(source_name='export').exists())
        self.assertEqual(self.client.get('/tags/export/').status_code, 404)
        self.assertEqual(self.client.get('/tags/exports/').status_code, 200)

    def test_rename_to_its_own_synonym(self):
        self.login(self.mod)
        self.client.post('/tags/csv/edit/', {'action': 'rename', 'name': 'comma-separated', 'keep_synonym': '1'})
        self.assertEqual(self.fresh(self.q1).tagnames, 'comma-separated collect')
        self.assertEqual(dict(TagSynonym.objects.values_list('source_name', 'target__name')),
                         {'csv': 'comma-separated'})

    def test_rename_errors(self):
        TagSynonym.objects.create(source_name='kml', target=Tag.objects.get(name='export'))
        self.login(self.mod)
        for name, message in (('export', 'There is already a tag named export'), ('kml', 'kml is a synonym of export.'),
                              ('bad name!', 'Enter a valid tag name.'), ('csv', 'The tag is already called csv.')):
            with self.subTest(name=name):
                response = self.client.post('/tags/csv/edit/', {'action': 'rename', 'name': name})
                self.assertContains(response, message)
                self.assertContains(response, 'class="tag-edit-error"')
        self.assertTrue(Tag.objects.filter(name='csv').exists())

    # -- synonyms and descriptions -------------------------------------------

    def test_synonym_add_remove_and_undo(self):
        self.login(self.mod)
        self.client.post('/tags/export/edit/', {'action': 'add_synonym', 'synonym': 'XLS'})
        self.assertEqual(TagSynonym.objects.get(source_name='xls').target.name, 'export')
        added = Action.objects.get(action_type='addtagsynonym').leaf
        self.assertEqual(added.user, self.mod)
        self.client.post('/tags/export/edit/', {'action': 'undo', 'id': added.id})
        self.assertFalse(TagSynonym.objects.filter(source_name='xls').exists())

        synonym = TagSynonym.objects.get(source_name='comma-separated')
        self.client.post('/tags/csv/edit/', {'action': 'remove_synonym', 'id': synonym.id})
        self.assertFalse(TagSynonym.objects.filter(source_name='comma-separated').exists())
        removed = Action.objects.get(action_type='removetagsynonym').leaf
        self.assertIn('removed the synonym', removed.describe())
        self.client.post('/tags/csv/edit/', {'action': 'undo', 'id': removed.id})
        self.assertEqual(TagSynonym.objects.get(source_name='comma-separated').target.name, 'csv')

    def test_synonyms_page(self):
        self.login(self.mod)
        page = self.client.get('/tag-synonyms/')
        self.assertContains(page, 'comma-separated')
        self.client.post('/tag-synonyms/', {'action': 'add', 'synonym': 'kml', 'tag': 'export'})
        self.assertEqual(TagSynonym.objects.get(source_name='kml').target.name, 'export')
        # The tag may be given by one of its synonyms.
        self.client.post('/tag-synonyms/', {'action': 'add', 'synonym': 'tsv', 'tag': 'comma-separated'})
        self.assertEqual(TagSynonym.objects.get(source_name='tsv').target.name, 'csv')
        # An existing tag as a synonym asks for a merge.
        response = self.client.post('/tag-synonyms/', {'action': 'add', 'synonym': 'collect', 'tag': 'export'})
        self.assertRedirects(response, '/tags/collect/merge/?into=export', fetch_redirect_response=False)
        self.assertContains(self.client.post('/tag-synonyms/', {'action': 'add', 'synonym': 'x1', 'tag': 'nope'}),
                            'There is no tag named nope.')
        self.assertContains(self.client.get('/tag-synonyms/', {'q': 'kml'}), 'kml')
        self.assertNotContains(self.client.get('/tag-synonyms/', {'q': 'kml'}), 'comma-separated')
        synonym = TagSynonym.objects.get(source_name='kml')
        self.client.post('/tag-synonyms/', {'action': 'remove', 'id': synonym.id})
        self.assertFalse(TagSynonym.objects.filter(source_name='kml').exists())
        # Listed with the admin tools.
        self.assertContains(self.client.get('/tags/'), 'href="/tag-synonyms/"')

    def test_description_and_undo(self):
        self.login(self.mod)
        self.client.post('/tags/csv/edit/', {'action': 'description', 'description': 'Comma-separated values.'})
        self.assertEqual(Tag.objects.get(name='csv').description, 'Comma-separated values.')
        action = Action.objects.get(action_type='edittag').leaf
        self.client.post('/tags/csv/edit/', {'action': 'undo', 'id': action.id})
        self.assertEqual(Tag.objects.get(name='csv').description, '')

    def test_tag_page_wiki_and_synonyms(self):
        tag = Tag.objects.get(name='csv')
        tag.description = 'Questions about **CSV files**. ' + 'Comma-separated values exported from Collect. ' * 6
        tag.save()
        page = self.client.get('/tags/csv/')
        self.assertContains(page, 'class="tag-wiki-summary"')
        self.assertContains(page, 'Read the full description')
        self.assertContains(page, '<strong>CSV files</strong>')
        self.assertContains(page, 'Also called:')
        self.assertContains(page, '<span class="tag-synonym">comma-separated</span>')
        self.assertNotContains(page, 'Manage tag')
        self.login(self.mod)
        self.assertContains(self.client.get('/tags/csv/'), 'href="/tags/csv/edit/"')

    # -- permissions ---------------------------------------------------------

    def test_members_are_refused(self):
        for user in (None, self.alice):
            self.client.logout()
            if user:
                self.login(user)
            with self.subTest(user=user):
                self.assertEqual(self.client.get('/tags/csv/edit/').status_code, 404)
                self.assertEqual(self.client.get('/tags/csv/merge/', {'into': 'export'}).status_code, 404)
                self.assertEqual(self.client.post('/tags/csv/merge/', {'into': 'export'}).status_code, 404)
                self.assertEqual(self.client.post('/tags/csv/edit/', {'action': 'rename', 'name': 'x2'}).status_code, 404)
                self.assertEqual(self.client.get('/tag-synonyms/').status_code, 404)
                self.assertEqual(self.client.post('/tag-synonyms/', {'action': 'add', 'synonym': 'x3', 'tag': 'csv'}
                                                  ).status_code, 404)
                self.assertNotContains(self.client.get('/tags/'), '/tag-synonyms/')
        self.assertTrue(Tag.objects.filter(name='csv').exists())
        self.assertFalse(TagSynonym.objects.filter(source_name='x3').exists())
        self.assertFalse(Action.objects.filter(action_type__in=('mergetag', 'renametag', 'addtagsynonym')).exists())

    def test_superusers_may(self):
        admin = self.create_user('admin', is_superuser=True)
        self.login(admin)
        self.assertEqual(self.client.get('/tags/csv/edit/').status_code, 200)

    # -- suggestions ---------------------------------------------------------

    def test_autocomplete_shows_the_tag_of_a_synonym(self):
        csv = Tag.objects.get(name='csv')
        lines = self.client.get('/matching_tags/', {'q': 'comma'}).content.decode().splitlines()
        self.assertEqual(lines, ['%d|csv|%d|comma-separated' % (csv.id, csv.used_count)])
        # Exact synonym first, then matching tags.
        TagSynonym.objects.create(source_name='exp', target=Tag.objects.get(name='collect'))
        lines = self.client.get('/matching_tags/', {'q': 'exp'}).content.decode().splitlines()
        self.assertEqual([line.split('|')[1:2] + line.split('|')[3:] for line in lines],
                         [['collect', 'exp'], ['export']])
        # A synonym of a listed tag is not repeated.
        lines = self.client.get('/matching_tags/', {'q': 'c'}).content.decode().splitlines()
        self.assertEqual(sorted(line.split('|')[1] for line in lines), ['collect', 'csv'])

    def test_search_box_suggests_the_tag_of_a_synonym(self):
        from forum.views.search import suggestions
        tags = suggestions('comma')['tags']
        self.assertEqual([t['name'] for t in tags], ['csv'])
        self.assertIn('also called comma-separated', tags[0]['meta'])
