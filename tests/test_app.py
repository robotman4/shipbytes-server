import base64
import hashlib
import hmac
import json
import re
import time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from shipbytes.config import Settings
from shipbytes.db import Base, now
from shipbytes.main import create_app
from shipbytes.mail import Mailer, MailError
from shipbytes.models import Subscriber, Issue

SECRET = 'whsec_' + base64.b64encode(b'webhook-test-secret-long-enough').decode()
PAYLOAD = {'slug': 'test-issue', 'title': 'Test issue', 'subject': 'Maritime test', 'intro': 'Test only', 'stories': [{'slug': 'test-story', 'title': 'Example', 'byte': 'A useful byte.', 'summary': 'LONG SUMMARY EXCLUDED FROM EMAIL', 'why_it_matters': 'Practical context.', 'source_name': 'Primary source', 'source_url': 'https://example.com/source', 'source_type': 'external'}]}
HEADERS = {'Authorization': 'Bearer test-token'}

@pytest.fixture
def setup(tmp_path):
    config = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path}/test.db', publish_api_token='test-token', resend_webhook_secret=SECRET, email_directory=str(tmp_path/'emails'))
    mailer = Mock(spec=Mailer)
    mailer.contact.return_value = ('contact-1', False)
    mailer.create_broadcast.return_value = 'broadcast-1'
    app = create_app(config, mailer)
    Base.metadata.create_all(app.state.engine)
    with TestClient(app) as client:
        yield client, app, mailer, config
    app.state.engine.dispose()

def form(client, path='/subscribe'):
    response = client.get(path)
    return re.search(r'name="csrf" value="([^"]+)"', response.text)[1]

def subscribe(client, email='Reader@Example.com'):
    return client.post('/subscribe', data={'email': email, 'csrf': form(client)})

def token(mailer):
    return mailer.confirmation.call_args.args[1].split('token=')[1]

def confirm(client, value):
    return client.post('/subscribe/confirm', data={'token': value, 'csrf': form(client, '/subscribe/confirm?token='+value)})

def create(client, payload=PAYLOAD):
    return client.post('/api/v1/issues', json=payload, headers=HEADERS)

def publish(client):
    return client.post('/api/v1/issues/test-issue/publish', headers=HEADERS)

def signed_event(kind, data, identifier='event-1', timestamp=None):
    timestamp = str(timestamp or int(time.time()))
    body = json.dumps({'type': kind, 'data': data})
    signature = base64.b64encode(hmac.new(base64.b64decode(SECRET[6:]), f'{identifier}.{timestamp}.{body}'.encode(), hashlib.sha256).digest()).decode()
    return body, {'svix-id': identifier, 'svix-timestamp': timestamp, 'svix-signature': 'v1,'+signature}

def test_home_and_pages(setup):
    client, _, _, _ = setup
    for path in ('/', '/subscribe', '/issues', '/about', '/privacy'):
        response = client.get(path)
        assert response.status_code == 200
        assert 'Ship Bytes' in response.text
        assert response.headers['x-frame-options'] == 'DENY'
    assert client.get('/health').json() == {'status': 'ok'}
    assert 'first issue' in client.get('/').text.lower()

def test_subscription_duplicate_confirmation(setup):
    client, app, mailer, _ = setup
    assert subscribe(client).status_code == 200
    assert subscribe(client, 'reader@example.com').status_code == 200
    mailer.confirmation.assert_called_once()
    value = token(mailer)
    assert confirm(client, value).status_code == 200
    assert confirm(client, value).status_code == 400
    with app.state.sessions() as db:
        subscriber = db.scalar(select(Subscriber))
        assert subscriber.status == 'subscribed'
        assert subscriber.email == 'reader@example.com'
        assert subscriber.confirmation_token_hash is None

def test_expired_confirmation(setup):
    client, app, mailer, _ = setup
    subscribe(client)
    with app.state.sessions() as db:
        subscriber = db.scalar(select(Subscriber))
        subscriber.confirmation_expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert confirm(client, token(mailer)).status_code == 400
    mailer.contact.assert_not_called()

def test_csrf_honeypot_rate_limit(setup):
    client, _, mailer, _ = setup
    assert client.post('/subscribe', data={'email': 'a@example.com'}).status_code == 403
    assert client.post('/subscribe', data={'email': 'a@example.com', 'csrf': form(client), 'website': 'spam'}).status_code == 200
    mailer.confirmation.assert_not_called()
    assert subscribe(client, 'invalid').status_code == 422
    for _ in range(3):
        assert subscribe(client).status_code == 200
    assert subscribe(client).status_code == 429

def test_provider_failure_confirmation_retry(setup):
    client, _, mailer, _ = setup
    mailer.confirmation.side_effect = MailError()
    assert subscribe(client).status_code == 503
    mailer.confirmation.side_effect = None
    assert subscribe(client).status_code == 200
    assert confirm(client, token(mailer)).status_code == 200

def test_resend_unsubscribe_authoritative(setup):
    client, app, mailer, _ = setup
    subscribe(client)
    mailer.contact.return_value = ('contact-1', True)
    assert confirm(client, token(mailer)).status_code == 200
    with app.state.sessions() as db:
        assert db.scalar(select(Subscriber)).status == 'unsubscribed'

@pytest.mark.parametrize('header', [{}, {'Authorization':'Bearer wrong'}])
def test_authentication(setup, header):
    assert setup[0].post('/api/v1/issues', json=PAYLOAD, headers=header).status_code == 401

def test_create_publish_render_idempotency(setup):
    client, app, mailer, _ = setup
    assert create(client).status_code == 201
    mailer.create_broadcast.assert_not_called()
    assert client.get('/issues/test-issue').status_code == 404
    assert client.get('/stories/test-story').status_code == 404
    response = publish(client)
    assert response.status_code == 200
    assert response.json()['broadcast_id'] == 'broadcast-1'
    assert publish(client).status_code == 409
    mailer.create_broadcast.assert_called_once()
    mailer.send_broadcast.assert_called_once_with('broadcast-1')
    assert client.get('/issues/test-issue').status_code == 200
    assert 'LONG SUMMARY' in client.get('/stories/test-story').text
    with app.state.sessions() as db:
        issue = db.scalar(select(Issue))
        for content in (issue.newsletter_html, issue.newsletter_text):
            assert 'LONG SUMMARY' not in content
            assert 'A useful byte.' in content
            assert 'RESEND_UNSUBSCRIBE_URL' in content
            assert 'Read more' in content
            assert 'Source' in content

def test_validation_duplicates_empty(setup):
    client = setup[0]
    assert create(client, {**PAYLOAD, 'stories':[{**PAYLOAD['stories'][0], 'source_url':'javascript:alert(1)'}]}).status_code == 422
    assert create(client, {**PAYLOAD, 'stories':PAYLOAD['stories']*2}).status_code == 422
    assert create(client, {**PAYLOAD, 'stories':[]}).status_code == 201
    assert publish(client).status_code == 422
    assert create(client).status_code == 409

@pytest.mark.parametrize('stage', ['create_broadcast', 'send_broadcast'])
def test_uncertain_publish_never_retries(setup, stage):
    client, _, mailer, _ = setup
    create(client)
    getattr(mailer, stage).side_effect = MailError()
    assert publish(client).status_code == 503
    assert publish(client).status_code == 409
    assert mailer.create_broadcast.call_count == 1
    assert mailer.send_broadcast.call_count <= 1

def test_concurrent_publish(setup):
    client, _, mailer, _ = setup
    create(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: publish(client).status_code, range(2)))
    assert sorted(codes) == [200, 409]
    mailer.send_broadcast.assert_called_once()

def test_original_reporting_and_escaping(setup):
    client, app, _, _ = setup
    payload = {**PAYLOAD, 'stories': [{**PAYLOAD['stories'][0], 'source_type':'shipbytes', 'source_name':'', 'source_url':'', 'title':'<script>alert(1)</script>'}]}
    create(client, payload)
    publish(client)
    assert '<script>' not in client.get('/issues/test-issue').text
    with app.state.sessions() as db:
        content = db.scalar(select(Issue)).newsletter_html
        assert 'Read full story' in content
        assert 'Source:' not in content

@pytest.mark.parametrize('kind,data,status', [('contact.updated', {'email':'reader@example.com','unsubscribed':True}, 'unsubscribed'), ('contact.deleted', {'id':'contact-1'}, 'unsubscribed'), ('email.bounced', {'to':['reader@example.com']}, 'bounced'), ('email.complained', {'to':['reader@example.com']}, 'complained')])
def test_webhook_suppression(setup, kind, data, status):
    client, app, mailer, _ = setup
    subscribe(client)
    confirm(client, token(mailer))
    body, headers = signed_event(kind, data)
    assert client.post('/webhooks/resend', content=body, headers=headers).status_code == 200
    assert client.post('/webhooks/resend', content=body, headers=headers).status_code == 200
    body, headers = signed_event('contact.updated', {'email':'reader@example.com', 'unsubscribed':False}, 'event-2')
    client.post('/webhooks/resend', content=body, headers=headers)
    with app.state.sessions() as db:
        assert db.scalar(select(Subscriber)).status == status
    subscribe(client)
    assert mailer.confirmation.call_count == 1

def test_invalid_webhook_signature(setup):
    client = setup[0]
    body, headers = signed_event('contact.updated', {}, timestamp=int(time.time())-1000)
    assert client.post('/webhooks/resend', content=body, headers=headers).status_code == 400
    body, headers = signed_event('contact.updated', {})
    assert client.post('/webhooks/resend', content=body+' ', headers=headers).status_code == 400
    assert client.post('/webhooks/resend', json={}).status_code == 400

def test_resend_http_mock(setup, monkeypatch):
    config = setup[3].model_copy(update={'email_backend':'resend', 'resend_api_key':'fake', 'resend_segment_id':'segment'})
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
        return httpx.Response(200, json={'id':'provider-id'})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    mailer = Mailer(config)
    mailer.confirmation('a@example.com', 'https://example.com/confirm')
    assert mailer.contact('a@example.com') == ('provider-id', False)
    assert calls[-1].url.path == '/contacts/provider-id/segments/segment'
    issue = Mock(slug='test', subject='Subject', newsletter_html='html', newsletter_text='text')
    assert mailer.create_broadcast(issue) == 'provider-id'
    assert json.loads(calls[-1].content)['segment_id'] == 'segment'
    mailer.send_broadcast('provider-id')
    assert calls[-1].url.path == '/broadcasts/provider-id/send'

def test_development_mail_writes_files(setup):
    config = setup[3]
    mailer = Mailer(config)
    mailer.confirmation('a@example.com', 'http://localhost:8000/confirm')
    from pathlib import Path
    assert len(list(Path(config.email_directory).glob('confirmation-*.json'))) == 1

def test_privacy_origin_and_cross_site_rejection(setup):
    client = setup[0]
    response = client.post('/subscribe', data={'email':'privacy@example.com', 'csrf':form(client)}, headers={'Origin':'null'})
    assert response.status_code == 200
    response = client.post('/subscribe', data={'email':'privacy@example.com', 'csrf':form(client)}, headers={'Origin':'https://attacker.example'})
    assert response.status_code == 403
    response = client.post('/subscribe', data={'email':'privacy@example.com', 'csrf':'非ASCII'})
    assert response.status_code == 403
