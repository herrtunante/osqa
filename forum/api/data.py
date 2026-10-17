"""Forum objects as API data (the shapes are the schemas in forum/api/schemas.py).

Everything here is shown to whoever is asking: callers pass only posts the
viewer may see (Node.objects.visible_to(), review.can_view()).
"""
from django.urls import reverse
from django.utils.html import strip_tags

from forum.models import Answer, Comment, QuestionSubscription, TagSynonym, Vote
from forum.utils import review
from forum.utils.antispam import links_trusted

from .core import absolute, api_url, iso

BADGE_TYPES = {1: 'gold', 2: 'silver', 3: 'bronze'}


def source(node):
    """A post's Markdown source (the stored body is rendered HTML)."""
    revision = node.active_revision
    return (revision.body if revision else strip_tags(node.body)).strip()


def question_url(question_id):
    return absolute(reverse('question', kwargs={'id': question_id}))


def user_brief(user):
    if user is None:
        return None
    return {'id': user.id, 'username': user.username, 'url': absolute(user.get_profile_url()),
            'reputation': user.reputation}


def user_data(user):
    """Public profile fields only (what the profile page shows everybody)."""
    data = user_brief(user)
    data.update({
        'api_url': api_url('/users/%d' % user.id),
        'real_name': user.real_name or None,
        'location': user.location or None,
        # Links of new members aren't shown as links on the site either (antispam.links_trusted).
        'website': (user.website or None) if links_trusted(user) else None,
        'badges': {'gold': user.gold, 'silver': user.silver, 'bronze': user.bronze},
        'joined': iso(user.date_joined),
        'moderator': bool(user.is_staff or user.is_superuser),
    })
    return data


def _held(node, viewer):
    return review.is_held(node) if (review.is_moderator(viewer) or getattr(viewer, 'id', None) == node.author_id) \
        else False


def question_status(question):
    """unanswered / answered / accepted, from the counts (visible answers only)."""
    if question.accepted_count:
        return 'accepted'
    return 'answered' if question.answer_count else 'unanswered'


def question_summary(question, viewer):
    duplicate = question.duplicate_of_id
    return {
        'id': question.id,
        'url': absolute(question.get_absolute_url()),
        'api_url': api_url('/questions/%d' % question.id),
        'title': question.title,
        'tags': question.tagname_list(),
        'score': question.score,
        'status': question_status(question),
        'answer_count': question.answer_count,
        'view_count': question.view_count,
        'closed': bool(question.nis.closed),
        'duplicate_of': {'id': duplicate, 'url': question_url(duplicate),
                         'api_url': api_url('/questions/%d' % duplicate)} if duplicate else None,
        'author': user_brief(question.author),
        'created': iso(question.added_at),
        'last_activity': iso(question.last_activity_at or question.added_at),
        'held_for_review': _held(question, viewer),
    }


def comment_data(comment, viewer):
    return {
        'id': comment.id,
        'post_id': comment.parent_id,
        'url': absolute(comment.get_absolute_url()),
        'score': comment.score,
        'author': user_brief(comment.author),
        'created': iso(comment.added_at),
        'html': str(comment.comment),
        'markdown': source(comment),
        'held_for_review': _held(comment, viewer),
    }


def answer_data(answer, viewer, comments=None, my_votes=None):
    data = {
        'id': answer.id,
        'question_id': answer.parent_id,
        'url': absolute(answer.get_absolute_url()),
        'api_url': api_url('/answers/%d' % answer.id),
        'score': answer.score,
        'accepted': bool(answer.nis.accepted),
        'author': user_brief(answer.author),
        'created': iso(answer.added_at),
        'html': str(answer.body),
        'markdown': source(answer),
        'held_for_review': _held(answer, viewer),
    }
    if comments is not None:
        data['comments'] = [comment_data(c, viewer) for c in comments.get(answer.id, ())]
    if my_votes is not None:
        data['my_vote'] = my_votes.get(answer.id)
    return data


def visible_answers(question, viewer):
    answers = list(Answer.objects.filter(parent=question).visible_to(viewer).select_related('active_revision')
                   .prefetch_cached(fks=('author',)).order_by('-score', 'added_at', 'id'))
    return sorted(answers, key=lambda a: not a.nis.accepted)


def visible_comments(post_ids, viewer):
    """{post id: [comments, oldest first]} for the posts, in one query."""
    found = {}
    for comment in (Comment.objects.filter(parent_id__in=post_ids).visible_to(viewer).select_related('active_revision')
                    .prefetch_cached(fks=('author',)).order_by('added_at', 'id')):
        found.setdefault(comment.parent_id, []).append(comment)
    return found


def my_votes(viewer, node_ids):
    """{node id: 'up' | 'down'} of the viewer's votes (None for anonymous visitors)."""
    if not getattr(viewer, 'is_authenticated', False):
        return None
    return {node_id: 'up' if value > 0 else 'down'
            for node_id, value in Vote.objects.filter(user=viewer, node_id__in=node_ids).values_list('node_id', 'value')}


def question_detail(question, viewer):
    answers = visible_answers(question, viewer)
    comments = visible_comments([question.id] + [a.id for a in answers], viewer)
    votes = my_votes(viewer, [question.id] + [a.id for a in answers])
    data = question_summary(question, viewer)
    data.update({
        'html': str(question.body),
        'markdown': source(question),
        'deleted': bool(question.nis.deleted),
        'comments': [comment_data(c, viewer) for c in comments.get(question.id, ())],
        'answers': [answer_data(a, viewer, comments, votes) for a in answers],
    })
    if votes is not None:
        data['my_vote'] = votes.get(question.id)
        data['following'] = QuestionSubscription.objects.filter(question=question, user=viewer).exists()
    return data


def tag_data(tag, synonyms=None):
    if synonyms is None:
        synonyms = sorted(TagSynonym.objects.filter(target=tag).values_list('source_name', flat=True))
    return {
        'name': tag.name,
        'url': absolute(tag.get_absolute_url()),
        'api_url': api_url('/tags/%s' % tag.name),
        'count': tag.used_count,
        'description': tag.description or '',
        'synonyms': list(synonyms),
        'created': iso(tag.created_at),
    }


def synonyms_of(tags):
    """{tag id: [synonym names]} for a page of tags, in one query."""
    found = {}
    for target_id, name in TagSynonym.objects.filter(target__in=[t.id for t in tags]).order_by('source_name') \
            .values_list('target_id', 'source_name'):
        found.setdefault(target_id, []).append(name)
    return found


def badge_data(badge):
    return {
        'id': badge.id,
        'name': str(badge.name),
        'description': str(badge.description),
        'type': BADGE_TYPES.get(badge.type, 'bronze'),
        'awarded_count': badge.awarded_count,
        'url': absolute(badge.get_absolute_url()),
    }

