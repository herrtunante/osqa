import datetime
import re
try:
    from hashlib import md5
except:
    from md5 import new as md5
from urllib.parse import quote_plus, urlencode
from django.db import models, IntegrityError, connection, transaction
from urllib.parse import quote as django_urlquote
from django.utils.html import strip_tags
from django.conf import settings as django_settings
from django.urls import reverse
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.template.defaultfilters import slugify
from django.db.models.signals import post_delete, post_save, pre_save, pre_delete
from django.utils.translation import gettext as _
from django.utils.safestring import mark_safe
import django.dispatch
from forum import settings
import logging


if not hasattr(cache, 'get_many'):
    #put django 1.2 code here
    pass

class CachedQuerySet(models.query.QuerySet):

    def obj_from_datadict(self, datadict):
        obj = self.model()
        obj.__dict__.update(datadict)

        if hasattr(obj, '_state'):
            obj._state.db = 'default'

        return obj

    def _can_use_object_cache(self, args, kwargs):
        # The object cache is keyed by primary key only, so it may only answer
        # a lookup that is nothing but a pk lookup on an otherwise unfiltered
        # queryset (filters added by the model's default manager are allowed).
        if args or len(kwargs) != 1 or self.query.is_sliced or self.query.extra:
            return False

        return self.query.where == self.model.objects.get_queryset().query.where

    def _pk_lookup_from_q(self, args, kwargs):
        # Forward foreign keys are resolved with get(Q(<pk attname>=value)) since
        # Django 1.7; turn that into a pk lookup so it can use the object cache.
        if len(args) == 1 and not kwargs and isinstance(args[0], models.Q):
            q = args[0]
            if not q.negated and len(q.children) == 1 and isinstance(q.children[0], tuple):
                key, value = q.children[0]
                pk = self.model._meta.pk
                if key in ('pk', pk.name, pk.attname):
                    return (), {'pk': value}
        return args, kwargs

    def get(self, *args, **kwargs):
        args, kwargs = self._pk_lookup_from_q(args, kwargs)
        key = self._can_use_object_cache(args, kwargs) and self.model.infer_cache_key(kwargs) or None

        if key is not None:
            obj = cache.get(key)

            if obj is None:
                obj = super(CachedQuerySet, self).get(*args, **kwargs)
                obj.cache()
            else:
                obj = self.obj_from_datadict(obj)

            obj.reset_original_state()

            return obj

        return super(CachedQuerySet, self).get(*args, **kwargs)

    def _fetch_from_query_cache(self, key):
        invalidation_key = self.model._get_cache_query_invalidation_key()
        cached_result = cache.get_many([invalidation_key, key])

        if not invalidation_key in cached_result:
            self.model._set_query_cache_invalidation_timestamp()
            return None

        if (key in cached_result) and(cached_result[invalidation_key] < cached_result[key][0]):
            return cached_result[key][1]

        return None

    def count(self):
        cache_key = self.model._generate_cache_key("CNT:%s" % self._get_query_hash())
        result = self._fetch_from_query_cache(cache_key)

        if result is not None:
            return result

        result = super(CachedQuerySet, self).count()
        cache.set(cache_key, (datetime.datetime.now(), result), 60 * 60)
        return result

    def _get_query_hash(self):
        try:
            return md5(str(self.query).encode("utf-8")).hexdigest()
        except:
            try:
                return md5(self.query).hexdigest()
            except:
                return md5(str(self.query)).hexdigest()


class CachedManager(models.Manager):
    use_for_related_fields = True

    def get_queryset(self):
        return CachedQuerySet(self.model)

    def get_or_create(self, *args, **kwargs):
        # Django 4.2: separate 'defaults' from lookup kwargs
        # 'defaults' is a special parameter for get_or_create, not a query field
        defaults = kwargs.pop('defaults', {})
        lookup_kwargs = kwargs.copy()

        try:
            return self.get(*args, **lookup_kwargs), False
        except self.model.DoesNotExist:
            # Put defaults back for create
            kwargs['defaults'] = defaults
            return super(CachedManager, self).get_or_create(*args, **kwargs)


class DenormalizedField(object):
    def __init__(self, manager, *args, **kwargs):
        self.manager = manager
        self.filter = (args, kwargs)

    def setup_class(self, cls, name):
        dict_name = '_%s_dencache_' % name

        def getter(inst):
            val = inst.__dict__.get(dict_name, None)

            if val is None:
                val = getattr(inst, self.manager).filter(*self.filter[0], **self.filter[1]).count()
                inst.__dict__[dict_name] = val
                inst.cache()

            return val

        def reset_cache(inst):
            inst.__dict__.pop(dict_name, None)
            inst.uncache()

        cls.add_to_class(name, property(getter))
        cls.add_to_class("reset_%s_cache" % name, reset_cache)


class BaseMetaClass(type(models.Model)):
    to_denormalize = []

    def __new__(cls, *args, **kwargs):
        new_cls = super(BaseMetaClass, cls).__new__(cls, *args, **kwargs)

        BaseMetaClass.to_denormalize.extend(
            [(new_cls, name, field) for name, field in new_cls.__dict__.items() if isinstance(field, DenormalizedField)]
        )

        return new_cls

    @classmethod
    def setup_denormalizes(cls):
        for new_cls, name, field in BaseMetaClass.to_denormalize:
            field.setup_class(new_cls, name)


class BaseModel(models.Model, metaclass=BaseMetaClass):

    objects = CachedManager()

    class Meta:
        abstract = True
        app_label = 'forum'

    def __init__(self, *args, **kwargs):
        super(BaseModel, self).__init__(*args, **kwargs)
        self.reset_original_state(kwargs.keys())

    def reset_original_state(self, reset_fields=None):
        self._original_state = self._as_dict()
        
        if reset_fields:
            self._original_state.update(dict([(f, None) for f in reset_fields]))

    def _changed(self, field):
        # Deferred fields (e.g. instances loaded by the delete collector with
        # .only()) are neither in __dict__ nor in the original state.
        return field.attname in self.__dict__ and \
               self._original_state.get(field.attname) != self.__dict__[field.attname]

    def get_dirty_fields(self):
        return [f.name for f in self._meta.fields if self._changed(f)]

    def _as_dict(self):
        # Only loaded values: reading a deferred field would query the database
        # and build another instance, which recursed without end on delete().
        return dict([(name, self.__dict__[name]) for name in
                     ([f.attname for f in self._meta.fields] + [k for k in self.__dict__.keys() if k.endswith('_dencache_')])
                     if name in self.__dict__
        ])

    def _get_update_kwargs(self):
        return dict([
            (f.name, getattr(self, f.name)) for f in self._meta.fields if self._changed(f)
        ])

    def save(self, full_save=False, *args, **kwargs):
        # Django 4.2: ExpressionNode renamed to Expression
        put_back = [k for k, v in self.__dict__.items() if isinstance(v, models.expressions.Expression)]

        if hasattr(self, '_state'):
            self._state.db = 'default'

        if self.id and not full_save:
            self.__class__.objects.filter(id=self.id).update(**self._get_update_kwargs())
        else:
            super(BaseModel, self).save()

        if put_back:
            try:
                self.__dict__.update(
                    self.__class__.objects.filter(id=self.id).values(*put_back)[0]
                )
            except:
                logging.error("Unable to read %s from %s" % (", ".join(put_back), self.__class__.__name__))
                self.uncache()

        self.reset_original_state()
        self._set_query_cache_invalidation_timestamp()
        self.cache()

    @classmethod
    def _get_cache_query_invalidation_key(cls):
        return cls._generate_cache_key("INV_TS")

    @classmethod
    def _set_query_cache_invalidation_timestamp(cls):
        cache.set(cls._get_cache_query_invalidation_key(), datetime.datetime.now(), 60 * 60 * 24)

        for base in filter(lambda c: issubclass(c, BaseModel) and (not c is BaseModel), cls.__bases__):
            base._set_query_cache_invalidation_timestamp()

    @classmethod
    def _generate_cache_key(cls, key, group=None):
        if group is None:
            group = cls.__name__

        return '%s:%s:%s' % (settings.APP_URL, group, key)

    def cache_key(self):
        return self._generate_cache_key(self.id)

    @classmethod
    def value_to_list_on_cache_query(cls):
        return 'id'

    @classmethod
    def infer_cache_key(cls, querydict):
        try:
            pk = [v for (k,v) in querydict.items() if k in ('pk', 'pk__exact', 'id', 'id__exact'
                            ) or k.endswith('_ptr__pk') or k.endswith('_ptr__id')][0]

            cache_key = cls._generate_cache_key(pk)

            if len(cache_key) > django_settings.CACHE_MAX_KEY_LENGTH:
                cache_key = cache_key[:django_settings.CACHE_MAX_KEY_LENGTH]

            return cache_key
        except:
            return None

    def cache(self):
        # A partially loaded instance must not be served as the full object later.
        if self.get_deferred_fields():
            return
        cache.set(self.cache_key(), self._as_dict(), 60 * 60)

    def uncache(self):
        cache.delete(self.cache_key())

    def delete(self):
        self.uncache()
        self._set_query_cache_invalidation_timestamp()
        super(BaseModel, self).delete()


from .user import User
from .node import Node, NodeRevision, NodeManager
from .action import Action





