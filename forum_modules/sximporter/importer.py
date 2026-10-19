
from datetime import datetime
import time
import re
import os
import gc
import logging
from django.utils.translation import gettext as _

from django.core.cache import cache
from django.db import models, transaction
from django.utils.encoding import force_str

from base64 import b64decode

from xml.sax import make_parser
from xml.sax.handler import ContentHandler

from forum import settings
from forum.models import User, AuthKeyUserAssociation, SubscriptionSettings, Tag, Node, NodeRevision, NodeState, \
    Action, ActionRepute, Vote, Flag, Badge, Award, KeyValue


def raw_save(obj):
    # Store the row as it is in the dump: the models' own save() methods would
    # create revisions, count tags, give reputation, run action hooks, etc.
    models.Model.save(obj)


class SXTableHandler(ContentHandler):
    def __init__(self, fname, callback):
        self.in_row = False
        self.el_data = {}
        self.ch_data = ''

        self.fname = fname.lower()
        self.callback = callback

    def startElement(self, name, attrs):
        if name.lower() == self.fname:
            pass
        elif name.lower() == "row":
            self.in_row = True
        self.ch_data = ''

    def characters(self, ch):
        self.ch_data += ch

    def endElement(self, name):
        if name.lower() == self.fname:
            pass
        elif name.lower() == "row":
            self.callback(self.el_data)

            self.in_row = False
            del self.el_data
            self.el_data = {}
        elif self.in_row:
            self.el_data[name.lower()] = self.ch_data.strip()
            del self.ch_data
            self.ch_data = ''


class DumpError(ValueError):
    """The dump can't be imported; the message is shown to the user."""


def readTable(path, name, callback, required=True):
    f = os.path.join(path, "%s.xml" % name)

    if not os.path.exists(f):
        if required:
            raise DumpError(_("The dump has no %s.xml file.") % name)
        logging.info("SX import: no %s.xml in the dump, skipped" % name)
        return

    parser = make_parser()
    handler = SXTableHandler(name, callback)
    parser.setContentHandler(handler)

    parser.parse(f)


msstrip = re.compile(r'^(.*)\.\d+')
def readTime(ts):
    noms = msstrip.match(ts)
    if noms:
        ts = noms.group(1)

    return datetime(*time.strptime(ts, '%Y-%m-%dT%H:%M:%S')[0:6])

google_accounts_lookup = re.compile(r'^https?://www.google.com/accounts/')
yahoo_accounts_lookup = re.compile(r'^https?://me.yahoo.com/a/')

openid_lookups = [
        re.compile(r'^https?://www.google.com/profiles/(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://me.yahoo.com/(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://openid.aol.com/(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://(?P<uname>\w+(\.\w+)*).myopenid.com/?$'),
        re.compile(r'^https?://flickr.com/(\w+/)*(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://technorati.com/people/technorati/(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://(?P<uname>\w+(\.\w+)*).wordpress.com/?$'),
        re.compile(r'^https?://(?P<uname>\w+(\.\w+)*).blogspot.com/?$'),
        re.compile(r'^https?://(?P<uname>\w+(\.\w+)*).livejournal.com/?$'),
        re.compile(r'^https?://claimid.com/(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://(?P<uname>\w+(\.\w+)*).pip.verisignlabs.com/?$'),
        re.compile(r'^https?://getopenid.com/(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://[\w\.]+/(\w+/)*(?P<uname>\w+(\.\w+)*)/?$'),
        re.compile(r'^https?://(?P<uname>[\w\.]+)/?$'),
        ]

def final_username_attempt(sxu):
    openid = sxu.get('openid', None)

    if openid:
        if google_accounts_lookup.search(openid):
            return UnknownGoogleUser(sxu.get('id'))
        if yahoo_accounts_lookup.search(openid):
            return UnknownYahooUser(sxu.get('id'))

        for lookup in openid_lookups:
            if lookup.search(openid):
                return lookup.search(openid).group('uname')

    return UnknownUser(sxu.get('id'))

class UnknownUser:
    def __init__(self, id):
        self._id = id

    def __str__(self):
        return _("user-%(id)s") % {'id': self._id}

    def encode(self, *args):
        return self.__str__()

class UnknownGoogleUser(UnknownUser):
    def __str__(self):
        return _("user-%(id)s (google)") % {'id': self._id}

class UnknownYahooUser(UnknownUser):
    def __str__(self):
        return _("user-%(id)s (yahoo)") % {'id': self._id}


class IdMapper(dict):

    def __init__(self):
        self.default = 1

    def __getitem__(self, key):
        key = int(key)
        return super().get(key, self.default)

    def __setitem__(self, key, value):
        super().__setitem__(int(key), int(value))

def unique_username(username, usernames):
    username = username[:30]
    inc = 0

    while username in usernames or User.objects.filter(username=username).exists():
        inc += 1
        username = "%s %d" % (username[:29 - len(str(inc))], inc)

    return username

openidre = re.compile(r'^https?\:\/\/')
def userimport(path, options):

    usernames = set()
    openids = set()
    uidmapper = IdMapper()

    authenticated_user = options['authenticated_user']
    owneruid = options.get('owneruid', None)
    #check for empty values
    if not owneruid:
        owneruid = None
    else:
        owneruid = int(owneruid)

    # Posts of unknown or deleted users go to the user who runs the import.
    uidmapper.default = authenticated_user.id
    uidmapper[-1] = authenticated_user.id

    def callback(sxu):
        create = True

        if sxu.get('id') == '-1':
            return
        #print "\n".join(["%s : %s" % i for i in sxu.items()])

        if owneruid and (int(sxu.get('id')) == owneruid):
            # The importing user's own SX account.
            osqau = User.objects.get(id=authenticated_user.id)

            for assoc in AuthKeyUserAssociation.objects.filter(user=osqau):
                openids.add(assoc.key)

            create = False

        sxbadges = sxu.get('badgesummary', None)
        badges = {'1':'0', '2':'0', '3':'0'}

        if sxbadges:
            badges.update(dict([b.split('=') for b in sxbadges.split()]))

        if create:
            username = unique_username(str(sxu.get('displayname',
                               sxu.get('displaynamecleaned', sxu.get('realname', final_username_attempt(sxu))))), usernames)

            osqau = User(
                    username     = username,
                    password     = '!',
                    email        = sxu.get('email', ''),
                    is_superuser = sxu.get('usertypeid') == '5',
                    is_staff     = sxu.get('usertypeid') == '4',
                    is_active    = True,
                    date_joined  = readTime(sxu.get('creationdate')),
                    last_seen    = readTime(sxu.get('lastaccessdate', sxu.get('creationdate'))),
                    about         = sxu.get('aboutme', ''),
                    date_of_birth = sxu.get('birthday', None) and readTime(sxu['birthday']) or None,
                    email_isvalid = int(sxu.get('usertypeid', 0)) > 2,
                    website       = sxu.get('websiteurl', '')[:200],
                    reputation    = int(sxu.get('reputation', 1)),
                    gold          = int(badges['1']),
                    silver        = int(badges['2']),
                    bronze        = int(badges['3']),
                    real_name     = sxu.get('realname', '')[:30],
                    location      = sxu.get('location', '')[:100],
                    )

            raw_save(osqau)

            user_joins = Action(
                    action_type = "userjoins",
                    action_date = osqau.date_joined,
                    user = osqau
                    )
            raw_save(user_joins)

            rep = ActionRepute(
                    value = 1,
                    user = osqau,
                    date = osqau.date_joined,
                    action = user_joins
                    )
            raw_save(rep)

            if not SubscriptionSettings.objects.filter(user=osqau).exists():
                s = SubscriptionSettings(user=osqau)
                s.save()

        else:
            new_about = sxu.get('aboutme', None)
            if new_about and osqau.about != new_about:
                if osqau.about:
                    osqau.about = "%s\n|\n%s" % (osqau.about, new_about)
                else:
                    osqau.about = new_about

            username = str(sxu.get('displayname',
                               sxu.get('displaynamecleaned', sxu.get('realname', final_username_attempt(sxu)))))[:30]
            if username != osqau.username:
                osqau.username = unique_username(username, usernames)
            osqau.email = sxu.get('email', osqau.email)
            osqau.reputation += int(sxu.get('reputation', 0))
            osqau.gold += int(badges['1'])
            osqau.silver += int(badges['2'])
            osqau.bronze += int(badges['3'])

            osqau.date_joined = readTime(sxu.get('creationdate'))
            osqau.website = sxu.get('websiteurl', '')[:200]
            osqau.date_of_birth = sxu.get('birthday', None) and readTime(sxu['birthday']) or None
            osqau.location = sxu.get('location', '')[:100]
            osqau.real_name = sxu.get('realname', '')[:100]

            #merged_users.append(osqau.id)
            raw_save(osqau)

        uidmapper[sxu.get('id')] = osqau.id
        usernames.add(osqau.username)

        openid = sxu.get('openid', None)
        if openid and openidre.match(openid) and (openid not in openids) and \
                not AuthKeyUserAssociation.objects.filter(key=openid).exists():
            assoc = AuthKeyUserAssociation(user=osqau, key=openid, provider="openidurl")
            assoc.save()
            openids.add(openid)

        openidalt = sxu.get('openidalt', None)
        if openidalt and openidre.match(openidalt) and (openidalt not in openids) and \
                not AuthKeyUserAssociation.objects.filter(key=openidalt).exists():
            assoc = AuthKeyUserAssociation(user=osqau, key=openidalt, provider="openidurl")
            assoc.save()
            openids.add(openidalt)

    readTable(path, "Users", callback)

    return uidmapper

def tagsimport(dump, uidmap):

    tagmap = dict([(t.name, t) for t in Tag.objects.all()])

    def callback(sxtag):
        otag = tagmap.get(sxtag['name'])

        if otag:
            otag.used_count += int(sxtag.get('count', 0))
        else:
            otag = Tag(
                    name = sxtag['name'],
                    used_count = int(sxtag.get('count', 0)),
                    created_by_id = uidmap[sxtag.get('userid', -1)],
                    )
        raw_save(otag)

        tagmap[otag.name] = otag

    readTable(dump, "Tags", callback)

    return tagmap

def add_post_state(name, post, action):
    if "(%s)" % name not in post.state_string:
        post.state_string = "%s(%s)" % (post.state_string, name)
        raw_save(post)

    try:
        state = NodeState.objects.get(node=post, state_type=name)
        state.action = action
        state.save()
    except NodeState.DoesNotExist:
        state = NodeState(node=post, state_type=name, action=action)
        state.save()

def remove_post_state(name, post):
    NodeState.objects.filter(state_type=name, node=post).delete()

    if "(%s)" % name in post.state_string:
        post.state_string = "".join("(%s)" % s for s in re.findall(r'\w+', post.state_string) if s != name)
        raw_save(post)

class PostMap:
    """Maps the posts and comments of the dump to the new node ids."""

    def __init__(self):
        self.ids = {}
        self.abs_parents = {}

    def __contains__(self, sxid):
        return int(sxid) in self.ids

    def __getitem__(self, sxid):
        return self.ids[int(sxid)]

    def add(self, sxid, node_id, abs_parent_id):
        self.ids[int(sxid)] = node_id
        self.abs_parents[node_id] = abs_parent_id

    def node_ids(self):
        return list(self.ids.values())

def postimport(dump, uidmap, tagmap):
    posts = PostMap()
    pending_parents = []

    def callback(sxpost):
        is_question = sxpost.get('posttypeid') == '1'

        post = Node(
                node_type = is_question and "question" or "answer",
                added_at = readTime(sxpost['creationdate']),
                body = sxpost['body'],
                score = int(sxpost.get('score', 0)),
                author_id = sxpost.get('deletiondate', None) and uidmap.default or uidmap[sxpost.get('owneruserid', -1)]
                )

        if is_question:
            post.title = sxpost.get('title', '')[:300]

            tagnames = sxpost.get('tags', '').replace('ö', '-').replace('é', '').replace('à', '')
            post.tagnames = tagnames

            post.extra_count = int(sxpost.get('viewcount', 0))

        if sxpost.get('lastactivityuserid', None):
            post.last_activity_by_id = uidmap[sxpost['lastactivityuserid']]
            post.last_activity_at = readTime(sxpost['lastactivitydate'])

        raw_save(post)

        create_action = Action(
                action_type = is_question and "ask" or "answer",
                user_id = post.author_id,
                node = post,
                action_date = post.added_at
                )

        raw_save(create_action)

        if sxpost.get('lasteditoruserid', None):
            revise_action = Action(
                    action_type = "revise",
                    user_id = uidmap[sxpost.get('lasteditoruserid')],
                    node = post,
                    action_date = readTime(sxpost['lasteditdate']),
                    )

            raw_save(revise_action)
            post.last_edited = revise_action

        if sxpost.get('communityowneddate', None):
            wikify_action = Action(
                    action_type = "wikify",
                    user_id = uidmap.default,
                    node = post,
                    action_date = readTime(sxpost['communityowneddate'])
                    )

            raw_save(wikify_action)
            add_post_state("wiki", post, wikify_action)

        if is_question:
            add_tags_to_post(post, tagmap)
            posts.add(sxpost['id'], post.id, post.id)
        else:
            # Questions usually come first; link the rest when all posts are in.
            pending_parents.append((post, sxpost['id'], sxpost.get('parentid')))

        raw_save(post)

        create_and_activate_revision(post)

        del post

    readTable(dump, "Posts", callback)

    for post, sxid, parentid in pending_parents:
        if parentid and parentid in posts:
            post.parent_id = post.abs_parent_id = posts[parentid]
            raw_save(post)
        posts.add(sxid, post.id, post.parent_id)

    return posts

def comment_import(dump, uidmap, posts):
    mapping = {}

    def callback(sxc):
        if sxc.get('postid') not in posts:
            return

        parent_id = posts[sxc['postid']]

        oc = Node(
                node_type = "comment",
                added_at = readTime(sxc['creationdate']),
                author_id = uidmap[sxc.get('userid', -1)],
                body = sxc['text'],
                parent_id = parent_id,
                abs_parent_id = posts.abs_parents.get(parent_id) or parent_id
                )

        if sxc.get('deletiondate', None):
            delete_action = Action(
                    action_type = "delete",
                    user_id = uidmap[sxc.get('deletionuserid', -1)],
                    action_date = readTime(sxc['deletiondate'])
                    )

            oc.author_id = uidmap[sxc.get('deletionuserid', -1)]
            raw_save(oc)

            delete_action.node = oc
            raw_save(delete_action)

            add_post_state("deleted", oc, delete_action)
        else:
            oc.author_id = uidmap[sxc.get('userid', -1)]
            raw_save(oc)

        create_action = Action(
                action_type = "comment",
                user_id = oc.author_id,
                node = oc,
                action_date = oc.added_at
                )

        create_and_activate_revision(oc)

        raw_save(create_action)

        mapping[int(sxc['id'])] = int(oc.id)

    readTable(dump, "PostComments", callback, required=False)
    return mapping


def add_tags_to_post(post, tagmap):
    tags = [tag for tag in [tagmap.get(name.strip()) for name in post.tagnames.split(' ') if name] if tag]
    post.tagnames = " ".join([t.name for t in tags]).strip()[:125]
    post.tags.set(tags)


def create_and_activate_revision(post):
    rev = NodeRevision(
            author_id = post.author_id,
            body = post.body,
            node_id = post.id,
            revised_at = post.added_at,
            revision = 1,
            summary = 'Initial revision',
            tagnames = post.tagnames,
            title = post.title,
            )

    raw_save(rev)
    post.active_revision_id = rev.id
    raw_save(post)

def post_vote_import(dump, uidmap, posts):
    close_reasons = {}

    def close_callback(r):
        close_reasons[r['id']] = r['name']

    readTable(dump, "CloseReasons", close_callback, required=False)

    user2vote = set()
    user2flag = set()

    def callback(sxv):
        action = Action(
                user_id=uidmap[sxv.get('userid', -1)],
                action_date = readTime(sxv['creationdate']),
                )

        if sxv['postid'] not in posts: return
        node = Node.objects.get(id=posts[sxv['postid']])
        action.node = node

        if sxv['votetypeid'] == '1':
            answer = node

            action.action_type = "acceptanswer"
            raw_save(action)

            answer.marked = True
            raw_save(answer)

            if answer.parent_id:
                Node.objects.filter(id=answer.parent_id).update(extra_ref=answer.id)

        elif sxv['votetypeid'] in ('2', '3'):
            if (action.node.id, action.user_id) not in user2vote:
                user2vote.add((action.node.id, action.user_id))

                action.action_type = (sxv['votetypeid'] == '2') and "voteup" or "votedown"
                raw_save(action)

                ov = Vote(
                        node_id = action.node.id,
                        user_id = action.user_id,
                        voted_at = action.action_date,
                        value = sxv['votetypeid'] == '2' and 1 or -1,
                        action = action
                        )
                ov.save()
            else:
                action.action_type = "unknown"
                raw_save(action)

        elif sxv['votetypeid'] in ('4', '12', '13'):
            action.action_type = "flag"
            raw_save(action)

            if (action.node.id, action.user_id) not in user2flag:
                user2flag.add((action.node.id, action.user_id))

                of = Flag(
                        node = action.node,
                        user_id = action.user_id,
                        flagged_at = action.action_date,
                        reason = '',
                        action = action
                        )

                of.save()

        elif sxv['votetypeid'] == '5':
            action.action_type = "favorite"
            raw_save(action)

        elif sxv['votetypeid'] == '6':
            action.action_type = "close"
            action.extra = close_reasons.get(sxv.get('comment'), '')
            raw_save(action)

            node.marked = True
            raw_save(node)

        elif sxv['votetypeid'] == '7':
            action.action_type = "unknown"
            raw_save(action)

            node.marked = False
            raw_save(node)

            remove_post_state("closed", node)

        elif sxv['votetypeid'] == '10':
            action.action_type = "delete"
            raw_save(action)

        elif sxv['votetypeid'] == '11':
            action.action_type = "unknown"
            raw_save(action)

            remove_post_state("deleted", node)

        else:
            action.action_type = "unknown"
            raw_save(action)

        if sxv.get('targetrepchange', None):
            rep = ActionRepute(
                    action = action,
                    date = action.action_date,
                    user_id = uidmap[sxv.get('targetuserid', -1)],
                    value = int(sxv['targetrepchange'])
                    )

            raw_save(rep)

        if sxv.get('voterrepchange', None):
            rep = ActionRepute(
                    action = action,
                    date = action.action_date,
                    user_id = uidmap[sxv.get('userid', -1)],
                    value = int(sxv['voterrepchange'])
                    )

            raw_save(rep)

        if action.action_type in ("acceptanswer", "delete", "close"):
            state = {"acceptanswer": "accepted", "delete": "deleted", "close": "closed"}[action.action_type]
            add_post_state(state, node, action)

    readTable(dump, "Posts2Votes", callback, required=False)


def comment_vote_import(dump, uidmap, comments):
    user2vote = set()
    comments2score = {}

    def callback(sxv):
        if sxv['votetypeid'] == "2" and int(sxv['postcommentid']) in comments:
            comment_id = comments[int(sxv['postcommentid'])]
            user_id = uidmap[sxv.get('userid', -1)]

            if (comment_id, user_id) not in user2vote:
                user2vote.add((comment_id, user_id))

                action = Action(
                        action_type = "voteupcomment",
                        user_id = user_id,
                        action_date = readTime(sxv['creationdate']),
                        node_id = comment_id
                        )
                raw_save(action)

                ov = Vote(
                        node_id = comment_id,
                        user_id = user_id,
                        voted_at = action.action_date,
                        value = 1,
                        action = action
                        )

                ov.save()

                if comment_id not in comments2score:
                    comments2score[comment_id] = 1
                else:
                    comments2score[comment_id] += 1

    readTable(dump, "Comments2Votes", callback, required=False)

    for cid, score in comments2score.items():
        Node.objects.filter(id=cid).update(score=score)


def badges_import(dump, uidmap):

    sxbadges = {}

    def sxcallback(b):
        sxbadges[int(b['id'])] = b

    readTable(dump, "Badges", sxcallback, required=False)

    obadges = dict([(b.cls, b) for b in Badge.objects.all()])

    sx_to_osqa = {}

    for id, sxb in sxbadges.items():
        cls = "".join(sxb['name'].replace('&', 'And').split(' '))

        if cls in obadges:
            sx_to_osqa[id] = obadges[cls]
        else:
            osqab = Badge(
                    cls = cls,
                    awarded_count = 0,
                    type = int(sxb.get('class', Badge.BRONZE))
                    )
            raw_save(osqab)
            sx_to_osqa[id] = osqab
            obadges[cls] = osqab

    awarded = {}

    def callback(sxa):
        badge = sx_to_osqa.get(int(sxa['badgeid']))

        if badge is None:
            return

        user_id = uidmap[sxa.get('userid', -1)]

        action = Action(
                action_type = "award",
                user_id = user_id,
                action_date = readTime(sxa['date'])
                )

        raw_save(action)

        # Badges awarded for the user, not for a post; several awards of the
        # same badge are allowed since NULL nodes never clash.
        osqaa = Award(
                user_id = user_id,
                badge = badge,
                node_id = None,
                awarded_at = action.action_date,
                action = action
                )

        osqaa.save()
        awarded[badge.id] = badge

    readTable(dump, "Users2Badges", callback, required=False)

    for badge in awarded.values():
        Badge.objects.filter(id=badge.id).update(awarded_count=Award.objects.filter(badge=badge).count())

def save_setting(k, v):
    try:
        kv = KeyValue.objects.get(key=k)
        kv.value = v
    except KeyValue.DoesNotExist:
        kv = KeyValue(key = k, value = v)

    kv.save()


def pages_import(dump, owner):
    registry = {}

    def callback(sxp):
        page = Node(
                node_type = "page",
                title = sxp['name'][:300],
                body = force_str(b64decode(sxp['value'])),
                extra = {
                'path': sxp['url'][1:],
                'mimetype': sxp['contenttype'],
                'template': (sxp['usemaster'] == "true") and "default" or "none",
                'render': "html",
                'sidebar': "",
                'sidebar_wrap': True,
                'sidebar_render': "html",
                'comments': False
                },
                author_id = owner
                )

        raw_save(page)
        create_and_activate_revision(page)

        registry[sxp['url'][1:]] = page.id

        create_action = Action(
                action_type = "newpage",
                user_id = page.author_id,
                node = page
                )

        raw_save(create_action)

        if sxp['active'] == "true" and sxp['contenttype'] == "text/html":
            pub_action = Action(
                    action_type = "publish",
                    user_id = page.author_id,
                    node = page
                    )

            raw_save(pub_action)
            page.marked = True
            add_post_state("published", page, pub_action)

    readTable(dump, "FlatPages", callback, required=False)

    if registry:
        current = dict(settings.STATIC_PAGE_REGISTRY.value)
        current.update(registry)
        settings.STATIC_PAGE_REGISTRY.set_value(current)

sx2osqa_set_map = {
'theme.html.name': 'APP_TITLE',
'theme.html.footer': 'CUSTOM_FOOTER',
'theme.html.sidebar': 'SIDEBAR_UPPER_TEXT',
'theme.html.sidebar-low': 'SIDEBAR_LOWER_TEXT',
'theme.html.welcome': 'APP_INTRO',
'theme.html.head': 'CUSTOM_HEAD',
'theme.html.header': 'CUSTOM_HEADER',
'theme.css': 'CUSTOM_CSS',
}

html_codes = (
('&amp;', '&'),
('&lt;', '<'),
('&gt;', '>'),
('&quot;', '"'),
('&#39;', "'"),
)

def html_decode(html):
    html = force_str(html)

    for args in html_codes:
        html = html.replace(*args)

    return html


def static_import(dump):
    sx_unknown = {}

    def callback(set):
        if str(set['name']) in sx2osqa_set_map:
            getattr(settings, sx2osqa_set_map[set['name']]).set_value(html_decode(set['value']))
        else:
            sx_unknown[set['name']] = html_decode(set['value'])

    readTable(dump, "ThemeTextResources", callback, required=False)

    if sx_unknown:
        save_setting('SXIMPORT_UNKNOWN_SETS', sx_unknown)


def sximport(dump, options):
    """Import the StackExchange 1.0 dump extracted to the folder dump.

    options['authenticated_user'] is the (super) user running the import; the
    dump user options['owneruid'] is merged into that user.
    """
    try:
        with transaction.atomic():
            uidmap = userimport(dump, options)
            tagmap = tagsimport(dump, uidmap)
            gc.collect()

            posts = postimport(dump, uidmap, tagmap)
            gc.collect()

            comments = comment_import(dump, uidmap, posts)
            gc.collect()

            post_vote_import(dump, uidmap, posts)
            gc.collect()

            comment_vote_import(dump, uidmap, comments)
            gc.collect()

            badges_import(dump, uidmap)

            pages_import(dump, uidmap.default)
            static_import(dump)
            gc.collect()
    finally:
        # Rows were written without the models' save(), which keeps the object
        # and query caches up to date (and a failed import is rolled back).
        cache.clear()
