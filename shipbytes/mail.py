import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote
import httpx

class MailError(Exception):
    pass

class RetryableMailError(MailError):
    """Request definitively not accepted; retry is safe."""

class Mailer:
    def __init__(self, config):
        self.config = config
        self._request_lock = threading.Lock()
        self._last_request = 0.0

    def request(self, method, path, payload=None, missing_ok=False):
        with self._request_lock:
            time.sleep(max(0, 0.6 - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
        try:
            with httpx.Client(timeout=20) as client:
                response = client.request(method, 'https://api.resend.com' + path,
                    headers={'Authorization': f'Bearer {self.config.resend_api_key}'}, json=payload)
            if missing_ok and response.status_code == 404:
                return None
            if response.status_code in (400, 401, 403, 404, 422, 429):
                raise RetryableMailError('Email provider rejected request')
            response.raise_for_status()
            return response.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            raise RetryableMailError('Could not connect to email provider') from None
        except (httpx.HTTPError, ValueError) as exc:
            raise MailError('Email provider request failed') from None

    def save(self, kind, payload):
        directory = Path(self.config.email_directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        identifier = 'dev-' + secrets.token_hex(16)
        path = directory / f'{kind}-{identifier}.json'
        with path.open('x') as file:
            path.chmod(0o600)
            json.dump(payload, file, indent=2)
        return identifier

    def confirmation(self, email, url):
        payload = {'from': f'{self.config.resend_from_name} <{self.config.resend_from_email}>',
                   'to': [email], 'subject': 'Confirm your Ship Bytes subscription',
                   'text': f'Confirm your subscription to Ship Bytes: {url}\nThis link expires in 24 hours. Ignore this email if you did not request it.',
                   'html': f'<p>Confirm your subscription to Ship Bytes.</p><p><a href="{url}">Confirm subscription</a></p><p>This link expires in 24 hours. Ignore this email if you did not request it.</p>'}
        if self.config.email_backend == 'development':
            self.save('confirmation', payload)
        else:
            self.request('POST', '/emails', payload)

    def contact(self, email):
        if self.config.email_backend == 'development':
            return 'dev-' + secrets.token_hex(16), False
        path = '/contacts/' + quote(email, safe='')
        contact = self.request('GET', path, missing_ok=True)
        if contact and contact.get('unsubscribed'):
            return contact['id'], True
        if not contact:
            contact = self.request('POST', '/contacts', {'email': email, 'unsubscribed': False})
        self.request('POST', f"/contacts/{contact['id']}/segments/{self.config.resend_segment_id}")
        return contact['id'], False

    def create_broadcast(self, issue):
        payload = {'name': f'Ship Bytes: {issue.slug}', 'segment_id': self.config.resend_segment_id,
                   'from': f'{self.config.resend_from_name} <{self.config.resend_from_email}>',
                   'subject': issue.subject.replace('—', '-'), 'html': issue.newsletter_html, 'text': issue.newsletter_text}
        if self.config.email_backend == 'development':
            return self.save('broadcast', payload)
        return self.request('POST', '/broadcasts', payload)['id']

    def send_broadcast(self, identifier):
        if self.config.email_backend != 'development':
            self.request('POST', f'/broadcasts/{identifier}/send', {})
