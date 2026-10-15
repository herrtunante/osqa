import datetime
import re
try:
    from hashlib import md5
except:
    from md5 import new as md5
from urllib.parse import quote_plus, urlencode
from django.core.exceptions import EmptyResultSet
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

    def prefetch_cached(self, fks=(), counts=()):
        """When the rows are loaded, fill their foreign keys `fks` from the
        object cache and their DenormalizedField `counts` in one grouped query
        each (see prefetch_cached_fk / prefetch_denormalized). Kept by slicing,
        so a paginated list loads them for the shown page only."""
        clone = self._chain()
        clone._osqa_prefetch = (tuple(fks), tuple(counts))
        return clone

    def _clone(self):
        clone = super(CachedQuerySet, self)._clone()
        clone._osqa_prefetch = getattr(self, '_osqa_prefetch', None)
        return clone

    def _fetch_all(self):
        fetching = self._result_cache is None
        super(CachedQuerySet, self)._fetch_all()
        spec = getattr(self, '_osqa_prefetch', None)
        if fetching and spec and self._result_cache and isinstance(self._result_cache[0], models.Model):
            fks, counts = spec
            prefetch_cached_fk(self._result_cache, *fks)
            prefetch_denormalized(self._result_cache, *counts)

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
        try:
            cache_key = self.model._generate_cache_key("CNT:%s" % self._get_query_hash())
        except EmptyResultSet:
            return 0  # e.g. .none() or id__in=[]: the query can't even be written out
        result = self._fetch_from_query_cache(cache_key)

        if result is not None:
            return result

        result = super(CachedQuerySet, self).count()
        cache.set(cache_key, (datetime.datetime.now(), result), 60 * 60)
        return result

    def cached_aggregate(self, **aggregates):
        """aggregate(**aggregates), kept in the query cache like count()."""
        signature = md5(repr(sorted(aggregates.items())).encode('utf-8')).hexdigest()
        cache_key = self.model._generate_cache_key("AGG:%s:%s" % (self._get_query_hash(), signature))
        result = self._fetch_from_query_cache(cache_key)

        if result is not None:
            return result

        result = self.aggregate(**aggregates)
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
        setattr(cls, '_denormalized_%s' % name, self)

    def prefetch(self, instances, name):
        """Give every instance its count with at most one grouped query.

        Uses the same query cache entries as the getter (one key per
        instance's count query, checked against the related model's
        invalidation time stamp), so a warm cache needs no query at all and
        what is computed here is what the getter would have stored.
        """
        dict_name = '_%s_dencache_' % name
        todo = [i for i in instances if i.id and i.__dict__.get(dict_name, None) is None]
        if not todo:
            return

        args, kwargs = self.filter
        querysets = [(i, getattr(i, self.manager).filter(*args, **kwargs)) for i in todo]
        model = querysets[0][1].model
        keys = dict((i.id, model._generate_cache_key("CNT:%s" % qs._get_query_hash())) for i, qs in querysets)
        invalidation_key = model._get_cache_query_invalidation_key()
        cached = cache.get_many([invalidation_key] + list(keys.values()))

        missing = []
        if invalidation_key not in cached:
            model._set_query_cache_invalidation_timestamp()
            missing = todo
        else:
            for inst in todo:
                hit = cached.get(keys[inst.id])
                if hit and cached[invalidation_key] < hit[0]:
                    inst.__dict__[dict_name] = hit[1]
                else:
                    missing.append(inst)

        if missing:
            relation = getattr(type(missing[0]), self.manager).field
            counts = dict(relation.model._default_manager.filter(*args, **kwargs)
                          .filter(**{'%s__in' % relation.name: [i.id for i in missing]})
                          .order_by().values_list(relation.attname).annotate(n=models.Count('pk')))
            now = datetime.datetime.now()
            for inst in missing:
                inst.__dict__[dict_name] = counts.get(inst.id, 0)
            cache.set_many(dict((keys[i.id], (now, i.__dict__[dict_name])) for i in missing), 60 * 60)


def prefetch_denormalized(instances, *names):
    """Load the DenormalizedField counts `names` for a list of instances of one
    model in one query per name (instead of one per instance and name)."""
    instances = [i for i in instances if i is not None]
    if not instances:
        return
    for name in names:
        getattr(type(instances[0]), '_denormalized_%s' % name).prefetch(instances, name)


def load_cached(model, pks):
    """{pk: object} for the given primary keys, as model.objects.get(pk=...)
    would return them one by one (from the object cache, else from the
    database, then cached), with one cache round trip and at most one query.
    Missing rows are left out."""
    loaded = {}
    pks = [pk for pk in pks if pk is not None]
    if not pks:
        return loaded
    if issubclass(model, BaseModel):
        keys = dict((model.infer_cache_key({'pk': pk}), pk) for pk in pks)
        keys.pop(None, None)
        queryset = model._base_manager.get_queryset()
        for key, data in cache.get_many(list(keys)).items():
            obj = queryset.obj_from_datadict(data)
            obj.reset_original_state()
            loaded[keys[key]] = getattr(obj, 'leaf', obj) if hasattr(obj, 'node_type') else obj
    missing = [pk for pk in pks if pk not in loaded]
    if missing:
        for obj in model._base_manager.filter(pk__in=missing):
            if isinstance(obj, BaseModel):
                obj.cache()
            loaded[obj.pk] = getattr(obj, 'leaf', obj) if hasattr(obj, 'node_type') else obj
    return loaded


def prefetch_cached_fk(instances, *field_names):
    """Fill the foreign keys `field_names` of `instances` (e.g. 'author') from
    the object cache in one round trip, loading the ones not in it with one
    query and caching them, as `instance.<field>` would one by one."""
    instances = [i for i in instances if i is not None]
    if not instances:
        return
    for field_name in field_names:
        field = instances[0]._meta.get_field(field_name)
        model = field.related_model
        pending = {}
        for inst in instances:
            pk = getattr(inst, field.attname)
            if pk is not None and not field.is_cached(inst):
                pending.setdefault(pk, []).append(inst)
        if not pending:
            continue

        loaded = load_cached(model, pending)
        for pk, objs in pending.items():
            if pk in loaded:
                for inst in objs:
                    field.set_cached_value(inst, loaded[pk])


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

        partial = bool(self.id) and not full_save
        if partial:
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
        if partial:
            # Only the changed fields were written; the others may be out of
            # date in this copy (another request changed them), so the cache
            # is refilled from the database on the next read.
            self.uncache()
        else:
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





