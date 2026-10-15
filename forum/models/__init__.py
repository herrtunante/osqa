import forum.utils.djangofix
from .question import Question ,QuestionRevision, QuestionSubscription
from .answer import Answer, AnswerRevision
from .tag import Tag, MarkedTag, TagSynonym
from .user import User, ValidationHash, AuthKeyUserAssociation, SubscriptionSettings
from .node import Node, NodeRevision, NodeState, NodeMetaClass
from .comment import Comment
from .action import Action, ActionRepute
from .meta import Vote, Flag, Badge, Award
from .utils import KeyValue
from .page import Page
from .moderation import ModerationReport
from .knowledge import KnowledgeChunk, AISuggestion
from .notification import Notification, QuestionReminder
from .search import SearchDocument

from .base import *

__all__ = [
        'Node', 'NodeRevision', 'NodeState',
        'Question', 'QuestionSubscription', 'QuestionRevision',
        'Answer', 'AnswerRevision',
        'Tag', 'TagSynonym', 'Comment', 'MarkedTag', 'Badge', 'Award',
        'ValidationHash', 'AuthKeyUserAssociation', 'SubscriptionSettings', 'KeyValue', 'User',
        'Action', 'ActionRepute', 'Vote', 'Flag', 'Page', 'ModerationReport', 'KnowledgeChunk', 'AISuggestion',
        'Notification', 'QuestionReminder', 'SearchDocument'
        ]


from forum.modules import get_modules_script_classes

for k, v in get_modules_script_classes('models', models.Model).items():
    if k not in __all__:
        __all__.append(k)
        exec("%s = v" % k)

NodeMetaClass.setup_relations()
BaseMetaClass.setup_denormalizes()
