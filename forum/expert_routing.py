"""Expert routing hooks (forum/utils/expertise.py): a published question is
suggested to the members with the most expertise in its tags; a question
held for review is suggested when a moderator approves it. Answering marks a
member's request answered, and an accepted answer updates the answerer's
expertise in that question's tags (the daily refresh_tag_expertise rebuilds
everything). Like every action hook, these run in the hook thread, after the
request that caused them."""
from forum.actions import AcceptAnswerAction, AnswerAction, ApprovePostAction, AskAction
from forum.utils import expertise


def question_asked(action, new):
    # route_question() leaves held questions alone (Node.is_notifiable).
    expertise.route_question(action.node)

AskAction.hook(question_asked)


def question_approved(action, new):
    from forum.utils.review import replay_on_approval
    replay_on_approval(action, {'ask': question_asked})

ApprovePostAction.hook(question_approved)


def answer_posted(action, new):
    answer = action.node
    expertise.answered(answer.parent_id, answer.author_id)

AnswerAction.hook(answer_posted)


def answer_accepted(action, new):
    answer = action.node
    question = answer.question
    if answer.author_id != question.author_id:
        expertise.refresh_user(answer.author, question.tags.values_list('id', flat=True))

AcceptAnswerAction.hook(answer_accepted)
