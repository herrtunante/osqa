import json
from unittest import mock

import sentry_sdk
from django.conf import settings as djsettings
from django.test import Client, RequestFactory, override_settings
from sentry_sdk.transport import Transport

from forum.middleware.sentry import SentryUserMiddleware
from forum.utils.sentry import dsn_origin, init_server

from .base import OSQATestCase

DSN = 'https://abc123@o77.ingest.de.sentry.io/42'


class CapturingTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if item.type == 'event':
                self.events.append(json.loads(item.payload.get_bytes()))


class BrowserReportingTest(OSQATestCase):
    def test_off_without_a_dsn(self):
        response = self.client.get('/')
        self.assertNotContains(response, 'sentry-config')
        self.assertNotIn('sentry.io', response['Content-Security-Policy'])

    @override_settings(SENTRY_FRONTEND_DSN=DSN, SENTRY_ENVIRONMENT='staging', SENTRY_RELEASE='osqa@abc')
    def test_pages_load_the_sdk_with_the_settings(self):
        alice = self.create_user('alice')
        self.login(alice)
        response = self.client.get('/')
        self.assertContains(response, '<script id="sentry-config" type="application/json">')
        config = json.loads(response.content.decode().split('<script id="sentry-config" type="application/json">')[1]
                            .split('</script>')[0])
        self.assertEqual(config, {'dsn': DSN, 'environment': 'staging', 'release': 'osqa@abc', 'user': str(alice.id)})
        self.assertContains(response, '/m/default/media/vendor/sentry-10.75.3/sentry.bundle.min.js')
        self.assertContains(response, '/m/default/media/js/osqa.sentry.js')
        csp = response['Content-Security-Policy']
        self.assertIn('https://o77.ingest.de.sentry.io', csp.split('connect-src')[1].split(';')[0])

        # The bundle comes before jQuery, so errors in every other script are caught.
        body = response.content.decode()
        self.assertLess(body.index('sentry.bundle.min.js'), body.index('jquery-3.7.1.min.js'))
        self.assertEqual(self.client.get('/m/default/media/vendor/sentry-10.75.3/sentry.bundle.min.js').status_code, 200)

        alice.is_superuser = True
        alice.save()
        self.assertContains(self.client.get('/admin/'), 'sentry-config')

    def test_dsn_origin(self):
        self.assertEqual(dsn_origin(DSN), 'https://o77.ingest.de.sentry.io')
        self.assertEqual(dsn_origin('https://k@sentry.openforis.org:9000/3'), 'https://sentry.openforis.org:9000')
        self.assertEqual(dsn_origin('not a dsn'), '')


class ServerReportingTest(OSQATestCase):
    def init_sentry(self):
        transport = CapturingTransport()
        init_server(DSN, 'test', transport=transport)  # as settings.py does when SENTRY_DSN is set
        self.addCleanup(sentry_sdk.init)  # back to a disabled client
        return transport

    def test_errors_are_reported_without_personal_data(self):
        transport = self.init_sentry()
        alice = self.create_user('alice')
        client = Client(raise_request_exception=False)
        client.force_login(alice)
        client.cookies['sessionid'] = client.cookies['sessionid'].value
        middleware = djsettings.MIDDLEWARE + ['forum.middleware.sentry.SentryUserMiddleware']
        with override_settings(MIDDLEWARE=middleware), \
                mock.patch('forum.views.meta.render', side_effect=RuntimeError('template exploded')):
            response = client.post('/about/', {'password': 'hunter2'}, HTTP_USER_AGENT='Test')
        self.assertEqual(response.status_code, 500)

        self.assertEqual(len(transport.events), 1)
        event = transport.events[0]
        self.assertEqual(event['exception']['values'][-1]['value'], 'template exploded')
        self.assertEqual(event['environment'], 'test')
        self.assertEqual(event['user'], {'id': str(alice.id)})
        serialized = json.dumps(event)
        for secret in ('hunter2', client.cookies['sessionid'].value, 'alice@example.com', '127.0.0.1'):
            self.assertNotIn(secret, serialized)

    def test_user_id_only(self):
        request = RequestFactory().get('/')
        request.user = self.create_user('alice')
        with mock.patch('sentry_sdk.set_user') as set_user:
            SentryUserMiddleware(lambda r: None)(request)
        set_user.assert_called_once_with({'id': str(request.user.id)})

    @override_settings(SENTRY_DSN=DSN)
    def test_csp_violations_are_sent_as_warnings(self):
        report = {'csp-report': {'violated-directive': 'script-src-elem', 'blocked-uri': 'https://cdn.example/x.js',
                                 'document-uri': 'http://testserver/'}}
        transport = self.init_sentry()
        with self.assertLogs('forum.security', 'WARNING'):
            self.client.post('/csp-report/', json.dumps(report), content_type='application/csp-report')
        event = transport.events[0]
        self.assertEqual(event['level'], 'warning')
        self.assertEqual(event['message'], 'CSP blocked https://cdn.example/x.js (script-src-elem)')
        self.assertEqual(event['fingerprint'], ['csp', 'script-src-elem', 'https://cdn.example/x.js'])
