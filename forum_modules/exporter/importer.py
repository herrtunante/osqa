import os, tarfile, datetime, configparser, logging, shutil, tempfile, zipfile

from django.core.cache import cache
from django.db import models, transaction
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from xml.sax import make_parser
from xml.sax.handler import ContentHandler, ErrorHandler

from forum.models import User, AuthKeyUserAssociation, SubscriptionSettings, Tag, MarkedTag, Node, NodeRevision, \
    NodeState, Action, ActionRepute, Vote, Flag, Badge, Award, KeyValue
from forum.templatetags.extra_tags import diff_date

from .exporter import TMP_FOLDER, DATETIME_FORMAT, DATE_FORMAT, META_INF_SECTION, OPTIONS_INF_SECTION, CACHE_KEY
from . import settings

NO_DEFAULT = object()

# Files a backup may contain; anything else in the archive is ignored.
BACKUP_FILES = ('backup.inf', 'users.xml', 'tags.xml', 'nodes.xml', 'actions.xml', 'states.xml', 'awards.xml',
                'settings.xml')


def raw_save(obj, **kwargs):
    # Store the row as it is in the backup. The models' own save() methods would
    # create revisions, count tags, give reputation, run action hooks, etc.
    models.Model.save(obj, **kwargs)


class ContentElement():
    def __init__(self, content):
        self._content = content

    def content(self):
        return self._content.strip()

    def as_bool(self):
        return self.content() == "true"

    def as_date(self, default=NO_DEFAULT):
        try:
            return datetime.datetime.strptime(self.content(), DATE_FORMAT).date()
        except:
            if default == NO_DEFAULT:
                return datetime.date.fromtimestamp(0)
            else:
                return default


    def as_datetime(self, default=NO_DEFAULT):
        try:
            return datetime.datetime.strptime(self.content(), DATETIME_FORMAT)
        except:
            if default == NO_DEFAULT:
                return datetime.datetime.fromtimestamp(0)
            else:
                return default

    def as_int(self, default=0):
        try:
            return int(self.content())
        except:
            return default

    def __str__(self):
        return self.content()


class RowElement(ContentElement):
    def __init__(self, name, attrs, parent=None):
        self.name = name.lower()
        self.parent = parent
        self.attrs = dict([(k.lower(), ContentElement(v)) for k, v in attrs.items()])
        self._content = u''
        self.sub_elements = {}

        if parent:
            parent.add(self)

    def add_to_content(self, ch):
        self._content += str(ch)

    def add(self, sub):
        curr = self.sub_elements.get(sub.name, None)

        if not curr:
            curr = []
            self.sub_elements[sub.name] = curr

        curr.append(sub)

    def get(self, name, default=None):
        return self.sub_elements.get(name.lower(), [default])[-1]

    def get_list(self, name):
        return self.sub_elements.get(name.lower(), [])

    def get_listc(self, name):
        return [r.content() for r in self.get_list(name)]

    def getc(self, name, default=""):
        el = self.get(name, None)

        if el:
            return el.content()
        else:
            return default

    def get_attr(self, name, default=""):
        return self.attrs.get(name.lower(), ContentElement(default))

    def as_pickled(self, default=None):
        value_el = self.get('value')

        if value_el:
            return value_el._as_pickled(default)
        else:
            return default

    TYPES_MAP = dict([(c.__name__, c) for c in (int, str, float)])

    def _as_pickled(self, default=None):
        type = self.get_attr('type').content()

        try:
            if type == 'dict':
                return dict([ (item.get_attr('key').content(), item.as_pickled()) for item in self.get_list('item') ])
            elif type == 'list':
                return [item.as_pickled() for item in self.get_list('item')]
            elif type == 'bool':
                return self.content().lower() == 'true'
            elif type in RowElement.TYPES_MAP:
                return RowElement.TYPES_MAP[type](self.content())
            else:
                return self.content()
        except:
            return default




class TableHandler(ContentHandler):
    def __init__(self, root_name, row_name, callback, callback_args = [], ping = None):
        self.root_name = root_name.lower()
        self.row_name = row_name.lower()
        self.callback = callback
        self.callback_args = callback_args
        self.ping = ping

        self._reset()

    def _reset(self):
        self.curr_element = None
        self.in_tag = None

    def startElement(self, name, attrs):
        name = name.lower()

        if name == self.root_name.lower():
            pass
        elif name == self.row_name:
            self.curr_element = RowElement(name, attrs)
        else:
            self.curr_element = RowElement(name, attrs, self.curr_element)

    def characters(self, ch):
        if self.curr_element:
            self.curr_element.add_to_content(ch)

    def endElement(self, name):
        name = name.lower()

        if name == self.root_name:
            pass
        elif name == self.row_name:
            self.callback(self.curr_element, *self.callback_args)
            if self.ping:
                self.ping()

            self._reset()
        else:
            self.curr_element = self.curr_element.parent


class SaxErrorHandler(ErrorHandler):
    def error(self, e):
        raise e

    def fatalError(self, e):
        raise e

    def warning(self, e):
        raise e


def extract_backup(fileobj, location):
    """Extract the files of a backup archive (zip or tar.gz) into location.

    Only the known backup files at the top of the archive are extracted, so a
    member name can never point outside of location.
    """
    def member_name(name):
        name = name.replace('\\', '/')
        while name.startswith('./') or name.startswith('/'):
            name = name[1:]
        return name in BACKUP_FILES and name or None

    def write(name, src):
        with open(os.path.join(location, name), 'wb') as dst:
            shutil.copyfileobj(src, dst)

    extracted = []

    if zipfile.is_zipfile(fileobj):
        with zipfile.ZipFile(fileobj) as dump:
            for info in dump.infolist():
                name = member_name(info.filename)
                if name and not info.is_dir():
                    with dump.open(info) as src:
                        write(name, src)
                    extracted.append(name)
    else:
        fileobj.seek(0)
        try:
            dump = tarfile.open(fileobj=fileobj, mode='r:*')
        except tarfile.TarError:
            raise ValueError(_('The file is not a zip or tar.gz backup.'))

        with dump:
            for info in dump.getmembers():
                name = member_name(info.name)
                if name and info.isfile():
                    write(name, dump.extractfile(info))
                    extracted.append(name)

    if not 'backup.inf' in extracted:
        raise ValueError(_('The file is not an OSQA backup (backup.inf is missing).'))

    return extracted


def read_inf(location):
    inf = configparser.RawConfigParser()

    with open(os.path.join(location, 'backup.inf'), 'r') as inffile:
        inf.read_file(inffile)

    return inf


def check_backup(location):
    inf = read_inf(location)

    if inf.has_option(OPTIONS_INF_SECTION, 'anon-data') and inf.get(OPTIONS_INF_SECTION, 'anon-data') == 'True':
        raise ValueError(_('Backups with anonymized data cannot be imported.'))


FILE_HANDLERS = []

def start_import(location, tag_merge, user):
    """Import the backup files previously extracted to location (see extract_backup)."""

    start_time = datetime.datetime.now()
    steps = [s for s in FILE_HANDLERS if os.path.exists(os.path.join(location, s['file']))]

    inf = read_inf(location)

    def count(id):
        try:
            return int(inf.get(META_INF_SECTION, id))
        except (configparser.Error, ValueError):
            return 0

    state = dict([(s['id'], {
        'status': _('Queued'), 'count': count(s['id']), 'parsed': 0
    }) for s in steps] + [
        ('overall', {
            'status': _('Starting'), 'count': sum([count(s['id']) for s in steps]), 'parsed': 0
        })
    ])

    full_state = dict(running=True, state=state, time_started="")

    def set_state():
        full_state['time_started'] = diff_date(start_time)
        cache.set(CACHE_KEY, full_state)

    set_state()

    def ping_state(name):
        state[name]['parsed'] += 1
        state['overall']['parsed'] += 1
        set_state()

    data = {
        'is_merge': True,
        'tag_merge': tag_merge,
        # Node states come from states.xml; older backups don't have it, so the
        # states are then rebuilt from the actions.
        'has_states': os.path.exists(os.path.join(location, 'states.xml')),
    }

    def run(step):
        name = step['id']

        def ping():
            ping_state(name)

        state['overall']['status'] = _('Importing %s') % step['name']
        state[name]['status'] = _('Importing')


        step['fn'](location, user, ping, data)

        state[name]['status'] = _('Done')

        set_state()

    try:
        check_backup(location)

        with transaction.atomic():
            for s in steps:
                run(s)

            rebuild_states(data)
            update_badge_counts(data)

        settings.MERGE_MAPPINGS.set_value(dict(merged_nodes=data.get('nodes_map', {}),
                                               merged_users=data.get('users_map', {})))

        # Rows were written without the models' save(), which keeps the object
        # and query caches up to date.
        cache.clear()

        full_state['running'] = False
        full_state['errors'] = False
        state['overall']['status'] = _('Done')
        set_state()
        return True
    except Exception as e:
        full_state['running'] = False
        full_state['errors'] = "%s: %s" % (e.__class__.__name__, str(e))
        set_state()

        import traceback
        logging.error("Error executing xml import: \n %s" % (traceback.format_exc()))
        return False

def file_handler(file_name, root_tag, el_tag, name, args_handler=None, pre_callback=None, post_callback=None):
    def decorator(fn):
        def decorated(location, current_user, ping, data):
            if pre_callback:
                pre_callback(current_user, data)

            if (args_handler):
                args = args_handler(current_user, data)
            else:
                args = []

            parser = make_parser()
            handler = TableHandler(root_tag, el_tag, fn, args, ping)
            parser.setContentHandler(handler)
            #parser.setErrorHandler(SaxErrorHandler())

            parser.parse(os.path.join(location, file_name))

            if post_callback:
                post_callback(current_user, data)

        FILE_HANDLERS.append(dict(id=root_tag, name=name, file=file_name, fn=decorated))
        return decorated
    return decorator

def verify_existence(row):
    email = row.getc('email')

    if email:
        try:
            return User.objects.get(email=email)
        except (User.DoesNotExist, User.MultipleObjectsReturned):
            pass

    for key in row.get('authKeys').get_list('key'):
        key = key.getc('key')

        if not ("google.com" in key or "yahoo.com" in key):
            try:
                return AuthKeyUserAssociation.objects.get(key=key).user
            except AuthKeyUserAssociation.DoesNotExist:
                pass

    return None

def user_import_pre_callback(user, data):
    data['users_map'] = {}
    data['marked_tags'] = []

SUBSCRIPTION_FIELDS = set([f.name for f in SubscriptionSettings._meta.fields])

@file_handler('users.xml', 'users', 'user', _lazy('Users'), pre_callback=user_import_pre_callback,
              args_handler=lambda u, d: [u, d['is_merge'], d['users_map'], d['marked_tags']])
def user_import(row, current_user, is_merge, users_map, marked_tags):
    existent = is_merge and verify_existence(row) or None

    roles = row.get('roles').get_listc('role')
    valid_email = row.get('email').get_attr('validated').as_bool()
    badges = row.get('badges')

    if existent:
        user = existent

        user.reputation += row.get('reputation').as_int()
        user.gold += badges.get_attr('gold').as_int()
        user.silver += badges.get_attr('silver').as_int()
        user.bronze += badges.get_attr('bronze').as_int()

    else:
        username = row.getc('username')

        if is_merge:
            username_count = 0

            while User.objects.filter(username=username).exists():
                username_count += 1
                username = "%s %s" % (row.getc('username'), username_count)

        user = User(
                id           = (not is_merge) and row.getc('id') or None,
                username     = username,
                password     = row.getc('password'),
                email        = row.getc('email'),
                email_isvalid= valid_email,
                is_superuser = (not is_merge) and 'superuser' in roles,
                is_staff     = ('moderator' in roles) or (is_merge and 'superuser' in roles),
                is_active    = row.get('active').as_bool(),
                date_joined  = row.get('joindate').as_datetime(),
                about         = row.getc('bio'),
                date_of_birth = row.get('birthdate').as_date(None),
                website       = row.getc('website'),
                reputation    = row.get('reputation').as_int(),
                gold          = badges.get_attr('gold').as_int(),
                silver        = badges.get_attr('silver').as_int(),
                bronze        = badges.get_attr('bronze').as_int(),
                real_name     = row.getc('realname'),
                location      = row.getc('location'),
        )

    raw_save(user)

    users_map[row.get('id').as_int()] = user.id

    authKeys = row.get('authKeys')

    for key in authKeys.get_list('key'):
        if (not is_merge) or AuthKeyUserAssociation.objects.filter(key=key.getc('key')).count() == 0:
            AuthKeyUserAssociation(user=user, key=key.getc('key'), provider=key.getc('provider')).save()

    if not existent:
        notifications = row.get('notifications')

        if notifications:
            attributes = dict([(str(k), v.as_bool() and 'i' or 'n') for k, v in notifications.get('notify').attrs.items()])
            attributes.update(dict([(str(k), v.as_bool()) for k, v in notifications.get('autoSubscribe').attrs.items()]))
            attributes.update(dict([(str("notify_%s" % k), v.as_bool()) for k, v in notifications.get('notifyOnSubscribed').attrs.items()]))
            attributes['send_digest'] = notifications.getc('digest') != 'off'

            # Attribute names are lower case after parsing; keep the ones this version has.
            attributes = dict([(k, v) for k, v in attributes.items() if k in SUBSCRIPTION_FIELDS])

            ss = SubscriptionSettings(user=user, enable_notifications=notifications.get_attr('enabled').as_bool(), **attributes)
        else:
            ss = SubscriptionSettings(user=user)

        ss.save()

    for reason, el in (('good', 'watchedTags'), ('bad', 'rejectedTags')):
        tags = row.get(el)
        if tags:
            for name in tags.get_listc('tag'):
                marked_tags.append((user.id, name, reason))


def pre_tag_import(user, data):
    data['tag_mappings'] = dict([ (t.name, t) for t in Tag.objects.all() ])
    data['tag_synonyms'] = []

def post_tag_import(user, data):
    tags = data['tag_mappings']
    tag_merge = data['tag_merge']

    for user_id, name, reason in data['marked_tags']:
        tag = tags.get(tag_merge and tag_merge.get(name, name) or name)

        if tag and not MarkedTag.objects.filter(user_id=user_id, tag=tag).exists():
            MarkedTag(user_id=user_id, tag=tag, reason=reason).save()

    from forum.models import TagSynonym
    for source_name, target_name in data['tag_synonyms']:
        target = Tag.objects.filter(name=target_name).first()
        if target and source_name != target_name and not TagSynonym.objects.filter(source_name=source_name).exists():
            TagSynonym(source_name=source_name, target=target).save()


@file_handler('tags.xml', 'tags', 'tag', _lazy('Tags'), pre_callback=pre_tag_import, post_callback=post_tag_import,
              args_handler=lambda u, d: [u, d['is_merge'], d['tag_merge'], d['users_map'], d['tag_mappings'], d['tag_synonyms']])
def tag_import(row, current_user, is_merge, tag_merge, users_map, tag_mappings, tag_synonyms):
    created_by = row.get('author').as_int(None)
    created_by = users_map.get(created_by, current_user.id)

    tag_name = row.getc('name')
    tag_name = tag_merge and tag_merge.get(tag_name, tag_name) or tag_name

    if is_merge and tag_name in tag_mappings:
        tag = tag_mappings[tag_name]
        tag.used_count += row.get('used').as_int()
    else:
        tag = Tag(name=tag_name, used_count=row.get('used').as_int(), created_by_id=created_by)
        tag_mappings[tag.name] = tag

    description = row.getc('description')
    if description and not tag.description:
        tag.description = description

    synonyms = row.get('synonyms')
    if synonyms:
        tag_synonyms.extend((name, tag_name) for name in synonyms.get_listc('synonym'))

    raw_save(tag)

def pre_node_import(user, data):
    data['nodes_map'] = {}
    data['node_refs'] = []

def post_node_import(user, data):
    # Parents and accepted answers can come after the node that refers to them.
    nodes_map = data['nodes_map']

    for node_id, parent, abs_parent, extra_ref in data['node_refs']:
        Node.objects.filter(id=node_id).update(
            parent_id=nodes_map.get(parent), abs_parent_id=nodes_map.get(abs_parent), extra_ref_id=nodes_map.get(extra_ref))

@file_handler('nodes.xml', 'nodes', 'node', _lazy('Nodes'), pre_callback=pre_node_import, post_callback=post_node_import,
              args_handler=lambda u, d: [u, d['is_merge'], d['tag_merge'], d['tag_mappings'], d['nodes_map'], d['users_map'], d['node_refs']])
def node_import(row, current_user, is_merge, tag_merge, tags, nodes_map, users_map, node_refs):

    ntags = []

    for t in row.get('tags').get_list('tag'):
        t = t.content()
        tag = tags.get(tag_merge and tag_merge.get(t, t) or t)
        if tag:
            ntags.append(tag)

    author = users_map.get(row.get('author').as_int(), current_user.id)

    last_act = row.get('lastactivity')
    last_act_user = last_act.get('by').as_int(None)

    parent = row.get('parent').as_int(None)
    abs_parent = row.get('absparent').as_int(None)
    extra_ref = row.get('extraRef').as_int(None)

    node = Node(
            id            = (not is_merge) and row.getc('id') or None,
            node_type     = row.getc('type'),
            author_id     = author,
            added_at      = row.get('date').as_datetime(),
            score         = row.get('score').as_int(0),

            last_activity_by_id = users_map.get(last_act_user),
            last_activity_at    = last_act.get('at').as_datetime(None),

            title         = row.getc('title'),
            body          = row.getc('body'),
            tagnames      = " ".join([t.name for t in ntags]),

            marked        = row.get('marked').as_bool(),
            extra_count   = row.get('extraCount').as_int(0),
            extra         = row.get('extraData').as_pickled()
    )

    raw_save(node)

    nodes_map[row.get('id').as_int()] = node.id

    if parent or abs_parent or extra_ref:
        node_refs.append((node.id, parent, abs_parent, extra_ref))

    node.tags.set(ntags)

    revisions = row.get('revisions')
    active = revisions.get_attr('active').as_int()

    if active == 0:
        active = NodeRevision(
            author_id = node.author_id,
            body = row.getc('body'),
            node = node,
            revised_at = row.get('date').as_datetime(),
            revision = 1,
            summary = _('Initial revision'),
            tagnames = " ".join([t.name for t in ntags]),
            title = row.getc('title'),
        )

        raw_save(active)
    else:
        for r in revisions.get_list('revision'):
            author = r.get('author').as_int()

            rev = NodeRevision(
                author_id = users_map.get(author, node.author_id),
                body = r.getc('body'),
                node = node,
                revised_at = r.get('date').as_datetime(),
                revision = r.get('number').as_int(),
                summary = r.getc('summary'),
                tagnames = " ".join([t.strip() for t in r.getc('tags').split(',') if t.strip()]),
                title = r.getc('title'),
            )

            raw_save(rev)
            if rev.revision == active:
                active = rev

    if isinstance(active, NodeRevision):
        Node.objects.filter(id=node.id).update(active_revision=active)

POST_ACTION = {}

def post_action(*types):
    def decorator(fn):
        for t in types:
            POST_ACTION[t] = fn
        return fn
    return decorator

def pre_action_import_callback(user, data):
    data['actions_map'] = {}
    data['persisted_votes'] = set()
    data['persisted_flags'] = set()

@file_handler('actions.xml', 'actions', 'action', _lazy('Actions'),
              pre_callback=pre_action_import_callback, args_handler=lambda u, d: [u, d['nodes_map'], d['users_map'], d['actions_map'], d])
def actions_import(row, current_user, nodes, users, actions_map, data):
    node = row.get('node').as_int(None)
    user = row.get('user').as_int()
    real_user = row.get('realUser').as_int(None)

    action = Action(
        #id           = row.get('id').as_int(),
        action_type  = row.getc('type'),
        action_date  = row.get('date').as_datetime(),
        node_id      = nodes.get(node),
        user_id      = users.get(user, current_user.id),
        real_user_id = users.get(real_user),
        ip           = row.getc('ip'),
        extra        = row.get('extraData').as_pickled(),
    )

    canceled = row.get('canceled')
    if canceled.get_attr('state').as_bool():
        by = canceled.get('user')
        by = by and by.as_int(None)
        action.canceled = True
        action.canceled_by_id = users.get(by, action.user_id)
        action.canceled_at = canceled.get('date') and canceled.get('date').as_datetime(None)
        action.canceled_ip = canceled.getc('ip')

    raw_save(action)

    actions_map[row.get('id').as_int()] = action.id

    reputes = row.get('reputes')

    for r in reputes and reputes.get_list('repute') or []:
        by_canceled = r.get_attr('byCanceled').as_bool()
        repute_user = users.get(r.get('user').as_int())

        if repute_user is None:
            continue

        # The users' reputation was imported with them, so these are only history.
        raw_save(ActionRepute(
            action = action,
            user_id = repute_user,
            value = r.get('value').as_int(),

            date = by_canceled and action.canceled_at or action.action_date,
            by_canceled = by_canceled
        ))

    if (not action.canceled) and action.node_id and (action.action_type in POST_ACTION):
        POST_ACTION[action.action_type](row, action, data)




@post_action('voteup', 'votedown', 'voteupcomment')
def vote_action(row, action, data):
    # Check to see if the vote has already been registered.
    if not (action.user_id, action.node_id) in data['persisted_votes']:
        # Persist the vote action.
        Vote(user_id=action.user_id, node_id=action.node_id, action=action,
                 voted_at=action.action_date, value=(action.action_type != 'votedown') and 1 or -1).save()

        # Record the vote action.  This will help us avoid duplicates.
        data['persisted_votes'].add((action.user_id, action.node_id))


def state_action(state):
    def fn(row, action, data):
        if data['has_states'] or NodeState.objects.filter(state_type = state, node = action.node_id).count():
            return

        NodeState(
            state_type = state,
            node_id = action.node_id,
            action = action
        ).save()
    return fn

post_action('wikify')(state_action('wiki'))
post_action('delete')(state_action('deleted'))
post_action('acceptanswer')(state_action('accepted'))
post_action('publish')(state_action('published'))
post_action('close')(state_action('closed'))


@post_action('flag')
def flag_action(row, action, data):
    if not (action.user_id, action.node_id) in data['persisted_flags']:
        reason = action.extra
        Flag(user_id=action.user_id, node_id=action.node_id, action=action, flagged_at=action.action_date,
             reason=isinstance(reason, str) and reason[:300] or "").save()
        data['persisted_flags'].add((action.user_id, action.node_id))


@file_handler('states.xml', 'states', 'state', _lazy('Node states'), args_handler=lambda u, d: [d['nodes_map'], d['actions_map']])
def states_import(row, nodes, actions):
    node = nodes.get(row.get('node').as_int())
    action = actions.get(row.get('trigger').as_int())
    state_type = row.getc('type')

    if node and action and not NodeState.objects.filter(state_type=state_type, node=node).exists():
        NodeState(state_type=state_type, node_id=node, action_id=action).save()


def chunks(items, size=500):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def rebuild_states(data):
    state_strings = {}

    for ids in chunks(data.get('nodes_map', {}).values()):
        with_state = NodeState.objects.filter(node_id__in=ids).values_list('node_id', 'state_type').order_by('id')

        for node_id, state_type in with_state:
            state_strings[node_id] = state_strings.get(node_id, '') + "(%s)" % state_type

    for node_id, state_string in state_strings.items():
        Node.objects.filter(id=node_id).update(state_string=state_string)


def award_import_args(user, data):
    data['awarded_badges'] = set()
    return [ dict([ (b.cls, b) for b in Badge.objects.all() ]) , data['nodes_map'], data['users_map'], data['actions_map'],
             data['awarded_badges']]


@file_handler('awards.xml', 'awards', 'award', _lazy('Awards'), args_handler=award_import_args)
def awards_import(row, badges, nodes, users, actions, awarded_badges):
    badge_type = badges.get(row.getc('badge'), None)

    if not badge_type:
        return

    action = actions.get(row.get('action').as_int(None))
    trigger = actions.get(row.get('trigger').as_int(None))
    node = nodes.get(row.get('node').as_int(None))
    user = users.get(row.get('user').as_int())

    if action is None or user is None:
        return

    if Award.objects.filter(badge=badge_type, user=user, node=node).count():
        return

    awarded_at = row.get('date')
    awarded_at = awarded_at and awarded_at.as_datetime(None) or Action.objects.filter(id=action).values_list(
        'action_date', flat=True)[0]

    Award(
        user_id = user,
        badge = badge_type,
        node_id = node,
        action_id = action,
        trigger_id = trigger,
        awarded_at = awarded_at,
    ).save()

    awarded_badges.add(badge_type.id)


def update_badge_counts(data):
    for badge in Badge.objects.filter(id__in=data.get('awarded_badges', ())):
        Badge.objects.filter(id=badge.id).update(awarded_count=badge.awards.count())


#@file_handler('settings.xml', 'settings', 'setting', _('Settings'))
def settings_import(row):
    KeyValue(key=row.getc('key'), value=row.get('value').as_pickled())
