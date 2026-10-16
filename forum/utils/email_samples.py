"""Sample data for the admin's email previews (Admin > Email previews).

Every kind of email the site sends, rendered with the real templates for the
admin who is looking (so the greeting, footer and unsubscribe links are theirs)
and made-up posts and people. The sample objects are unsaved model instances:
nothing is written to the database and no email is sent.
"""
import datetime

from django.utils.translation import gettext_lazy as _lazy

from forum.models import Answer, Comment, Question, User

SAMPLE_ID = 990000  # ids of the made-up objects; never saved


def _user(n, username):
    return User(id=SAMPLE_ID + n, username=username, email='%s@example.com' % username, reputation=120)


def _question(n, title, author, body, tags='export csv'):
    return Question(id=SAMPLE_ID + n, node_type='question', title=title, tagnames=tags, author=author,
                    body=body, added_at=datetime.datetime.now())


class SampleDigest:
    """Stands in for send_email_alerts.DigestQuestionsIndex."""
    def __init__(self, questions):
        self.questions = questions
        self.count = 12

    def get_for_user(self, user):
        q = self.questions
        return {'interesting': q[:3], 'may_help': q[3:5], 'subscriptions': q[5:]}


def _people():
    bob = _user(1, 'bob')
    carol = _user(2, 'carol')
    question = _question(
        10, 'How do I export survey data to CSV?', carol,
        '<p>I collected about 400 forms in the field. How do I get them out as one CSV file, '
        'with one column per question?</p>')
    answer = Answer(id=SAMPLE_ID + 11, node_type='answer', parent=question, abs_parent=question, author=bob,
                    body='<p>Open the survey, go to <strong>Data &gt; Export</strong> and pick <em>CSV</em>. '
                         'Each repeat group becomes its own file, so tick &quot;Merge repeats&quot; if you want a '
                         'single table.</p><pre><code>export --format csv --merge-repeats</code></pre>',
                    added_at=datetime.datetime.now())
    return bob, carol, question, answer


def new_answer(recipient):
    bob, carol, question, answer = _people()
    return {'answer': answer, 'email_kind': 'question', 'email_question': question}


def new_comment(recipient):
    bob, carol, question, answer = _people()
    comment = Comment(id=SAMPLE_ID + 12, node_type='comment', parent=answer, abs_parent=question, author=carol,
                      body='<p>Thanks! Does the merged file keep the GPS columns too?</p>',
                      added_at=datetime.datetime.now())
    return {'comment': comment, 'email_kind': 'question', 'email_question': question}


def answer_accepted(recipient):
    bob, carol, question, answer = _people()
    return {'answer': answer, 'accepted_by': carol, 'email_kind': 'question', 'email_question': question}


def mention(recipient):
    bob, carol, question, answer = _people()
    body = ('<p>I tried the export and it works. <a class="mention" data-user="%d" href="#">@%s</a> do you know '
            'if the GPS columns survive the merge? The documentation is not clear about it.</p>'
            % (recipient.id, recipient.username))
    comment = Comment(id=SAMPLE_ID + 13, node_type='comment', parent=answer, abs_parent=question, author=carol,
                      body=body, added_at=datetime.datetime.now())
    return {'node': comment, 'question': question, 'author': carol, 'email_kind': 'mention'}


def new_question(recipient):
    bob, carol, question, answer = _people()
    return {'question': question, 'email_kind': 'new_question'}


def new_member(recipient):
    return {'newmember': _user(3, 'dana'), 'email_kind': 'member'}


def digest(recipient):
    bob, carol, question, answer = _people()
    titles = ['Why does my map not load offline?', 'Can I translate a survey into Swahili?',
              'How do I add a new user to my project?', 'Exporting plots with their photos',
              'What does "sync conflict" mean?']
    questions = [question] + [_question(20 + i, title, bob, '<p>…</p>') for i, title in enumerate(titles)]
    return {'digest': SampleDigest(questions[1:] + questions[:1]),
            'new_members': [_user(3, 'dana'), _user(4, 'emeka'), _user(5, 'fatima')],
            'new_member_count': 3, 'show_all_users': False, 'flagged_count': 2, 'email_kind': 'digest'}


def summary(recipient):
    """A weekly summary with one or more items in each section."""
    from forum.utils.email_frequency import Summary
    bob, carol, question, answer = _people()
    own = _question(30, 'Why does my map not load offline?', recipient, '<p>…</p>', tags='maps offline')
    own_answer = Answer(id=SAMPLE_ID + 31, node_type='answer', parent=own, abs_parent=own, author=bob,
                        body='<p>Download the map area first under <em>Settings &gt; Offline maps</em>.</p>',
                        added_at=datetime.datetime.now())
    comment = Comment(id=SAMPLE_ID + 32, node_type='comment', parent=answer, abs_parent=question, author=carol,
                      body='<p>Thanks! Does the merged file keep the GPS columns too?</p>',
                      added_at=datetime.datetime.now())
    mention = Comment(id=SAMPLE_ID + 33, node_type='comment', parent=answer, abs_parent=question, author=carol,
                      body='<p>@%s have you seen this with older surveys?</p>' % recipient.username,
                      added_at=datetime.datetime.now())
    new = _question(34, 'Can I translate a survey into Swahili?', carol, '<p>…</p>', tags='translation')

    def item(section, kind, node, actor, question, own=False):
        return {'section': section, 'kind': kind, 'node': node, 'actor': actor, 'question': question,
                'url': node.get_absolute_url(), 'own': own}
    items = [
        item('mention', 'mention', mention, carol, question),
        item('answer_own', 'answer', own_answer, bob, own),
        item('answer', 'answer', answer, bob, question),
        item('comment', 'comment', comment, carol, question),
        item('accepted', 'accepted', answer, carol, question),
        item('new_question', 'new_question', new, carol, new),
        {'section': 'member', 'kind': 'member', 'actor': _user(3, 'dana'), 'url': _user(3, 'dana').get_profile_url()},
    ]
    return {'summaries': {recipient.id: Summary(items, 'w')}, 'period': 'w', 'email_kind': 'summary_weekly'}


def post_reviewed(recipient):
    bob, carol, question, answer = _people()
    return {'node': answer, 'outcome': 'approved'}


def suggested_edit(recipient):
    from forum.models import SuggestedEdit
    bob, carol, question, answer = _people()
    suggestion = SuggestedEdit(id=SAMPLE_ID + 40, node=answer, author=recipient, summary='Added the menu path',
                               reason='no_improvement', comment='The menu path was already in the answer.')
    return {'suggestion': suggestion, 'node': answer, 'reviewer': carol, 'outcome': 'rejected',
            'reason': "It doesn't make the post easier to read or more useful", 'email_kind': 'suggested_edit'}


def accept_reminder(recipient):
    bob, carol, question, answer = _people()
    return {'question': question, 'email_kind': 'question', 'email_question': question}


def feedback(recipient):
    return {'name': 'carol', 'email': 'carol@example.com', 'ip': '203.0.113.7',
            'message': "The CSV export link on the FAQ page is broken.\nIt gives a 404 since Monday."}


def user_reported(recipient):
    bob, carol, question, answer = _people()
    return {'reported': bob, 'user': carol, 'message': 'Posts links to a shop in every answer.'}


def welcome(recipient):
    return {'validation_code': 'sample-validation-code', 'link_hours': 48}


def password_reset(recipient):
    return {'reset_code': 'sample-reset-code', 'link_minutes': 60}


def mail_validation(recipient):
    return {'validation_code': 'sample-validation-code', 'link_hours': 48}


def _security_event():
    return {'when': datetime.datetime.now(), 'browser': 'Firefox on Windows', 'ip': '203.0.113.7'}


def password_changed(recipient):
    return dict(_security_event(), how='changed')


def new_signin(recipient):
    return _security_event()


def email_change_confirm(recipient):
    return {'confirm_code': 'sample-confirm-code', 'new_email': 'new.address@example.com', 'link_hours': 48}


def email_change_notice(recipient):
    return {'cancel_code': 'sample-cancel-code', 'new_email_masked': 'n•••@example.com', 'link_hours': 48}


def email_changed_by_admin(recipient):
    return {'new_email_masked': 'n•••@example.com'}


def account_deleted(recipient):
    return {'deleted_name': recipient.username, 'posts_deleted': False}


def account_exists(recipient):
    return {}


def mail_test(recipient):
    return {'user': recipient}


# slug -> (name, who gets it, template, sample context)
KINDS = {
    'new-answer': (_lazy("New answer"), _lazy("People who follow a question, when someone answers it."),
                   'notifications/newanswer.html', new_answer),
    'new-comment': (_lazy("New comment"), _lazy("The post's author and people who follow all comments."),
                    'notifications/newcomment.html', new_comment),
    'answer-accepted': (_lazy("Answer accepted"), _lazy("The answer's author and followers who asked for it."),
                        'notifications/answeraccepted.html', answer_accepted),
    'mention': (_lazy("Mention"), _lazy("Someone mentioned with @username in a post or comment."),
                'notifications/mention.html', mention),
    'new-question': (_lazy("New question"), _lazy("People who want new questions, or new questions in tags they follow."),
                     'notifications/newquestion.html', new_question),
    'new-member': (_lazy("New member"), _lazy("People who asked to hear about new members."),
                   'notifications/newmember.html', new_member),
    'digest': (_lazy("Daily digest"), _lazy("People subscribed to the daily digest."),
               'notifications/digest.html', digest),
    'summary': (_lazy("Daily or weekly summary"),
                _lazy("Members who chose a daily or weekly summary instead of separate activity emails."),
                'notifications/summary.html', summary),
    'post-reviewed': (_lazy("Post reviewed"),
                      _lazy("New members whose post waited for review, when a moderator approves or removes it."),
                      'notifications/post_reviewed.html', post_reviewed),
    'suggested-edit': (_lazy("Suggested edit reviewed"),
                       _lazy("Members who suggested an edit, when a reviewer approves or rejects it."),
                       'notifications/suggested_edit.html', suggested_edit),
    'accept-reminder': (_lazy("Accept reminder"), _lazy("Askers whose question has answers but none accepted."),
                        'notifications/acceptreminder.html', accept_reminder),
    'feedback': (_lazy("Feedback"), _lazy("Administrators, when someone sends the feedback form."),
                 'notifications/feedback.html', feedback),
    'user-reported': (_lazy("User reported"), _lazy("Administrators, when someone reports a user."),
                      'notifications/user_reported.html', user_reported),
    'welcome': (_lazy("Welcome"), _lazy("New members, with the link that confirms their email address (double "
                                        "opt-in)."),
                'auth/welcome_email.html', welcome),
    'password-reset': (_lazy("Password reset"), _lazy("Members who use \"Forgot your password?\"."),
                       'auth/password_reset_email.html', password_reset),
    'confirm-email': (_lazy("Confirm email address"), _lazy("Members who ask for a new confirmation link."),
                      'auth/mail_validation.html', mail_validation),
    'password-changed': (_lazy("Password changed"),
                         _lazy("Members whose password was changed, reset or set (a security notice)."),
                         'auth/password_changed_email.html', password_changed),
    'new-sign-in': (_lazy("New sign-in"), _lazy("Members who sign in from a new browser, when the site has these "
                                                "notices turned on (Security settings)."),
                    'auth/new_signin_email.html', new_signin),
    'email-change-confirm': (_lazy("Confirm new email address"),
                             _lazy("The new address, when a member changes their email address."),
                             'auth/email_change_confirm_email.html', email_change_confirm),
    'email-change-notice': (_lazy("Email address being changed"),
                            _lazy("The old address, when a member changes their email address (with a cancel link)."),
                            'auth/email_change_notice_email.html', email_change_notice),
    'email-changed-by-admin': (_lazy("Email address changed by an administrator"),
                               _lazy("The old address, when an administrator changes a member's address."),
                               'auth/email_changed_by_admin_email.html', email_changed_by_admin),
    'account-deleted': (_lazy("Account deleted"), _lazy("Members who delete their account (the last email)."),
                        'auth/account_deleted_email.html', account_deleted),
    'account-exists': (_lazy("Account already exists"),
                       _lazy("The owner of an address someone tried to sign up with, when new members must "
                             "confirm before signing in."),
                       'auth/account_exists_email.html', account_exists),
    'test-email': (_lazy("Test email"), _lazy("The administrator who tests the email settings."),
                   'osqaadmin/mail_test.html', mail_test),
}


def render(kind, recipient):
    """The sample email of `kind` for `recipient`: dict with subject, html, text, headers."""
    from forum.utils.mail import render_template_email
    name, audience, template, sample = KINDS[kind]
    return render_template_email(recipient, template, sample(recipient))
