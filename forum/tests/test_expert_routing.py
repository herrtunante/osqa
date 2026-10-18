"""Expert routing (forum/utils/expertise.py, forum/expert_routing.py,
forum/views/for_you.py): expertise per member and tag, who is asked to
answer a new question (and who never is), held first posts routed only once
approved, the notification, email and summary line, "Not for me", the
preference, and the "Questions for you" page."""
import datetime
from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from forum import settings
from forum.actions import ApprovePostAction
from forum.models import (Answer, AnswerRequest, Node, Notification, PendingEmail, Question, Tag, TagExpertise,
                          User)
from forum.utils import email_frequency, expertise, push, unsubscribe
from forum.utils.display import update_preferences

from .base import OSQATestCase
from .test_query_budgets import QueryBudgetMixin


class RoutingTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', email_isvalid=True, reputation=100)
        self.experts = [self.create_user(name, email_isvalid=True, reputation=200)
                        for name in ('bob', 'carol', 'dave', 'erin', 'frank', 'gina', 'hugo')]
        self.bob, self.carol, self.dave = self.experts[:3]
        self.set(settings.EXPERT_ROUTING_MIN_SCORE, 5)
        self.set(settings.EXPERT_ROUTING_MAX_EXPERTS, 5)
        self.set(settings.EXPERT_ROUTING_DAILY_CAP, 3)

    def set(self, setting, value):
        setting.set_value(value)
        self.addCleanup(setattr, setting, '_temp', None)

    def tag(self, name):
        return Tag.objects.get_or_create(name=name, defaults={'created_by': self.alice})[0]

    def expertise(self, user, tag, score, answers=4, accepted=1):
        return TagExpertise.objects.create(user=user, tag=self.tag(tag), score=score, answers=answers,
                                           accepted=accepted)

    def ask(self, user=None, title='How do I export survey data to CSV files?', tags='collect'):
        self.login(user or self.alice)
        response = self.client.post(reverse('ask'), {
            'title': title, 'text': 'Question body that is long enough to be posted.', 'tags': tags})
        self.assertEqual(response.status_code, 302)
        return Question.objects.get(title=title)

    def requested(self, question):
        return set(AnswerRequest.objects.filter(question_id=question.id).values_list('user__username', flat=True))


class ExpertiseTest(RoutingTestCase):
    def question(self, author, tags='collect', days_ago=0, **state):
        question = Question(author=author, title='Question %s %s' % (tags, Question.objects.count()),
                            tagnames=tags, body='A question body that is long enough.', **state)
        question.save()
        return question

    def answer(self, author, question, score=0, marked=False, days_ago=0, state_string=''):
        answer = Answer(author=author, parent=question, body='An answer body long enough.', score=score,
                        marked=marked, state_string=state_string,
                        added_at=datetime.datetime.now() - datetime.timedelta(days=days_ago))
        answer.save()
        return answer

    def test_formula(self):
        now = datetime.datetime.now()
        self.assertEqual(expertise.answer_points(0, False, now, now), 1)
        self.assertEqual(expertise.answer_points(4, False, now, now), 3)
        self.assertEqual(expertise.answer_points(4, True, now, now), 6)
        self.assertEqual(expertise.answer_points(50, False, now, now), 6)       # votes stop counting at 10
        self.assertEqual(expertise.answer_points(-5, False, now, now), 0)       # downvoted: nothing
        self.assertEqual(expertise.answer_points(-5, True, now, now), 3)
        old = now - datetime.timedelta(days=400)
        self.assertEqual(expertise.answer_points(4, True, old, now), 1.5)        # a quarter after a year

    def test_refresh_counts_visible_answers_on_others_questions(self):
        q1 = self.question(self.alice, 'collect export')
        q2 = self.question(self.alice, 'collect')
        self.answer(self.bob, q1, score=2, marked=True)       # 1 + 1 + 3 = 5 in collect and export
        self.answer(self.bob, q2, score=0, days_ago=500)      # 0.25 in collect
        self.answer(self.bob, q2, state_string='(deleted)')   # deleted: no
        self.answer(self.carol, q2, state_string='(pending_review)')  # held: no
        own = self.question(self.bob, 'collect')
        self.answer(self.bob, own, score=5, marked=True)     # own question: no
        deleted_question = self.question(self.alice, 'collect', state_string='(deleted)')
        self.answer(self.carol, deleted_question)            # under a deleted question: no
        User.objects.filter(id=self.dave.id).update(is_active=False)
        self.answer(User.objects.get(id=self.dave.id), q1, score=3)  # inactive member: no

        out = StringIO()
        call_command('refresh_tag_expertise', stdout=out)
        self.assertIn('2 expertise row(s)', out.getvalue())
        rows = {(r.user.username, r.tag.name): (r.score, r.answers, r.accepted)
                for r in TagExpertise.objects.select_related('user', 'tag')}
        self.assertEqual(rows, {('bob', 'collect'): (5.25, 2, 1), ('bob', 'export'): (5.0, 1, 1)})

    def test_accepting_an_answer_updates_the_answerer(self):
        question = self.ask(tags='collect export')
        self.login(self.bob)
        self.client.post(reverse('answer', args=[question.id]), {'text': 'An answer that is long enough to post.'})
        answer = Answer.objects.get(author=self.bob)
        self.assertFalse(TagExpertise.objects.exists())
        self.login(self.alice)
        self.assertTrue(self.command(reverse('accept_answer', args=[answer.id]))['success'])
        rows = {r.tag.name: (r.score, r.answers, r.accepted) for r in TagExpertise.objects.filter(user=self.bob)}
        self.assertEqual(rows, {'collect': (4.0, 1, 1), 'export': (4.0, 1, 1)})
        # The asker's own answers never count.
        self.assertFalse(TagExpertise.objects.filter(user=self.alice).exists())


class RoutingTest(RoutingTestCase):
    def test_top_experts_are_asked_once(self):
        for user, score in zip(self.experts, (30, 25, 20, 15, 10, 8, 6)):
            self.expertise(user, 'collect', score)
        self.set(settings.EXPERT_ROUTING_MAX_EXPERTS, 3)
        question = self.ask()
        self.assertEqual(self.requested(question), {'bob', 'carol', 'dave'})
        request = AnswerRequest.objects.get(question_id=question.id, user=self.bob)
        self.assertEqual((request.tag_name, request.tag_answers, request.state, request.routed),
                         ('collect', 4, AnswerRequest.PENDING, True))
        notes = Notification.objects.filter(kind=Notification.ANSWER_REQUEST)
        self.assertEqual(set(notes.values_list('user__username', flat=True)), {'bob', 'carol', 'dave'})
        self.assertEqual(notes.get(user=self.bob).request_reason, {'answers': 4, 'tag': 'collect'})
        self.assertEqual(notes.get(user=self.bob).actor, self.alice)

        # Routing again (e.g. a replayed hook) asks nobody else, and nobody twice.
        self.assertEqual(expertise.route_question(question), [])
        self.assertEqual(Notification.objects.filter(kind=Notification.ANSWER_REQUEST).count(), 3)

    def test_scores_add_up_across_the_question_tags(self):
        self.expertise(self.bob, 'collect', 10)
        self.expertise(self.carol, 'collect', 8)
        self.expertise(self.carol, 'export', 6)
        self.set(settings.EXPERT_ROUTING_MAX_EXPERTS, 1)
        question = self.ask(tags='collect export')
        self.assertEqual(self.requested(question), {'carol'})
        self.assertEqual(AnswerRequest.objects.get(user=self.carol).tag_name, 'collect')  # her best tag

    def test_who_is_never_asked(self):
        bob, carol, dave, erin, frank, gina, hugo = self.experts
        for user in self.experts + [self.alice]:
            self.expertise(user, 'collect', 20)
        self.expertise(hugo, 'export', 3)
        User.objects.filter(id=carol.id).update(is_active=False)        # inactive
        update_preferences(dave, answer_requests=False)                 # turned it off
        TagExpertise.objects.filter(user=erin).update(score=4)          # below the minimum
        # frank was asked three times today already (the cap); gina only declined one herself.
        for n in range(3):
            other = Question(author=self.alice, title='Other %d' % n, tagnames='x', body='Body long enough.')
            other.save()
            AnswerRequest.objects.create(question=other, user=frank)
            AnswerRequest.objects.create(question=other, user=gina, routed=False, state=AnswerRequest.DECLINED)
        question = self.ask(tags='collect export')
        # Not the asker either; hugo's 3 in export adds to his 20 in collect.
        self.assertEqual(self.requested(question), {'bob', 'gina', 'hugo'})

    def test_the_cap_only_counts_the_last_24_hours(self):
        self.expertise(self.bob, 'collect', 20)
        self.set(settings.EXPERT_ROUTING_DAILY_CAP, 1)
        old = Question(author=self.alice, title='Old one', tagnames='x', body='Body long enough.')
        old.save()
        AnswerRequest.objects.create(question=old, user=self.bob,
                                     created=datetime.datetime.now() - datetime.timedelta(hours=25))
        first = self.ask(title='First question about the CSV export')
        second = self.ask(title='Second question about the CSV export')
        self.assertEqual(self.requested(first), {'bob'})
        self.assertEqual(self.requested(second), set())

    def test_people_who_answered_are_not_asked_and_answering_counts(self):
        self.expertise(self.bob, 'collect', 20)
        self.expertise(self.carol, 'collect', 20)
        question = self.ask()
        self.login(self.bob)
        self.client.post(reverse('answer', args=[question.id]), {'text': 'An answer that is long enough to post.'})
        self.assertEqual(AnswerRequest.objects.get(user=self.bob).state, AnswerRequest.ANSWERED)
        self.assertEqual(AnswerRequest.objects.get(user=self.carol).state, AnswerRequest.PENDING)

    def test_switched_off(self):
        self.expertise(self.bob, 'collect', 20)
        self.set(settings.EXPERT_ROUTING_ENABLED, False)
        self.assertEqual(self.requested(self.ask()), set())

    def test_held_question_is_routed_when_approved(self):
        self.expertise(self.bob, 'collect', 20)
        self.set(settings.FIRST_POSTS_MODERATION, 'before')
        newbie = self.create_user('newbie', email_isvalid=True, reputation=60)
        question = self.ask(newbie)
        self.assertTrue(question.nis.pending_review)
        self.assertEqual(self.requested(question), set())
        self.assertFalse(Notification.objects.filter(kind=Notification.ANSWER_REQUEST).exists())
        mod = self.create_user('mod', is_staff=True, email_isvalid=True, reputation=10)
        ApprovePostAction(user=mod, node=Node.objects.get(id=question.id), ip='127.0.0.1').save()
        self.assertEqual(self.requested(question), {'bob'})
        self.assertTrue(Notification.objects.filter(kind=Notification.ANSWER_REQUEST, user=self.bob).exists())


class TellingTheExpertTest(RoutingTestCase):
    def prefs(self, user, **values):
        prefs = User.objects.get(id=user.id).subscription_settings
        for name, value in values.items():
            setattr(prefs, name, value)
        prefs.save()

    def test_email_now_or_in_the_summary(self):
        self.expertise(self.bob, 'collect', 20, answers=7)
        self.expertise(self.carol, 'collect', 15)
        self.expertise(self.dave, 'collect', 10)
        self.prefs(self.carol, email_frequency='d')
        self.prefs(self.dave, enable_notifications=False)
        self.sent_mail = []
        self.ask(title='How do I merge repeat groups into one CSV?')

        mails = [m for m in self.sent_mail if 'You might know the answer' in m[1]]
        self.assertEqual([m[0].username for m in mails], ['bob'])
        recipient, subject, html, text = mails[0][:4]
        self.assertIn('How do I merge repeat groups into one CSV?', subject)
        self.assertIn("you've answered 7 questions tagged collect", text)
        self.assertIn(reverse('questions_for_you'), text)
        self.assertIn('/unsubscribe/', html)
        self.assertEqual(list(PendingEmail.objects.values_list('user__username', 'kind')),
                         [('carol', 'answer_request')])

        self.sent_mail = []
        self.assertEqual(email_frequency.send_summaries()['daily'], 1)
        summary = [m for m in self.sent_mail if m[0].username == 'carol'][0]
        self.assertIn('Questions you might know the answer to (1)', summary[3])
        self.assertIn("Asked by alice, tagged collect: you've answered questions like it.", summary[3])

    def test_notification_list_and_push(self):
        self.expertise(self.bob, 'collect', 20, answers=1)
        question = self.ask()
        note = Notification.objects.get(user=self.bob)
        self.login(self.bob)
        page = self.client.get(reverse('notifications'))
        self.assertContains(page, "You might know the answer: <em>%s</em> (you've answered 1 question tagged "
                                  "collect)" % question.title, html=False)
        self.assertEqual(push.kind_of(note), 'answer_request')
        self.assertIn('answer_request', push.DEFAULT_KINDS)
        self.assertEqual(push.message(note)['title'], 'You might know the answer')
        self.assertEqual(push.message(note)['body'], question.title)

    def test_unsubscribe_link_turns_suggestions_off(self):
        self.assertTrue(expertise.requests_enabled(self.bob))
        unsubscribe.apply(self.bob, 'answer_requests')
        self.assertFalse(expertise.requests_enabled(User.objects.get(id=self.bob.id)))
        self.assertEqual(expertise.opted_out([self.bob.id, self.carol.id]), {self.bob.id})
        unsubscribe.undo(User.objects.get(id=self.bob.id), 'answer_requests')
        self.assertTrue(expertise.requests_enabled(User.objects.get(id=self.bob.id)))


class MemberControlTest(RoutingTestCase):
    def test_preference_on_the_preferences_page(self):
        url = reverse('user_preferences', kwargs={'id': self.bob.id, 'slug': 'bob'})
        self.login(self.bob)
        page = self.client.get(url)
        self.assertContains(page, 'Suggest questions I could answer')
        self.assertContains(page, 'name="answer_requests" id="id_answer_requests" checked')
        self.client.post(url, {'keyboard_shortcuts': 'on'})
        bob = User.objects.get(id=self.bob.id)
        self.assertFalse(expertise.requests_enabled(bob))
        self.assertIs(bob.prop.preferences['keyboard_shortcuts'], True)
        self.expertise(self.bob, 'collect', 20)
        self.assertEqual(self.requested(self.ask()), set())
        self.login(self.bob)
        self.assertContains(self.client.get(reverse('questions_for_you')), 'You turned suggestions off')

    def test_not_for_me(self):
        self.expertise(self.bob, 'collect', 20)
        question = self.ask()
        url = reverse('answer_request_decline', args=[question.id])
        self.login(self.bob)
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {'next': 'https://evil.example/'})
        self.assertRedirects(response, reverse('questions_for_you'), fetch_redirect_response=False)
        request = AnswerRequest.objects.get(user=self.bob)
        self.assertEqual(request.state, AnswerRequest.DECLINED)
        self.assertIsNotNone(Notification.objects.get(user=self.bob).read_at)
        self.assertNotContains(self.client.get(reverse('questions_for_you')), question.title)
        self.assertEqual(expertise.route_question(question), [])
        # Twice is harmless; a question that was only listed gets a declined row that isn't routed.
        self.client.post(url)
        other = self.ask(title='Another question about the CSV export')
        AnswerRequest.objects.filter(question_id=other.id).delete()
        self.login(self.bob)
        self.client.post(reverse('answer_request_decline', args=[other.id]))
        row = AnswerRequest.objects.get(question_id=other.id, user=self.bob)
        self.assertEqual((row.state, row.routed), (AnswerRequest.DECLINED, False))

    def test_decline_needs_a_member_and_a_visible_question(self):
        self.expertise(self.bob, 'collect', 20)
        question = self.ask()
        url = reverse('answer_request_decline', args=[question.id])
        self.client.logout()
        self.client.post(url, HTTP_REFERER='/')
        self.assertEqual(AnswerRequest.objects.get(user=self.bob).state, AnswerRequest.PENDING)
        self.login(self.bob)
        self.assertEqual(self.client.post(reverse('answer_request_decline', args=[999999])).status_code, 404)


class ForYouPageTest(RoutingTestCase):
    def test_anonymous_visitors_sign_in_first(self):
        response = self.client.get(reverse('questions_for_you'))
        self.assertRedirects(response, reverse('auth_signin') + '?next=' + reverse('questions_for_you'),
                             fetch_redirect_response=False)

    def test_empty_state_explains_expertise(self):
        self.login(self.bob)
        page = self.client.get(reverse('questions_for_you'))
        self.assertCleanPage(page)
        self.assertContains(page, 'No questions for you yet')
        self.assertContains(page, 'an accepted answer and upvotes count more')
        self.assertContains(page, 'id="nav_for_you" class="on" aria-current="true"')

    def test_lists_requests_first_then_open_questions_in_top_tags(self):
        self.expertise(self.bob, 'collect', 20, answers=6, accepted=2)
        self.expertise(self.bob, 'maps', 1, answers=1, accepted=0)
        routed = self.ask(title='Routed question about collect export')
        self.set(settings.EXPERT_ROUTING_ENABLED, False)
        listed = self.ask(title='Listed question in the maps tag', tags='maps')
        other_tag = self.ask(title='Question in a tag bob never answered', tags='sepal')
        own = self.ask(self.bob, title='Bob asks his own collect question')
        closed = self.ask(title='Closed question about collect')
        Question.objects.filter(id=closed.id).update(state_string='(closed)')
        accepted = self.ask(title='Collect question with an accepted answer')
        Answer(author=self.carol, parent=accepted, body='Accepted answer long enough.', marked=True).save()
        answered = self.ask(title='Collect question bob already answered')
        Answer(author=self.bob, parent=answered, body='Bob answer long enough to count.').save()

        self.login(self.bob)
        page = self.client.get(reverse('questions_for_you'))
        self.assertCleanPage(page)
        body = page.content.decode()
        self.assertIn(routed.title, body)
        self.assertIn(listed.title, body)
        self.assertLess(body.index(routed.title), body.index(listed.title))   # requests first
        for question in (other_tag, own, closed, accepted, answered):
            self.assertNotIn(question.title, body)
        self.assertEqual(body.count('class="short-summary question-row'), 2)
        self.assertIn("Suggested for you: you've answered 6 questions tagged collect", body)
        self.assertEqual(body.count('for-you-reason'), 1)
        self.assertIn('2 questions', body)
        self.assertIn('6 answers, 2 accepted', body)   # sidebar
        self.assertIn('action="%s"' % reverse('answer_request_decline', args=[listed.id]), body)

    def test_held_questions_never_show(self):
        self.expertise(self.bob, 'collect', 20)
        self.set(settings.FIRST_POSTS_MODERATION, 'before')
        newbie = self.create_user('newbie', email_isvalid=True, reputation=60)
        held = self.ask(newbie, title='Held question about collect export')
        moderator = self.create_user('moderator', is_staff=True, reputation=10)
        self.expertise(moderator, 'collect', 20)
        for user in (self.bob, moderator):
            self.login(user)
            self.assertNotContains(self.client.get(reverse('questions_for_you')), held.title)


class ForYouQueryBudgetTest(QueryBudgetMixin, RoutingTestCase):
    def test_page_does_not_grow_with_rows(self):
        self.expertise(self.bob, 'collect', 20)
        self.set(settings.EXPERT_ROUTING_DAILY_CAP, 50)
        for i in range(12):
            question = self.ask(self.experts[1 + i % 6], title='Collect question number %d about export' % i)
            Answer(author=self.experts[1 + (i + 1) % 6], parent=question, body='An answer that is long enough.').save()
        self.assertEqual(AnswerRequest.objects.filter(user=self.bob).count(), 12)
        # The same bounds as test_query_budgets.PageQueryBudgetTest.test_for_you_page, with more rows.
        self.assertQueryBudget(reverse('questions_for_you'), cold=19, warm=13, user=self.bob)
