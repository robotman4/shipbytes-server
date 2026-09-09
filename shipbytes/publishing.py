"""Shared API/CLI publisher. Durable intent plus a process lock prevents duplicate sends."""
import fcntl
from contextlib import contextmanager
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from .db import now
from .models import Issue
from .schemas import StoryInput
from .mail import MailError, RetryableMailError

class PublicationError(Exception):
    def __init__(self, message, code=503):
        super().__init__(message)
        self.code = code

@contextmanager
def publication_lock(sessions):
    engine = sessions.kw['bind']
    path = Path(engine.url.database).resolve().with_suffix('.publisher.lock')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublicationError('Another publisher is running', 409)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

def publish_issue(sessions, config, mailer, slug):
    with publication_lock(sessions):
        return publish_locked(sessions, config, mailer, slug)

def publish_locked(sessions, config, mailer, slug):
    with sessions() as db:
        issue = db.scalar(select(Issue).where(Issue.slug == slug))
        if not issue:
            raise PublicationError('Issue not found', 404)
        if issue.status == 'published' or issue.delivery_state == 'sent':
            raise PublicationError('Issue already published', 409)
        if issue.delivery_state in ('creating', 'sending', 'uncertain'):
            # A crash or lost response must never trigger another external write.
            raise PublicationError('Provider outcome uncertain; reconcile with Resend before retrying', 409)
        if not issue.stories:
            raise PublicationError('Cannot publish an empty issue', 422)
        try:
            for story in issue.stories:
                StoryInput.model_validate(story)
            if not issue.resend_broadcast_id:
                issue.published_at = now()
                for story in issue.stories:
                    story.published_at = story.published_at or issue.published_at
                env = Environment(loader=FileSystemLoader(Path(__file__).parent / 'templates'), autoescape=select_autoescape(['html', 'xml']))
                context = {'issue': issue, 'config': config, 'unsubscribe': '{{{RESEND_UNSUBSCRIBE_URL}}}'}
                issue.newsletter_html = env.get_template('newsletter.html').render(**context)
                issue.newsletter_text = env.get_template('newsletter.txt').render(**context)
        except Exception:
            issue.status = 'failed'
            issue.published_at = None
            db.commit()
            raise PublicationError('Issue validation or newsletter rendering failed', 422) from None
        issue.status = 'publishing'
        db.commit()
        try:
            if not issue.resend_broadcast_id:
                issue.delivery_state = 'creating'
                db.commit()
                issue.resend_broadcast_id = mailer.create_broadcast(issue)
                issue.delivery_state = 'created'
                db.commit()
            issue.delivery_state = 'sending'
            db.commit()
            mailer.send_broadcast(issue.resend_broadcast_id)
        except RetryableMailError:
            issue.status = 'failed'
            issue.delivery_state = 'created' if issue.resend_broadcast_id else 'new'
            issue.published_at = None
            db.commit()
            raise PublicationError('Provider rejected request before acceptance; safe to retry') from None
        except (MailError, OSError, ValueError, KeyError):
            issue.status = 'failed'
            issue.delivery_state = 'uncertain'
            issue.published_at = None
            db.commit()
            raise PublicationError('Provider outcome uncertain; automatic resend blocked') from None
        issue.status = 'published'
        issue.delivery_state = 'sent'
        issue.published_at = now()
        db.commit()
        return {'status': 'published', 'issue': slug, 'url': f'{config.site_url}/issues/{slug}',
                'stories': len(issue.stories), 'broadcast_id': issue.resend_broadcast_id}
