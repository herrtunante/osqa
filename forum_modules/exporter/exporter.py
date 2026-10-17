import os
import tarfile
import datetime
import logging
import re
import configparser
import shutil
import tempfile
import zipfile

from django.core.cache import cache
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from forum.models import *
from forum.settings import APP_URL
from forum.templatetags.extra_tags import diff_date
from xml.etree import ElementTree as ET
from forum import settings
from django.conf import settings as djsettings
from . import settings as selfsettings

CACHE_KEY = "%s_exporter_state" % APP_URL
EXPORT_STEPS = []

TMP_FOLDER = os.path.join(os.path.dirname(__file__), 'tmp')

DATE_AND_AUTHOR_INF_SECTION = 'DateAndAuthor'
OPTIONS_INF_SECTION = 'Options'
META_INF_SECTION = 'Meta'

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"

# Characters that are not allowed in an XML 1.0 document.
INVALID_XML_CHARS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]')

def make_date(date, with_time=True):
    try:
        return date.strftime(with_time and DATETIME_FORMAT or DATE_FORMAT)
    except ValueError:
        return date.replace(year=1900).strftime(with_time and DATETIME_FORMAT or DATE_FORMAT)


def xml_text(value):
    return INVALID_XML_CHARS.sub('', str(value))


def add(el, tag_name, content = None, **attrs):
    tag = ET.SubElement(el, tag_name)

    if content:
        tag.text = xml_text(content)

    for k, v in attrs.items():
        tag.set(k, xml_text(v))

    return tag

GOOD_TAG_NAME = re.compile(r"^\w+$")

def make_extra(el, v):
    if v is None:
        return


    if isinstance(v, (int, str, float, bool, dict, list, tuple)):
        if isinstance(v, tuple):
            t = 'list'
        else:
            t = v.__class__.__name__
    else:
        t = 'unknown'

    value = add(el, 'value', type=t)

    if isinstance(v, (list, tuple)):
        for i in v:
            item = add(value, 'item')
            make_extra(item, i)

    elif isinstance(v, dict):
        for k, i in v.items():
            item = add(value, 'item', key=k)
            make_extra(item, i)
    else:
        value.text = xml_text(v)

def write_to_file(root, tmp, filename):
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(os.path.join(tmp, filename), encoding='UTF-8', xml_declaration=True)

def create_targz(tmp, files, start_time, options, user, state, set_state, file_format):
    now = datetime.datetime.now()
    domain = re.match(r'^\w+://([\w-]+(\.[\w-]+)*)', djsettings.APP_URL)
    if domain:
        domain = '_'.join(domain.group(1).split('.'))
    else:
        domain = 'localhost'

    fname = "%s-%s" % (domain, now.strftime('%Y%m%d%H%M%S'))
    if file_format == 'zip':
        full_fname = "%s.zip" % fname
    else:
        full_fname = "%s.tar.gz" % fname

    if file_format == 'zip':
        t = zipfile.ZipFile(os.path.join(str(selfsettings.EXPORTER_BACKUP_STORAGE), full_fname), 'w', zipfile.ZIP_DEFLATED)

        def add_to_file(f, a):
            t.write(f, a)
    else:
        t = tarfile.open(os.path.join(str(selfsettings.EXPORTER_BACKUP_STORAGE), full_fname), mode = 'w:gz')

        def add_to_file(f, a):
            t.add(f, a)

    state['overall']['status'] = _('Compressing xml files')
    set_state()

    for f in files:
        add_to_file(os.path.join(tmp, f), f)

    if options.get('uplodaded_files', False):
        state['overall']['status'] = _('Importing uploaded files')
        set_state()
        export_upfiles(t)

    if options.get('import_skins_folder', False):
        state['overall']['status'] = _('Importing skins folder')
        set_state()
        export_skinsfolder(t)

    state['overall']['status'] = _('Writing inf file.')
    set_state()

    inf = configparser.RawConfigParser()

    inf.add_section(DATE_AND_AUTHOR_INF_SECTION)

    inf.set(DATE_AND_AUTHOR_INF_SECTION, 'file-name', full_fname)
    inf.set(DATE_AND_AUTHOR_INF_SECTION, 'author', str(user.id))
    inf.set(DATE_AND_AUTHOR_INF_SECTION, 'site', djsettings.APP_URL)
    inf.set(DATE_AND_AUTHOR_INF_SECTION, 'started', start_time.strftime(DATETIME_FORMAT))
    inf.set(DATE_AND_AUTHOR_INF_SECTION, 'finished', now.strftime(DATETIME_FORMAT))

    inf.add_section(OPTIONS_INF_SECTION)
    inf.set(OPTIONS_INF_SECTION, 'anon-data', str(options.get('anon_data', False)))
    inf.set(OPTIONS_INF_SECTION, 'with-upfiles', str(options.get('uplodaded_files', False)))
    inf.set(OPTIONS_INF_SECTION, 'with-skins', str(options.get('import_skins_folder', False)))

    inf.add_section(META_INF_SECTION)

    for id, s in state.items():
        inf.set(META_INF_SECTION, id, str(s['count']))

    with open(os.path.join(tmp, 'backup.inf'), 'w') as inffile:
        inf.write(inffile)

    add_to_file(os.path.join(tmp, 'backup.inf'), 'backup.inf')
    state['overall']['status'] = _('Saving backup file')
    set_state()
    t.close()

    # A copy of the inf file next to the backup lists it in the exporter page.
    shutil.copy(os.path.join(tmp, 'backup.inf'),
                os.path.join(str(selfsettings.EXPORTER_BACKUP_STORAGE), '%s.backup.inf' % fname))
    return full_fname


def add_folder(tf, folder, arcname):
    if isinstance(tf, zipfile.ZipFile):
        # ZipFile.write() only stores the directory entry, not its contents.
        for path, dirs, files in os.walk(folder):
            for f in files:
                full = os.path.join(path, f)
                tf.write(full, os.path.join(arcname, os.path.relpath(full, folder)))
    else:
        tf.add(folder, arcname=arcname)


def export_upfiles(tf):
    folder = str(settings.UPFILES_FOLDER)

    if os.path.exists(folder):
        add_folder(tf, folder, 'upfiles')


def export_skinsfolder(tf):
    folder = djsettings.TEMPLATES[0]['DIRS'][0]

    if os.path.exists(folder):
        add_folder(tf, folder, 'skins')


def export(options, user):
    start_time = datetime.datetime.now()
    anon_data = options.get('anon_data', False)

    steps = [s for s in EXPORT_STEPS if not (anon_data and s['fn'].is_user_data())]

    state = dict([(s['id'], {
        'status': _('Queued'), 'count': s['fn'].count(start_time), 'parsed': 0
    }) for s in steps] + [
        ('overall', {
            'status': _('Starting'), 'count': sum([s['fn'].count(start_time) for s in steps]), 'parsed': 0
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

    def run(step, tmp):
        name = step['id']

        def ping():
            ping_state(name)

        state['overall']['status'] = _('Exporting %s') % step['name']
        state[name]['status'] = _('Exporting')

        root, fname = step['fn'](ping, start_time, anon_data)

        state[name]['status'] = _('Writing temp file')
        state['overall']['status'] = _('Writing %s temp file') % step['name']

        set_state()

        write_to_file(root, tmp, fname)
        state[name]['status'] = _('Done')

        set_state()

        return fname

    if not os.path.exists(TMP_FOLDER):
        os.makedirs(TMP_FOLDER)
    tmp = tempfile.mkdtemp(dir=TMP_FOLDER)

    try:
        dump_files = []

        for s in steps:
            dump_files.append(run(s, tmp))

        state['overall']['status'] = _('Compressing files')
        set_state()

        fname = create_targz(tmp, dump_files, start_time, options, user, state, set_state, options['file_format'])
        full_state['running'] = False
        full_state['errors'] = False
        state['overall']['status'] = _('Done')
        state['overall']['fname'] = fname

        set_state()
        return fname
    except Exception as e:
        full_state['running'] = False
        full_state['errors'] = "%s: %s" % (e.__class__.__name__, str(e))
        set_state()

        import traceback
        logging.error("Error executing xml backup: \n %s" % (traceback.format_exc()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exporter_step(queryset, root_tag_name, el_tag_name, name, date_lock=None, user_data=False):

    def decorator(fn):
        def qs(lock):
            if date_lock:
                return queryset.filter(**{"%s__lte" % date_lock: lock})
            return queryset

        def decorated(ping, lock, anon_data):
            root = ET.Element(root_tag_name)

            for item in qs(lock).order_by('id'):
                el = add(root, el_tag_name)
                fn(item, el, anon_data)
                ping()

            return root, "%s.xml" % root_tag_name

        def count(lock):
            return qs(lock).count()

        def is_user_data():
            return user_data

        decorated.count = count
        decorated.is_user_data = is_user_data

        EXPORT_STEPS.append(dict(id=root_tag_name, name=name, fn=decorated))

        return decorated

    return decorator

@exporter_step(Tag.objects.all(), 'tags', 'tag', _lazy('Tags'))
def export_tags(t, el, anon_data):
    add(el, 'name', t.name)
    if not anon_data:
        add(el, 'author', t.created_by_id)
    add(el, 'used', t.used_count)
    if t.description:
        add(el, 'description', t.description)
    synonyms = list(t.synonyms.values_list('source_name', flat=True))
    if synonyms:
        syn_el = add(el, 'synonyms')
        for name in synonyms:
            add(syn_el, 'synonym', name)


@exporter_step(User.objects.all(), 'users', 'user', _lazy('Users'), 'date_joined', True)
def export_users(u, el, anon_data):
    add(el, 'id', u.id)
    add(el, 'username', u.username)
    add(el, 'password', u.password)
    add(el, 'email', u.email, validated=u.email_isvalid and 'true' or 'false')
    add(el, 'reputation', u.reputation)
    add(el, 'badges', bronze=u.bronze, silver=u.silver, gold=u.gold)
    add(el, 'joindate', make_date(u.date_joined))
    add(el, 'active', u.is_active and 'true' or 'false')

    add(el, 'realname', u.real_name)
    add(el, 'bio', u.about)
    add(el, 'location', u.location)
    add(el, 'website', u.website)
    add(el, 'birthdate', u.date_of_birth and make_date(u.date_of_birth, with_time=False) or "")

    roles = add(el, 'roles')

    if u.is_superuser:
        add(roles, 'role', 'superuser')

    if u.is_staff:
        add(roles, 'role', 'moderator')

    auth = add(el, 'authKeys')
    for a in u.auth_keys.all():
        key = add(auth, 'key')
        add(key, 'provider', a.provider)
        add(key, 'key', a.key)

    try:
        ss = u.subscription_settings

        notify = add(el, 'notifications', enabled=ss.enable_notifications and 'true' or 'false')

        add(notify, 'notify', **dict([(t, ss.__dict__.get(t, 'n') == 'i' and 'true' or 'false') for t in ['member_joins', 'new_question', 'new_question_watched_tags', 'subscribed_questions']]))

        add(notify, 'autoSubscribe', **dict([(t, ss.__dict__.get(t, False) and 'true' or 'false') for t in [
                'all_questions', 'all_questions_watched_tags', 'questions_asked', 'questions_answered', 'questions_commented', 'questions_viewed']]))

        add(notify, 'notifyOnSubscribed', **dict([(t, ss.__dict__.get("notify_%s" % t, False) and 'true' or 'false') for t in [
                'answers', 'reply_to_comments', 'comments_own_post', 'comments', 'accepted']]))

        add(notify, 'digest', ss.send_digest and 'on' or 'off')
    except SubscriptionSettings.DoesNotExist:
        pass

    watched = add(el, 'watchedTags')
    rejected = add(el, 'rejectedTags')

    for m in u.tag_selections.all():
        try:
            if m.reason == 'good':
                add(watched, 'tag', m.tag.name)
            else:
                add(rejected, 'tag', m.tag.name)
        except Tag.DoesNotExist:
            pass



@exporter_step(Node.objects.all(), 'nodes', 'node', _lazy('Nodes'), 'added_at')
def export_nodes(n, el, anon_data):
    add(el, 'id', n.id)
    add(el, 'type', n.node_type)

    if not anon_data:
        add(el, 'author', n.author_id)
    add(el, 'date', make_date(n.added_at))
    add(el, 'parent', n.parent_id or "")
    add(el, 'absparent', n.abs_parent_id or "")

    act = add(el, 'lastactivity')
    add(act, 'by', n.last_activity_by_id or "")
    add(act, 'at', n.last_activity_at and make_date(n.last_activity_at) or "")

    add(el, 'title', n.title)
    add(el, 'body', n.body)

    add(el, 'score', n.score)

    tags = add(el, 'tags')

    for t in n.tagname_list():
        add(tags, 'tag', t)

    try:
        active = n.active_revision and n.active_revision.revision or n.revisions.order_by('revision')[0].revision
    except IndexError:
        active = 0

    revs = add(el, 'revisions', active=active)

    for r in n.revisions.order_by('revision'):
        rev = add(revs, 'revision')
        add(rev, 'number', r.revision)
        add(rev, 'summary', r.summary)
        if not anon_data:
            add(rev, 'author', r.author_id)
        add(rev, 'date', make_date(r.revised_at))

        add(rev, 'title', r.title)
        add(rev, 'body', r.body)
        add(rev, 'tags', ", ".join(r.tagname_list()))

    add(el, 'marked', n.marked and 'true' or 'false')
    add(el, 'wiki', n.nis.wiki and 'true' or 'false')
    add(el, 'extraRef', n.extra_ref_id or "")
    make_extra(add(el, 'extraData'), n.extra)
    add(el, 'extraCount', n.extra_count and n.extra_count or "")


@exporter_step(Action.objects.all(), 'actions', 'action', _lazy('Actions'), 'action_date')
def export_actions(a, el, anon_data):
    add(el, 'id', a.id)
    add(el, 'type', a.action_type)
    add(el, 'date', make_date(a.action_date))

    if not anon_data:
        add(el, 'user', a.user_id)
        add(el, 'realUser', a.real_user_id or "")
        add(el, 'ip', a.ip)
    add(el, 'node', a.node_id or "")

    make_extra(add(el, 'extraData'), a.extra)

    canceled = add(el, 'canceled', state=a.canceled and 'true' or 'false')

    if a.canceled:
        if not anon_data:
            add(canceled, 'user', a.canceled_by_id or "")
            add(canceled, 'ip', a.canceled_ip)

        add(canceled, 'date', a.canceled_at and make_date(a.canceled_at) or "")

    if not anon_data:
        reputes = add(el, 'reputes')

        for r in a.reputes.all():
            repute = add(reputes, 'repute', byCanceled=r.by_canceled and 'true' or 'false')
            add(repute, 'user', r.user_id)
            add(repute, 'value', r.value)


@exporter_step(NodeState.objects.all(), 'states', 'state', _lazy('Node states'), 'action__action_date')
def export_states(s, el, anon_data):
    add(el, 'type', s.state_type)
    add(el, 'node', s.node_id)
    add(el, 'trigger', s.action_id)


#@exporter_step(Badge.objects.all(), 'badges', 'badge', _('Badges'), user_data=True)
#def export_badges(b, el, anon_data):
#    el.add('type', ["", 'gold', 'silver', 'bronze'][b.type])
#    el.add('name', b.cls)
#    el.add('count', b.awarded_count)


@exporter_step(Award.objects.all(), 'awards', 'award', _lazy('Awards'), 'awarded_at', True)
def export_awards(a, el, anon_data):
    add(el, 'badge', a.badge.cls)
    add(el, 'user', a.user_id)
    add(el, 'node', a.node_id or "")
    add(el, 'trigger', a.trigger_id or "")
    add(el, 'action', a.action_id)
    add(el, 'date', make_date(a.awarded_at))

@exporter_step(KeyValue.objects.all(), 'settings', 'setting', _lazy('Settings'))
def export_settings(s, el, anon_data):
    add(el, 'key', s.key)
    make_extra(add(el, 'value'), s.value)
