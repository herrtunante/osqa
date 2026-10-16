"""Browser push notifications (forum/utils/push.py, forum/views/push.py):
off without VAPID keys, the subscribe/unsubscribe calls, the device settings,
what is pushed for which notification, and the key command."""
import base64
import io
import json
import os
import re
from unittest import mock

from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from forum import settings
from forum.actions import ApprovePostAction
from forum.models import Answer, Comment, Node, Notification, PushSubscription, Question, User
from forum.utils import push

from .base import OSQATestCase

PUBLIC_KEY, PRIVATE_KEY = push.generate_keys()
KEYS = dict(VAPID_PUBLIC_KEY=PUBLIC_KEY, VAPID_PRIVATE_KEY=PRIVATE_KEY, VAPID_SUBJECT='mailto:admin@example.org')
ENDPOINT = 'https://fcm.googleapis.com/fcm/send/device-%s'
REAL_WEBPUSH = push.webpush


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def browser_keys():
    """A browser's subscription keys: (private key, p256dh, auth)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return key, b64(public), b64(auth)


def subscription_json(name='a'):
    key, p256dh, auth = browser_keys()
    return {'endpoint': ENDPOINT % name, 'expirationTime': None, 'keys': {'p256dh': p256dh, 'auth': auth}}


class PushError(Exception):
    def __init__(self, status):
        super().__init__('push failed: %s' % status)
        self.response = mock.Mock(status_code=status)


@override_settings(**KEYS)
class PushTestCase(OSQATestCase):
    def setUp(self):
        super().setUp()
        self.alice = self.create_user('alice', email_isvalid=True, reputation=100)
        self.bob = self.create_user('bob', email_isvalid=True, reputation=100)
        self.carol = self.create_user('carol', email_isvalid=True, reputation=100)
        self.question = Question(author=self.alice, title='How do I export <survey> data to CSV & Excel?',
                                 tagnames='collect', body='A question body that is long enough, a secret detail.')
        self.question.save()
        self.pushed = []
        patcher = mock.patch('forum.utils.push.webpush', side_effect=self.fake_webpush)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.push_errors = {}

    def fake_webpush(self, subscription, data):
        status = self.push_errors.get(subscription.endpoint)
        if status:
            raise PushError(status)
        self.pushed.append((subscription.user.username, json.loads(data)))

    def device(self, user, name=None):
        data = subscription_json(name or user.username)
        return PushSubscription.objects.create(
            user=user, endpoint=data['endpoint'], endpoint_hash=push.endpoint_hash(data['endpoint']),
            p256dh=data['keys']['p256dh'], auth=data['keys']['auth'], label='Chrome on Android')

    def answer_as(self, user, text='An answer that is long enough to post, with private words.'):
        self.login(user)
        self.client.post(reverse('answer', args=[self.question.id]), {'text': text})
        return Answer.objects.filter(author=user).latest('id')

    def comment_as(self, user, post, text):
        self.login(user)
        self.command(reverse('comment', kwargs={'id': post.id}), {'comment': text})
        return Comment.objects.filter(author=user).latest('id')

    def pushed_to(self, username):
        return [payload for name, payload in self.pushed if name == username]


class OffWithoutKeysTest(PushTestCase):
    @override_settings(VAPID_PUBLIC_KEY='', VAPID_PRIVATE_KEY='')
    def test_no_ui_no_urls_no_pushes(self):
        self.device(self.alice)
        self.login(self.alice)
        self.assertNotContains(self.client.get(reverse('notifications')), 'data-push-switch')
        self.assertNotContains(self.client.get(self.alice.get_user_subscriptions_url()), 'push-settings')
        for name in ('push_subscribe', 'push_unsubscribe', 'push_settings'):
            self.assertEqual(self.client.post(reverse(name), '{}', content_type='application/json').status_code, 404)
        self.answer_as(self.bob)
        self.assertEqual(Notification.objects.filter(user=self.alice).count(), 1)
        self.assertEqual(self.pushed, [])

    def test_shown_with_keys(self):
        self.login(self.alice)
        page = self.client.get(reverse('notifications'))
        self.assertContains(page, 'data-push-switch')
        self.assertContains(page, 'data-key="%s"' % PUBLIC_KEY)
        self.assertContains(page, 'Get notifications on this device')
        self.assertContains(page, '/media/js/osqa.push.js')
        page = self.client.get(self.alice.get_user_subscriptions_url())
        self.assertContains(page, 'id="push-settings"')
        self.assertContains(page, 'value="answer_mine" checked')
        self.assertContains(page, 'value="badge" />')
        # Never someone else's devices (a superuser looking at alice's settings).
        admin = self.create_user('admin', is_superuser=True, is_staff=True, reputation=5000)
        self.login(admin)
        page = self.client.get(self.alice.get_user_subscriptions_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'push-settings')
        # Anonymous visitors: nothing (the list page needs a sign-in anyway).
        self.client.logout()
        self.assertNotContains(self.client.get('/'), 'data-push-switch')


class SubscribeEndpointTest(PushTestCase):
    def post(self, name, data, client=None, **extra):
        client = client or self.client
        return client.post(reverse(name), json.dumps(data) if not isinstance(data, str) else data,
                           content_type='application/json', **extra)

    def test_subscribe_and_unsubscribe(self):
        self.login(self.alice)
        data = subscription_json()
        response = self.post('push_subscribe', data, HTTP_USER_AGENT=(
            'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36'))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'ok': True, 'device': 'Chrome on Android'})
        device = PushSubscription.objects.get()
        self.assertEqual((device.user, device.endpoint, device.p256dh, device.auth),
                         (self.alice, data['endpoint'], data['keys']['p256dh'], data['keys']['auth']))

        # Again (the browser re-sends it): still one row.
        self.assertEqual(self.post('push_subscribe', data).status_code, 200)
        self.assertEqual(PushSubscription.objects.count(), 1)

        # The same browser, now signed in as bob: the device is bob's.
        self.login(self.bob)
        self.assertEqual(self.post('push_subscribe', data).status_code, 200)
        self.assertEqual(PushSubscription.objects.get().user, self.bob)

        # alice can't remove bob's device; bob can.
        self.login(self.alice)
        self.assertEqual(self.post('push_unsubscribe', {'endpoint': data['endpoint']}).json()['removed'], 0)
        self.login(self.bob)
        self.assertEqual(self.post('push_unsubscribe', {'endpoint': data['endpoint']}).json()['removed'], 1)
        self.assertFalse(PushSubscription.objects.exists())

    def test_sign_in_and_post_needed(self):
        response = self.post('push_subscribe', subscription_json())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post('push_unsubscribe', {'endpoint': ENDPOINT % 'a'}).status_code, 403)
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('push_subscribe')).status_code, 405)
        self.assertEqual(self.client.get(reverse('push_settings')).status_code, 405)
        self.assertFalse(PushSubscription.objects.exists())

    def test_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        self.assertEqual(self.post('push_subscribe', subscription_json(), client=client).status_code, 403)
        self.assertEqual(self.post('push_unsubscribe', {'endpoint': ENDPOINT % 'a'}, client=client).status_code, 403)
        self.assertEqual(client.post(reverse('push_settings'), {'kinds': ['badge']}).status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())
        # With the token, as osqa.push.js sends it.
        page = client.get(reverse('notifications')).content.decode()
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)
        response = self.post('push_subscribe', subscription_json(), client=client, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_validation(self):
        self.login(self.alice)
        good = subscription_json()
        bad = [
            'not json', '[]', {}, {'endpoint': good['endpoint']},
            dict(good, endpoint='http://fcm.googleapis.com/fcm/send/x'),          # not https
            dict(good, endpoint='https://127.0.0.1/admin/'),                      # not a push service
            dict(good, endpoint='https://evil.example.org/fcm.googleapis.com/x'),
            dict(good, endpoint='https://fcm.googleapis.com.evil.org/x'),
            dict(good, endpoint='https://user:pw@fcm.googleapis.com/x'),
            dict(good, endpoint='https://fcm.googleapis.com/' + 'x' * 3000),
            dict(good, keys={'p256dh': good['keys']['p256dh']}),
            dict(good, keys={'p256dh': b64(b'\x04' + b'1' * 10), 'auth': good['keys']['auth']}),
            dict(good, keys={'p256dh': good['keys']['p256dh'], 'auth': b64(b'short')}),
            dict(good, keys={'p256dh': '!!!', 'auth': good['keys']['auth']}),
            dict(good, keys='nope'),
        ]
        for data in bad:
            response = self.post('push_subscribe', data)
            self.assertEqual(response.status_code, 400, data)
            self.assertIn('error', response.json())
        self.assertFalse(PushSubscription.objects.exists())
        for endpoint in ('https://updates.push.services.mozilla.com/wpush/v2/abc',
                         'https://web.push.apple.com/QGx', 'https://wns2-par02p.notify.windows.com/w/?token=x'):
            self.assertEqual(self.post('push_subscribe', dict(good, endpoint=endpoint)).status_code, 200, endpoint)

    def test_device_limit(self):
        self.login(self.alice)
        for index in range(25):
            self.assertEqual(self.post('push_subscribe', subscription_json('n%d' % index)).status_code, 200)
        self.assertEqual(PushSubscription.objects.filter(user=self.alice).count(), 20)
        self.assertTrue(PushSubscription.objects.filter(endpoint=ENDPOINT % 'n24').exists())


class SettingsPageTest(PushTestCase):
    def test_kinds_and_devices(self):
        mine = self.device(self.alice, 'phone')
        theirs = self.device(self.bob, 'other')
        self.login(self.alice)
        page = self.client.get(self.alice.get_user_subscriptions_url())
        self.assertContains(page, 'Chrome on Android')
        self.assertContains(page, 'name="remove" value="%d"' % mine.id)
        self.assertNotContains(page, 'name="remove" value="%d"' % theirs.id)
        self.assertEqual(push.kinds_for(User.objects.get(id=self.alice.id)), set(push.DEFAULT_KINDS))

        url = self.alice.get_user_subscriptions_url()
        response = self.client.post(reverse('push_settings'), {'kinds': ['badge', 'mention', 'bogus'], 'next': url})
        self.assertRedirects(response, url + '#push-settings', fetch_redirect_response=False)
        self.assertEqual(push.kinds_for(User.objects.get(id=self.alice.id)), {'badge', 'mention'})
        page = self.client.get(url)
        self.assertContains(page, 'value="badge" checked')
        self.assertContains(page, 'value="answer_mine" />')

        # Nothing ticked: no pushes at all.
        self.client.post(reverse('push_settings'), {'next': url})
        self.assertEqual(push.kinds_for(User.objects.get(id=self.alice.id)), set())

        # Remove a device: only one's own.
        self.client.post(reverse('push_settings'), {'remove': theirs.id})
        self.assertTrue(PushSubscription.objects.filter(id=theirs.id).exists())
        response = self.client.post(reverse('push_settings'), {'remove': mine.id, 'next': 'https://evil.example.org/'})
        self.assertRedirects(response, url + '#push-settings', fetch_redirect_response=False)
        self.assertFalse(PushSubscription.objects.filter(id=mine.id).exists())


class SendingTest(PushTestCase):
    def test_answer_comment_mention_accepted(self):
        self.device(self.alice)
        self.device(self.bob)
        self.device(self.carol)
        answer = self.answer_as(self.bob)
        [payload] = self.pushed_to('alice')
        notification = Notification.objects.get(user=self.alice, kind='answer')
        self.assertEqual(payload, {
            'title': 'bob answered your question',
            'body': 'How do I export <survey> data to CSV & Excel?',  # plain text: the browser shows it as is
            'url': '/notifications/%d/' % notification.id,
            'id': notification.id,
            'tag': 'osqa-question-%d' % self.question.id,
            'icon': payload['icon'],
        })
        self.assertTrue(payload['icon'].startswith('/m/default/media/images/icons/icon-192.png'))
        self.assertNotIn('private words', json.dumps(self.pushed))

        self.pushed.clear()
        self.comment_as(self.carol, answer, 'Thanks, @alice see this, it is a private remark.')
        self.assertEqual([p['title'] for p in self.pushed_to('bob')], ['carol commented on your post'])
        self.assertEqual([p['title'] for p in self.pushed_to('alice')], ['carol mentioned you'])
        self.assertNotIn('private remark', json.dumps(self.pushed))

        self.pushed.clear()
        self.login(self.alice)
        self.assertTrue(self.command('/accept_answer/%d/' % answer.id)['success'])
        self.assertIn('alice accepted your answer', [p['title'] for p in self.pushed_to('bob')])
        # Badges are off by default.
        self.assertTrue(Notification.objects.filter(kind='badge').exists())
        self.assertNotIn('badge', json.dumps([p['title'] for _, p in self.pushed]).lower())

    def test_kinds_respected(self):
        self.device(self.alice)
        follower = self.carol
        from forum.models import QuestionSubscription
        QuestionSubscription(user=follower, question=self.question).save()
        self.device(follower)
        self.answer_as(self.bob)
        self.assertEqual(len(self.pushed_to('alice')), 1)
        self.assertEqual(self.pushed_to('carol'), [])  # answers to followed questions: off by default
        self.assertEqual(Notification.objects.filter(user=follower).count(), 1)

        push.set_kinds(User.objects.get(id=self.alice.id), ['mention'])
        push.set_kinds(User.objects.get(id=follower.id), ['answer_followed'])
        self.pushed.clear()
        self.answer_as(self.create_user('dave', reputation=100))
        self.assertEqual(self.pushed_to('alice'), [])
        self.assertEqual([p['title'] for p in self.pushed_to('carol')], ['dave answered a question you follow'])

    def test_badge_when_chosen(self):
        self.device(self.alice)
        push.set_kinds(User.objects.get(id=self.alice.id), ['badge'])
        answer = self.answer_as(self.bob)
        self.login(self.alice)
        self.command('/accept_answer/%d/' % answer.id)
        self.assertTrue(Notification.objects.filter(user=self.alice, kind='badge').exists())
        titles = [p['title'] for p in self.pushed_to('alice')]
        self.assertTrue(titles and all(t.startswith('You earned the ') for t in titles), titles)

    def test_every_device_and_gone_ones_deleted(self):
        phone = self.device(self.alice, 'phone')
        laptop = self.device(self.alice, 'laptop')
        old = self.device(self.alice, 'old')
        self.push_errors = {old.endpoint: 410}
        with mock.patch('forum.utils.metrics.count') as count:
            self.answer_as(self.bob)
        self.assertEqual(len(self.pushed_to('alice')), 2)
        self.assertFalse(PushSubscription.objects.filter(id=old.id).exists())
        self.assertEqual(sorted(c.args[0] for c in count.call_args_list if c.args[0].startswith('push_')),
                         ['push_failed', 'push_sent', 'push_sent'])
        for device in (phone, laptop):
            device.refresh_from_db()
            self.assertIsNotNone(device.last_success)
            self.assertEqual(device.failure_count, 0)

    def test_failures_counted_then_dropped(self):
        device = self.device(self.alice)
        self.push_errors = {device.endpoint: 500}
        self.answer_as(self.bob)
        device.refresh_from_db()
        self.assertEqual(device.failure_count, 1)
        self.push_errors = {device.endpoint: 404}
        self.comment_as(self.bob, self.question, 'A comment that is long enough to post.')
        self.assertFalse(PushSubscription.objects.exists())

        # A timeout or a broken connection is a failure too, and never breaks the action.
        device = self.device(self.alice, 'again')
        with mock.patch('forum.utils.push.webpush', side_effect=TimeoutError('slow')):
            self.comment_as(self.carol, self.question, 'Another comment that is long enough.')
        device.refresh_from_db()
        self.assertEqual(device.failure_count, 1)
        self.assertEqual(Notification.objects.filter(user=self.alice, kind='comment').count(), 2)

        PushSubscription.objects.filter(id=device.id).update(failure_count=push.MAX_FAILURES - 1)
        with mock.patch('forum.utils.push.webpush', side_effect=PushError(502)):
            self.comment_as(self.carol, self.question, 'A third comment that is long enough.')
        self.assertFalse(PushSubscription.objects.exists())

    def test_suspended_members_get_nothing(self):
        self.device(self.alice)
        User.objects.filter(id=self.alice.id).update(is_active=False)  # what suspending does
        self.answer_as(self.bob)
        self.assertEqual(self.pushed, [])
        # Even for a notification made some other way.
        notification = Notification.objects.create(user=self.alice, kind='mention', actor=self.bob, node=self.question)
        self.assertEqual(push.send_notifications([notification]), 0)
        self.assertEqual(self.pushed, [])

    def test_held_posts_push_nothing_until_approved(self):
        self.device(self.alice)
        settings.FIRST_POSTS_MODERATION.set_value('before')
        self.addCleanup(setattr, settings.FIRST_POSTS_MODERATION, '_temp', None)
        newbie = self.create_user('newbie', email_isvalid=True, reputation=60)
        answer = self.answer_as(newbie, 'Held answer mentioning @alice, long enough to post.')
        self.assertTrue(Node.objects.get(id=answer.id).nis.pending_review)
        self.assertFalse(Notification.objects.filter(user=self.alice).exists())
        self.assertEqual(self.pushed, [])
        # Defence in depth: a notification about a held post is never pushed.
        notification = Notification.objects.create(user=self.alice, kind='answer', actor=newbie, node=answer)
        self.assertEqual(push.send_notifications([notification]), 0)
        notification.delete()

        mod = self.create_user('mod', is_staff=True, email_isvalid=True, reputation=10)
        ApprovePostAction(user=mod, node=Node.objects.get(id=answer.id), ip='127.0.0.1').save()
        self.assertEqual([p['title'] for p in self.pushed_to('alice')], ['newbie mentioned you'])

    def test_review_outcome_when_chosen(self):
        self.device(self.bob)
        push.set_kinds(User.objects.get(id=self.bob.id), ['review'])
        mod = self.create_user('mod', is_staff=True, email_isvalid=True, reputation=10)
        from forum.notifications import notify
        notify({self.bob.id}, Notification.APPROVED, mod, self.question)
        notify({self.bob.id}, Notification.REMOVED, mod, self.question)
        self.assertEqual([p['title'] for p in self.pushed_to('bob')],
                         ['Your post was approved', 'A moderator removed your post'])

    def test_long_and_marked_up_texts(self):
        self.device(self.alice)
        Question.objects.filter(id=self.question.id).update(title='<b>Bold</b>\n\x07 ' + 'word ' * 60)
        actor = self.create_user('a' * 30 + '<script>', reputation=100)
        notification = Notification.objects.create(user=self.alice, kind='mention', actor=actor,
                                                   node=Question.objects.get(id=self.question.id))
        push.send_notifications([notification])
        [payload] = self.pushed_to('alice')
        self.assertLessEqual(len(payload['body']), push.BODY_LENGTH)
        # Plain text on one line (browsers never read it as HTML), control characters removed.
        self.assertTrue(payload['body'].startswith('<b>Bold</b> word word'), payload['body'])
        self.assertTrue(payload['body'].endswith('…'))
        self.assertNotIn('\x07', payload['body'])
        self.assertNotIn('\n', payload['body'])
        self.assertEqual(payload['title'], 'a' * 29 + '\u2026 mentioned you')  # names are cut to 30 characters


class RealEncryptionTest(PushTestCase):
    """pywebpush itself, with only the HTTP request replaced: the message is
    encrypted for the browser's keys and signed with the VAPID key."""

    def test_payload_encrypted_and_signed(self):
        import http_ece
        from py_vapid import Vapid
        key, p256dh, auth = browser_keys()
        device = PushSubscription.objects.create(user=self.alice, endpoint=ENDPOINT % 'real',
                                                 endpoint_hash='x', p256dh=p256dh, auth=auth)
        real = mock.patch('forum.utils.push.webpush', new=REAL_WEBPUSH)
        real.start()
        self.addCleanup(real.stop)
        with mock.patch('requests.post', return_value=mock.Mock(status_code=201)) as post:
            self.assertTrue(push.send(device, {'title': 'Hello', 'url': '/notifications/1/'}))
        (url,), kwargs = post.call_args
        self.assertEqual(url, ENDPOINT % 'real')
        self.assertEqual(kwargs['timeout'], push.TIMEOUT)
        self.assertEqual(kwargs['headers']['ttl'], str(push.TTL))
        plain = http_ece.decrypt(kwargs['data'], private_key=key, auth_secret=base64.urlsafe_b64decode(auth + '=='),
                                 version='aes128gcm')
        self.assertEqual(json.loads(plain), {'title': 'Hello', 'url': '/notifications/1/'})
        authorization = kwargs['headers']['Authorization']
        self.assertIn('k=%s' % PUBLIC_KEY, authorization)
        self.assertTrue(authorization.startswith('vapid '))
        self.assertTrue(Vapid.verify(authorization))

        with mock.patch('requests.post', return_value=mock.Mock(status_code=410, reason='Gone', text='')):
            self.assertFalse(push.send(device, {'title': 'Hello'}))
        self.assertFalse(PushSubscription.objects.exists())


class KeyCommandTest(OSQATestCase):
    def test_prints_a_usable_pair(self):
        out = io.StringIO()
        call_command('generate_vapid_keys', stdout=out)
        lines = dict(line.split(' = ', 1) for line in out.getvalue().splitlines() if ' = ' in line)
        public = lines['VAPID_PUBLIC_KEY'].strip("'")
        private = lines['VAPID_PRIVATE_KEY'].strip("'")
        self.assertEqual(len(base64.urlsafe_b64decode(public + '==')), 65)
        self.assertEqual(len(base64.urlsafe_b64decode(private + '==')), 32)
        from py_vapid import Vapid
        vapid = Vapid.from_string(private)
        from cryptography.hazmat.primitives import serialization
        self.assertEqual(b64(vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                           serialization.PublicFormat.UncompressedPoint)), public)
        out = io.StringIO()
        call_command('generate_vapid_keys', '--env', stdout=out)
        self.assertRegex(out.getvalue(), r'^VAPID_PUBLIC_KEY=[\w-]{87}\nVAPID_PRIVATE_KEY=[\w-]{43}\n')
        self.assertNotEqual(out.getvalue().split('\n')[0].split('=')[1], public)


class ServiceWorkerPushTest(OSQATestCase):
    def test_push_handlers_next_to_the_caching_rules(self):
        worker = self.client.get('/sw.js').content.decode()
        for part in ("addEventListener('push'", "addEventListener('notificationclick'", "addEventListener('fetch'",
                     'const APP_NAME = "OSQA";', "const ICON = \"/m/default/media/images/icons/icon-192.png?v=",
                     'stopPush()'):
            self.assertIn(part, worker)
