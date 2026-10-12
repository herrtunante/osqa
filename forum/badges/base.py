import re

from django.core.exceptions import MultipleObjectsReturned
from django.db.models.signals import post_save

from forum.models import Badge, Node, Action
from forum.actions import AwardAction

import logging

class BadgesMeta(type):
    # Badge handlers by class name; Badge.cls in the database holds the same name.
    by_class = {}

    def __new__(mcs, name, bases, dic):
        badge = type.__new__(mcs, name, bases, dic)

        # Badge rows are created on demand (see install() and db_object()), not
        # here: this runs at import time, possibly before the tables exist or
        # against a different database than the one used later (tests).
        if not dic.get('abstract', False):
            inst = badge()

            def hook(action, new):
                user = inst.award_to(action)

                if user:
                    badge.award(user, action, badge.award_once)

            for action in badge.listen_to:
                action.hook(hook)

            BadgesMeta.by_class[name] = inst

        return badge

    @classmethod
    def install(mcs):
        """Create the Badge rows of registered badges that are not in the database yet."""
        existing = set(Badge.objects.values_list('cls', flat=True))
        for name, inst in mcs.by_class.items():
            if name not in existing:
                Badge(cls=name, type=getattr(inst, 'type', Badge.BRONZE)).save()

class AbstractBadge(object, metaclass=BadgesMeta):

    abstract = True
    award_once = False

    @property
    def name(self):
        raise NotImplementedError

    @property
    def description(self):
        raise NotImplementedError

    @classmethod
    def db_object(cls):
        return Badge.objects.get_or_create(cls=cls.__name__, defaults={'type': getattr(cls, 'type', Badge.BRONZE)})[0]

    @classmethod
    def award(cls, user, action, once=False):
        db_object = cls.db_object()
        try:
            if once:
                node = None
                awarded = AwardAction.get_for(user, db_object)
            else:
                node = action.node
                awarded = AwardAction.get_for(user, db_object, node)

            trigger = isinstance(action, Action) and action or None

            if not awarded:
                AwardAction(user=user, node=node).save(data=dict(badge=db_object, trigger=trigger))
        except MultipleObjectsReturned:
            if node:
                logging.error('Found multiple %s badges awarded for user %s (%s)' % (self.name, user.username, user.id))
            else:
                logging.error('Found multiple %s badges awarded for user %s (%s) and node %s' % (self.name, user.username, user.id, node.id))