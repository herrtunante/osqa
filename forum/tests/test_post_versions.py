"""Version-aware questions and answers (forum/utils/post_versions.py): the
tag's version list on its management page (forum/utils/tags.py), versions
chosen on ask/answer/edit, the "may be outdated" notice, "Still works in
<current>" (ConfirmVersionAction) and its undo, the version= search filter,
the JSON API and the Markdown export, query budgets and the switch."""
import datetime
import json

from django.core.cache import cache
from django.http import QueryDict

from forum import settings
from forum.actions import ConfirmVersionAction
from forum.api.schemas import COMPONENTS
from forum.models import Action, Answer, NodeVersion, Question, Tag, TagSynonym, TagVersion
from forum.search.filters import SearchFilters
from forum.utils import post_versions
from forum.utils import tags as tagtools
from forum.utils.tags import TagError

from .base import OSQATestCase
from .test_api import check_schema
from .test_query_budgets import QueryBudgetMixin

BODY = 'A question body that is long enough to be posted here.'
ANSWER = 'An answer with enough characters to pass the checks.'


class VersionsTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.mod = self.create_user('mod', reputation=1, is_staff=True)
        self.alice = self.create_user('alice', reputation=10)          # asks
        self.bob = self.create_user('bob', reputation=10)              # answers, low reputation
        self.carol = self.create_user('carol', reputation=100000)      # may edit others' posts
        self.dave = self.create_user('dave', reputation=10)            # nobody in particular
        self.question = self.post_question('How do I export Collect data to CSV?', 'collect export')
        self.collect = Tag.objects.get(name='collect')
        self.export = Tag.objects.get(name='export')
        self.v326 = tagtools.add_version(self.collect, '3.26', self.mod, '127.0.0.1')
        self.v40 = tagtools.add_version(self.collect, '4.0', self.mod, '127.0.0.1')
        self.v41 = tagtools.add_version(self.collect, '4.1', self.mod, '127.0.0.1', current=True)

    def post_question(self, title, tags, author=None):
        question = Question(author=author or self.alice, title=title, tagnames=tags, body=BODY)
        question.save()
        return question

    def post_answer(self, question=None, author=None, version=None):
        answer = Answer(author=author or self.bob, parent=question or self.question, body=ANSWER)
        answer.save()
        if version is not None:
            NodeVersion.objects.create(node=answer, tag=version.tag, version=version)
        return answer

    def field(self, tag=None):
        return post_versions.field_name((tag or self.collect).id)

    def versions_of(self, node):
        return dict(NodeVersion.objects.filter(node_id=node.id).values_list('tag__name', 'version__name'))

    def reload(self, version):
        return TagVersion.objects.get(id=version.id)


class TagVersionManagementTest(VersionsTestCase):
    def edit(self, data, user=None):
        self.login(user or self.mod)
        return self.client.post('/tags/collect/edit/', data)

    def names(self, tag=None):
        return list(TagVersion.objects.filter(tag=tag or self.collect).order_by('position').values_list(
            'name', flat=True))

    def test_only_moderators(self):
        for user in (self.carol, self.alice):
            self.login(user)
            self.assertEqual(self.client.get('/tags/collect/edit/').status_code, 404)
            self.assertEqual(self.client.post('/tags/collect/edit/', {'action': 'add_version', 'version': '5.0'})
                             .status_code, 404)
        self.assertFalse(TagVersion.objects.filter(name='5.0').exists())

    def test_page_lists_versions(self):
        self.login(self.mod)
        response = self.client.get('/tags/collect/edit/')
        self.assertContains(response, 'id="tag-versions-heading"')
        self.assertContains(response, '<span class="version-chip">4.1</span>', html=False)
        self.assertContains(response, 'current version')
        self.assertContains(response, 'Move 4.1 earlier')

    def test_add_validates(self):
        response = self.edit({'action': 'add_version', 'version': '5.0', 'released': '2026-09-01', 'current': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.names(), ['3.26', '4.0', '4.1', '5.0'])
        new = TagVersion.objects.get(tag=self.collect, name='5.0')
        self.assertTrue(new.is_current)
        self.assertEqual(new.released.isoformat(), '2026-09-01')
        self.assertFalse(self.reload(self.v41).is_current)  # one current version
        for bad in ('', 'a b', '<b>', 'x' * 21, '4.0'):
            response = self.edit({'action': 'add_version', 'version': bad})
            self.assertEqual(response.status_code, 200, bad)
            self.assertContains(response, 'role="alert"')
        self.assertEqual(self.names(), ['3.26', '4.0', '4.1', '5.0'])
        with self.assertRaises(TagError):
            tagtools.add_version(self.collect, '4.1', self.mod, '')  # the same name, any case
        self.post_answer(version=self.v326)
        with self.assertRaises(TagError):
            tagtools.remove_version(self.v326, self.mod, '')  # a post applies to it

    def test_current_move_remove(self):
        self.edit({'action': 'current_version', 'id': self.v40.id})
        self.assertEqual(list(TagVersion.objects.filter(is_current=True).values_list('name', flat=True)), ['4.0'])
        self.edit({'action': 'no_current_version', 'id': self.v40.id})
        self.assertFalse(TagVersion.objects.filter(is_current=True).exists())
        self.edit({'action': 'version_up', 'id': self.v41.id})
        self.assertEqual(self.names(), ['3.26', '4.1', '4.0'])
        self.edit({'action': 'version_down', 'id': self.v326.id})
        self.assertEqual(self.names(), ['4.1', '3.26', '4.0'])
        self.edit({'action': 'version_up', 'id': self.v41.id})  # already first: nothing moves
        self.assertEqual(self.names(), ['4.1', '3.26', '4.0'])
        self.edit({'action': 'remove_version', 'id': self.v40.id})
        self.assertEqual(self.names(), ['4.1', '3.26'])
        self.assertEqual(self.edit({'action': 'remove_version', 'id': 'x'}).status_code, 200)

    def test_used_version_cannot_be_removed(self):
        self.post_answer(version=self.v326)
        response = self.edit({'action': 'remove_version', 'id': self.v326.id})
        self.assertContains(response, "1 post applies to 3.26, so it can&#x27;t be removed.")
        self.assertTrue(TagVersion.objects.filter(id=self.v326.id).exists())

    def test_changes_are_in_the_history(self):
        self.login(self.mod)
        response = self.client.get('/tags/collect/edit/')
        self.assertContains(response, 'added the version <span class="version-chip">4.1</span> to the tag')
        self.assertEqual(Action.objects.filter(action_type='tagversion').count(), 3)

    def test_rename_keeps_versions(self):
        answer = self.post_answer(version=self.v326)
        tagtools.rename_tag(self.collect, 'collect-desktop', self.mod, '127.0.0.1')
        self.assertEqual(self.names(), ['3.26', '4.0', '4.1'])
        self.assertEqual(self.versions_of(answer), {'collect-desktop': '3.26'})
        catalog = post_versions.catalog()
        self.assertEqual(catalog.tag_ids_for(['collect-desktop', 'collect']), [self.collect.id])  # old name: synonym
        self.assertEqual(post_versions.of_post(Answer.objects.get(id=answer.id))[0].version.label,
                         'collect-desktop 3.26')

    def test_merge_moves_versions(self):
        mobile = self.post_question('Collect Mobile crashes on start', 'collect-mobile').tags.get()
        m40 = tagtools.add_version(mobile, '4.0', self.mod, '')
        m50 = tagtools.add_version(mobile, '5.0', self.mod, '', current=True)
        tagtools.add_version(mobile, '3.0', self.mod, '')
        one = self.post_answer(version=m40)
        two = self.post_answer(version=m50)
        # Tagged with both: keeps its version of the target.
        both = self.post_question('Sync between both apps', 'collect collect-mobile')
        NodeVersion.objects.create(node=both, tag=mobile, version=m50)
        NodeVersion.objects.create(node=both, tag=self.collect, version=self.v40)
        NodeVersion.objects.filter(node=one).update(confirmed_version=m50)

        tagtools.merge_tags(mobile, self.collect, self.mod, '127.0.0.1')

        self.assertEqual(self.names(), ['3.26', '4.0', '4.1', '5.0', '3.0'])
        self.assertEqual(list(TagVersion.objects.filter(tag=self.collect, is_current=True).values_list(
            'name', flat=True)), ['4.1'])  # the target's current version stays current
        self.assertEqual(self.versions_of(one), {'collect': '4.0'})
        self.assertEqual(NodeVersion.objects.get(node=one).version_id, self.v40.id)  # same name: the target's
        self.assertEqual(NodeVersion.objects.get(node=one).confirmed_version.name, '5.0')
        self.assertEqual(self.versions_of(two), {'collect': '5.0'})
        self.assertEqual(self.versions_of(both), {'collect': '4.0'})
        self.assertFalse(TagVersion.objects.filter(tag_id=mobile.id).exists())
        self.assertEqual(Action.objects.get(action_type='mergetag').extra['versions'], 3)


class SavingVersionsTest(VersionsTestCase):
    def test_ask_with_a_version(self):
        self.login(self.alice)
        page = self.client.get('/questions/ask/')
        self.assertContains(page, 'name="%s"' % self.field())
        self.assertContains(page, 'data-follow-tags')
        self.assertContains(page, '4.1 (current)')
        self.assertContains(page, 'osqa.versions.js')
        response = self.client.post('/questions/ask/', {
            'title': 'Which Collect version reads CSV dates?', 'text': BODY, 'tags': 'collect csv',
            self.field(): str(self.v40.id), self.field(self.export): str(self.v41.id)})
        self.assertEqual(response.status_code, 302)
        question = Question.objects.get(title='Which Collect version reads CSV dates?')
        self.assertEqual(self.versions_of(question), {'collect': '4.0'})

    def test_bad_values_are_ignored(self):
        self.login(self.alice)
        mobile = self.post_question('Mobile', 'mobile').tags.get()
        other = tagtools.add_version(mobile, '1.0', self.mod, '')
        self.client.post('/questions/ask/', {'title': 'A question with a wrong version', 'text': BODY,
                                             'tags': 'collect', self.field(): str(other.id)})
        self.assertEqual(self.versions_of(Question.objects.get(title='A question with a wrong version')), {})

    def test_answer_and_edit(self):
        self.login(self.bob)
        page = self.client.get(self.question.get_absolute_url())
        self.assertContains(page, 'id="compose-versions"')
        self.assertNotContains(page, 'data-follow-tags')
        self.client.post('/questions/%d/answer/' % self.question.id, {'text': ANSWER, self.field(): str(self.v326.id)})
        answer = Answer.objects.get(parent=self.question)
        self.assertEqual(self.versions_of(answer), {'collect': '3.26'})

        # Only the version changes: saved without a new revision.
        edit = self.client.get('/answers/%d/edit/' % answer.id)
        self.assertContains(edit, '<option value="%d" selected>3.26</option>' % self.v326.id)
        response = self.client.post('/answers/%d/edit/' % answer.id, {'text': ANSWER, self.field(): str(self.v41.id)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.versions_of(answer), {'collect': '4.1'})
        self.assertEqual(answer.revisions.count(), 1)
        self.client.post('/answers/%d/edit/' % answer.id, {'text': ANSWER, self.field(): ''})
        self.assertEqual(self.versions_of(answer), {})

    def test_edit_question_drops_versions_of_removed_tags(self):
        NodeVersion.objects.create(node=self.question, tag=self.collect, version=self.v40)
        self.login(self.alice)
        page = self.client.get('/questions/%d/edit/' % self.question.id)
        self.assertContains(page, '<option value="%d" selected>4.0</option>' % self.v40.id)
        response = self.client.post('/questions/%d/edit/' % self.question.id, {
            'title': self.question.title, 'text': BODY, 'tags': 'export', 'summary': 'retag'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.versions_of(self.question), {})

    def test_changing_the_version_drops_an_older_confirmation(self):
        answer = self.post_answer(version=self.v326)
        NodeVersion.objects.filter(node=answer).update(confirmed_version=self.v40, confirmed_by=self.carol)
        post_versions.save(answer, {self.field(): str(self.v41.id)}, ['collect'])
        self.assertIsNone(NodeVersion.objects.get(node=answer).confirmed_version_id)

    def test_suggested_edits_show_no_versions(self):
        answer = self.post_answer(version=self.v326)
        self.login(self.dave)
        page = self.client.get('/posts/%d/suggest-edit/' % answer.id)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'compose-versions')


class OutdatedNoticeTest(VersionsTestCase):
    def test_logic(self):
        catalog = post_versions.catalog()
        v326, v40, v41 = (catalog.by_id[v.id] for v in (self.v326, self.v40, self.v41))
        pv = post_versions.PostVersion(1, v326, v41)
        self.assertTrue(pv.outdated)
        self.assertFalse(post_versions.PostVersion(1, v41, v41).outdated)
        self.assertFalse(post_versions.PostVersion(1, v326, None).outdated)  # no current version
        confirmed = post_versions.PostVersion(1, v326, v41, confirmed=v41)
        self.assertFalse(confirmed.outdated)
        self.assertTrue(confirmed.confirmed_current)
        self.assertTrue(post_versions.PostVersion(1, v326, v41, confirmed=v40).outdated)
        # Order is the moderators' order, not the label's.
        tagtools.move_version(self.v41, -1, self.mod, '')
        tagtools.move_version(self.v41, -1, self.mod, '')
        catalog = post_versions.catalog()
        self.assertFalse(post_versions.PostVersion(1, catalog.by_id[self.v326.id], catalog.by_id[self.v41.id]).outdated)

    def test_question_page(self):
        NodeVersion.objects.create(node=self.question, tag=self.collect, version=self.v41)
        old = self.post_answer(version=self.v326)
        new = self.post_answer(author=self.dave, version=self.v41)
        page = self.client.get(self.question.get_absolute_url()).content.decode()
        self.assertIn('<span class="version-chip">collect 4.1</span>', page)
        self.assertIn('<span class="version-chip">collect 3.26</span>', page)
        self.assertEqual(page.count('class="version-notice'), 1)
        self.assertIn('Written for collect 3.26 — the current version is 4.1. It may be outdated.', page)
        self.assertNotIn('Still works in', page)  # anonymous
        notice_at = page.index('class="version-notice')
        self.assertTrue(page.index('answer-container-%d' % old.id) < notice_at < page.index(
            'answer-container-%d' % new.id) or page.index('answer-container-%d' % new.id) < page.index(
            'answer-container-%d' % old.id) < notice_at)

    def test_who_sees_the_button(self):
        self.post_answer(version=self.v326)
        for user, shown in ((self.bob, True), (self.carol, True), (self.dave, False), (self.alice, False)):
            self.login(user)
            page = self.client.get(self.question.get_absolute_url())
            (self.assertContains if shown else self.assertNotContains)(page, 'Still works in 4.1')

    def test_list_rows_show_chips(self):
        NodeVersion.objects.create(node=self.question, tag=self.collect, version=self.v40)
        for url in ('/questions/', '/tags/collect/', '/search/?q=export'):
            self.assertContains(self.client.get(url), '<span class="version-chip">collect 4.0</span>', msg_prefix=url)


class ConfirmVersionTest(VersionsTestCase):
    def setUp(self):
        super().setUp()
        self.answer = self.post_answer(version=self.v326)
        self.url = '/posts/%d/confirm-version/' % self.answer.id

    def confirm(self, user, **data):
        self.login(user)
        return self.client.post(self.url, dict({'tag': self.collect.id}, **data))

    def row(self):
        return NodeVersion.objects.get(node=self.answer)

    def test_get_and_anonymous(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        response = self.client.post(self.url, {'tag': self.collect.id})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.row().confirmed_version_id)

    def test_permissions(self):
        self.assertEqual(self.confirm(self.dave).status_code, 403)
        self.assertEqual(self.confirm(self.alice).status_code, 403)  # the asker can't edit the answer
        self.assertEqual(self.confirm(self.carol, tag=self.export.id).status_code, 404)
        self.assertIsNone(self.row().confirmed_version_id)

    def test_confirm_and_undo(self):
        response = self.confirm(self.carol)
        self.assertRedirects(response, self.answer.get_absolute_url(), fetch_redirect_response=False)
        row = self.row()
        self.assertEqual((row.confirmed_version_id, row.confirmed_by_id), (self.v41.id, self.carol.id))
        action = Action.objects.get(action_type='confirmversion')
        self.assertIsInstance(action.leaf, ConfirmVersionAction)
        self.assertEqual(action.node_id, self.answer.id)
        self.assertEqual(self.answer.revisions.count(), 1)  # the text isn't edited
        self.assertIn('still works in collect 4.1', action.leaf.describe())

        page = self.client.get(self.question.get_absolute_url()).content.decode()
        self.assertNotIn('class="version-notice', page)
        self.assertIn('Confirmed for collect 4.1 by <a href="%s">carol</a> on' % self.carol.get_profile_url(), page)
        self.assertIn('version-undo-button', page)
        self.assertEqual(self.confirm(self.carol).status_code, 403)  # nothing left to confirm

        self.assertEqual(self.confirm(self.bob, undo='1').status_code, 403)  # only who confirmed, or a moderator
        self.confirm(self.carol, undo='1')
        self.assertIsNone(self.row().confirmed_version_id)
        self.assertTrue(Action.objects.get(id=action.id).canceled)
        self.assertContains(self.client.get(self.question.get_absolute_url()), 'Still works in 4.1')

    def test_undo_brings_back_the_earlier_confirmation(self):
        tagtools.set_current_version(self.v40, self.mod, '')
        self.confirm(self.bob)  # the author confirms 4.0
        tagtools.set_current_version(self.v41, self.mod, '')
        self.confirm(self.carol)
        self.assertEqual(self.row().confirmed_version_id, self.v41.id)
        self.confirm(self.mod, undo='1')  # a moderator takes carol's back
        row = self.row()
        self.assertEqual((row.confirmed_version_id, row.confirmed_by_id), (self.v40.id, self.bob.id))

    def test_feature_off(self):
        settings.POST_VERSIONS.set_value(False)
        self.assertEqual(self.confirm(self.carol).status_code, 404)


class SearchFilterTest(VersionsTestCase):
    def setUp(self):
        super().setUp()
        self.answered = self.post_question('Old CSV export question', 'collect csv')
        self.post_answer(self.answered, version=self.v326)
        self.asked = self.post_question('New question about maps', 'collect maps')
        NodeVersion.objects.create(node=self.asked, tag=self.collect, version=self.v41)
        self.confirmed = self.post_question('Confirmed question', 'collect')
        answer = self.post_answer(self.confirmed, version=self.v40)
        NodeVersion.objects.filter(node=answer).update(confirmed_version=self.v41)

    def ids(self, **params):
        filters = SearchFilters.from_params(QueryDict('', mutable=True) | params)
        return set(filters.apply(Question.objects.public()).values_list('id', flat=True))

    def test_params(self):
        filters = SearchFilters.from_params(QueryDict('version=+4.1+&tags=collect'))
        self.assertEqual(filters.version, '4.1')
        self.assertEqual(filters.params(), [('tags', 'collect'), ('version', '4.1')])
        self.assertEqual(filters.without('version').params(), [('tags', 'collect')])
        self.assertEqual(filters.active_count, 2)

    def test_matches_question_or_answers(self):
        self.assertEqual(self.ids(version='4.1'), {self.asked.id, self.confirmed.id})
        self.assertEqual(self.ids(version='3.26'), {self.answered.id})
        self.assertEqual(self.ids(version='3.26', tags='maps'), set())
        self.assertEqual(self.ids(version='9.9'), set())

    def test_held_answers_dont_count(self):
        from forum.utils import review
        answer = Answer.objects.get(parent=self.answered)
        Answer.objects.filter(id=answer.id).update(state_string=review.state_mark(review.HELD))
        cache.clear()
        self.assertEqual(self.ids(version='3.26'), set())

    def test_search_page(self):
        page = self.client.get('/search/', {'q': 'question', 'version': '4.1'})
        self.assertContains(page, 'id="search-filter-version"')
        self.assertContains(page, 'Version 4.1')
        self.assertContains(page, 'Confirmed <mark>question</mark>')
        self.assertContains(page, 'New <mark>question</mark> about maps')
        self.assertNotContains(page, 'Old CSV export')


class ApiAndExportTest(VersionsTestCase):
    def setUp(self):
        super().setUp()
        NodeVersion.objects.create(node=self.question, tag=self.collect, version=self.v41)
        self.answer = self.post_answer(version=self.v326)
        NodeVersion.objects.filter(node=self.answer).update(confirmed_version=self.v40, confirmed_by=self.carol,
                                                            confirmed_at=datetime.datetime(2026, 9, 1, 12, 0))

    def test_api(self):
        data = json.loads(self.client.get('/api/v1/questions/%d' % self.question.id).content)
        check_schema(data, COMPONENTS['Question'])
        self.assertEqual(data['versions'], [{'tag': 'collect', 'version': '4.1', 'current_version': '4.1',
                                             'outdated': False, 'confirmed_for': None}])
        version = data['answers'][0]['versions'][0]
        self.assertEqual((version['version'], version['outdated'], version['confirmed_for']['version'],
                          version['confirmed_for']['by']['username']), ('3.26', True, '4.0', 'carol'))
        answer = json.loads(self.client.get('/api/v1/answers/%d' % self.answer.id).content)
        check_schema(answer, COMPONENTS['Answer'])
        self.assertEqual(answer['versions'], data['answers'][0]['versions'])
        listed = json.loads(self.client.get('/api/v1/questions/%d/answers' % self.question.id).content)
        self.assertEqual(listed['data'][0]['versions'], data['answers'][0]['versions'])
        spec = json.loads(self.client.get('/api/v1/openapi.json').content)
        self.assertIn('PostVersion', spec['components']['schemas'])

    def test_markdown_export(self):
        text = self.client.get('/questions/%d.md' % self.question.id).content.decode()
        self.assertIn('Applies to: collect 4.1\n', text)
        self.assertIn('Applies to: collect 3.26, confirmed for 4.0 on 2026-09-01 (', text)
        self.assertIn('(the current version is 4.1; this may be outdated)', text)

    def test_mcp_and_api_search_take_a_version(self):
        from forum.views.agents import MCP_TOOLS
        self.assertIn('version', MCP_TOOLS[0]['inputSchema']['properties'])
        spec = json.loads(self.client.get('/api/v1/openapi.json').content)
        from forum.api.openapi import openapi_path
        params = [p['name'] for p in spec['paths'][openapi_path('/search')]['get']['parameters']]
        self.assertIn('version', params)


class FeatureSwitchTest(VersionsTestCase):
    def test_off_shows_nothing(self):
        NodeVersion.objects.create(node=self.question, tag=self.collect, version=self.v41)
        self.post_answer(version=self.v326)
        settings.POST_VERSIONS.set_value(False)
        self.login(self.carol)
        page = self.client.get(self.question.get_absolute_url())
        self.assertNotContains(page, 'version-chip')
        self.assertNotContains(page, 'compose-versions')
        self.assertNotContains(self.client.get('/questions/ask/'), 'compose-versions')
        self.assertNotContains(self.client.get('/questions/'), 'version-chip')
        data = json.loads(self.client.get('/api/v1/questions/%d' % self.question.id).content)
        self.assertEqual((data['versions'], data['answers'][0]['versions']), ([], []))
        self.assertNotIn('Applies to', self.client.get('/questions/%d.md' % self.question.id).content.decode())
        self.login(self.mod)
        self.assertNotContains(self.client.get('/tags/collect/edit/'), 'tag-versions-heading')

    def test_no_versions_no_queries(self):
        TagVersion.objects.all().delete()
        post_versions.invalidate()
        post_versions.catalog()  # the empty catalogue is cached
        post_versions.enabled()  # and the setting read
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(post_versions.for_thread(self.question), {})
            self.assertEqual(post_versions.form_groups(['collect'], show_all=True), [])
            self.assertEqual(post_versions.for_nodes([self.question.id]), {})
        self.assertEqual(len(ctx.captured_queries), 0)


class VersionQueryBudgetTest(QueryBudgetMixin, VersionsTestCase):
    """With versions on every post, the question page and lists load them
    with one query per page (and the catalogue once from the cache)."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        from forum.settings import base as settings_base
        patcher = mock.patch.object(settings_base, 'TMP_MINICACHE_SECONDS', 3600)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.small = self.post_question('A small question about versions', 'collect')
        self.post_answer(self.small, version=self.v326)
        users = [self.create_user('user%d' % i, reputation=100 + i) for i in range(6)]
        for i, user in enumerate(users):
            answer = self.post_answer(author=user, version=(self.v326, self.v40, self.v41)[i % 3])
            if i % 2:
                NodeVersion.objects.filter(node=answer).update(confirmed_version=self.v41, confirmed_by=users[0])
        NodeVersion.objects.create(node=self.question, tag=self.collect, version=self.v41)
        TagSynonym.objects.create(source_name='opencollect', target=self.collect)

    def test_question_page_does_not_grow_with_versioned_answers(self):
        counts = []
        for question in (self.small, self.question):
            self.client.get(question.get_absolute_url())
            cache.clear()
            counts.append(self.count_queries('get', question.get_absolute_url())[0])
        # 5 more answers by 6 more authors (a confirmer among them): per author at
        # most the accept rate and the user row, nothing per version.
        self.assertLessEqual(counts[1] - counts[0], 13, counts)

    def test_budgets(self):
        # The question page budgets of test_query_budgets plus the versions:
        # the catalogue (cold only: TagVersion and synonyms) and one NodeVersion query.
        url = self.question.get_absolute_url()
        self.assertQueryBudget(url, cold=40, warm=15)
        self.assertQueryBudget('/questions/', cold=17, warm=9)
        self.assertQueryBudget('/questions/%d.md' % self.question.id, cold=14, warm=9)
