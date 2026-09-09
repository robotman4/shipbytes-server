import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import select_autoescape
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, text, delete
from sqlalchemy.exc import IntegrityError
from svix.webhooks import Webhook, WebhookVerificationError
from .config import settings
from .db import database, now
from .models import Subscriber, Issue, Story, WebhookReceipt, RateLimit
from .schemas import IssueInput, StoryInput
from .mail import Mailer, MailError

ROOT = Path(__file__).parent

def create_app(config=None, mailer=None):
    config = config or settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    engine, sessions = database(config.database_url)
    app.state.engine, app.state.sessions = engine, sessions
    mailer = mailer or Mailer(config)
    app.state.mailer = mailer
    signer = URLSafeTimedSerializer(config.secret_key)
    templates = Jinja2Templates(directory=str(ROOT / 'templates'))
    templates.env.autoescape = select_autoescape(['html', 'xml'])
    from .media import media_root
    media_directory = media_root(config)
    media_directory.mkdir(parents=True, exist_ok=True)
    app.mount('/media', StaticFiles(directory=media_directory), name='media')
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    @app.middleware('http')
    async def security(request, call_next):
        if int(request.headers.get('content-length', '0')) > 262144:
            return HTMLResponse('Request too large', status_code=413)
        response = await call_next(request)
        response.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
            'Content-Security-Policy': "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        if config.site_url.startswith('https:'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        if request.url.path.startswith(('/subscribe', '/api', '/webhooks')):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def render(request, template, **context):
        csrf = signer.dumps(secrets.token_urlsafe(24), salt='csrf')
        response = templates.TemplateResponse(request=request, name=template,
            context={'config': config, 'csrf': csrf, **context})
        response.set_cookie('csrf', csrf, httponly=True, secure=config.site_url.startswith('https:'), samesite='lax', max_age=3600)
        return response

    def csrf_check(request, token):
        if not token or not hmac.compare_digest(token.encode(), request.cookies.get('csrf', '').encode()):
            raise HTTPException(403, 'Invalid form token; reload the page')
        try:
            signer.loads(token, salt='csrf', max_age=3600)
        except BadSignature:
            raise HTTPException(403, 'Expired form; reload the page')
        origin = request.headers.get('origin')
        if origin and origin not in ('null', config.site_url):
            raise HTTPException(403, 'Invalid origin')

    def auth(request: Request):
        expected = config.publish_api_token
        actual = request.headers.get('authorization', '')
        if not expected or not hmac.compare_digest(actual.encode(), ('Bearer ' + expected).encode()):
            raise HTTPException(401, 'Invalid publishing credentials', headers={'WWW-Authenticate': 'Bearer'})

    @app.exception_handler(MailError)
    async def mail_error(request, exc):
        return HTMLResponse('Email service temporarily unavailable. Please try again later.', status_code=503)

    @app.get('/health')
    def health():
        with sessions() as db:
            db.execute(text('SELECT 1'))
        return {'status': 'ok'}

    @app.get('/')
    def home(request: Request):
        with sessions() as db:
            issues = list(db.scalars(select(Issue).where(Issue.status == 'published').order_by(Issue.published_at.desc()).limit(6)))
            stories = list(db.scalars(select(Story).join(Issue).where(Issue.status == 'published').order_by(Issue.published_at.desc(), Story.sort_order).limit(10)))
            return render(request, 'home.html', issues=issues, stories=stories)

    @app.get('/issues')
    def archive(request: Request, page: int = 1):
        if page < 1 or page > 10000:
            raise HTTPException(422, 'Invalid page')
        with sessions() as db:
            issues = list(db.scalars(select(Issue).where(Issue.status == 'published').order_by(Issue.published_at.desc()).offset((page-1)*20).limit(20)))
            return render(request, 'archive.html', issues=issues, page=page)

    @app.get('/issues/{slug}')
    def issue_page(request: Request, slug: str):
        with sessions() as db:
            issue = db.scalar(select(Issue).where(Issue.slug == slug, Issue.status == 'published'))
            if not issue:
                raise HTTPException(404, 'Issue not found')
            return render(request, 'issue.html', issue=issue)

    @app.get('/stories/{slug}')
    def story_page(request: Request, slug: str):
        with sessions() as db:
            story = db.scalar(select(Story).join(Issue).where(Story.slug == slug, Issue.status == 'published'))
            if not story:
                raise HTTPException(404, 'Story not found')
            return render(request, 'story.html', story=story)

    @app.get('/about')
    def about(request: Request):
        return render(request, 'about.html')

    @app.get('/privacy')
    def privacy(request: Request):
        return render(request, 'privacy.html')

    @app.get('/subscribe')
    def subscribe_page(request: Request):
        return render(request, 'subscribe.html')

    @app.post('/subscribe')
    async def subscribe(request: Request):
        form = await request.form(max_fields=5, max_files=0)
        csrf_check(request, str(form.get('csrf', '')))
        message = 'If your address is eligible, a confirmation email is on its way. Check your inbox.'
        if form.get('website'):
            return render(request, 'message.html', title='Check your inbox', message=message)
        try:
            email = str(TypeAdapter(EmailStr).validate_python(str(form.get('email', '')).strip())).lower()
        except ValidationError:
            raise HTTPException(422, 'Enter a valid email address')
        with sessions() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            timestamp = int(time.time())
            db.execute(delete(RateLimit).where(RateLimit.expires < timestamp))
            for value, maximum in [(request.client.host, 10), (email, 3)]:
                key = hmac.new(config.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()
                limit = db.get(RateLimit, key)
                if limit and limit.count >= maximum:
                    raise HTTPException(429, 'Too many subscription requests; try again in an hour', headers={'Retry-After': '3600'})
                if limit:
                    limit.count += 1
                else:
                    db.add(RateLimit(key=key, count=1, expires=timestamp+3600))
            db.commit()
            db.execute(text('BEGIN IMMEDIATE'))
            subscriber = db.scalar(select(Subscriber).where(Subscriber.email == email))
            if subscriber and (subscriber.status != 'pending' or (subscriber.confirmation_expires_at and subscriber.confirmation_expires_at > now())):
                return render(request, 'message.html', title='Check your inbox', message=message)
            if not subscriber:
                subscriber = Subscriber(email=email)
                db.add(subscriber)
            random = secrets.token_urlsafe(32)
            token = signer.dumps(random, salt='confirmation')
            subscriber.confirmation_token_hash = hashlib.sha256(random.encode()).hexdigest()
            subscriber.confirmation_expires_at = now() + timedelta(hours=24)
            mailer.confirmation(email, config.site_url + '/subscribe/confirm?token=' + token)
            db.commit()
        return render(request, 'message.html', title='Check your inbox', message=message)

    @app.get('/subscribe/confirm')
    def confirm_page(request: Request, token: str = ''):
        # A POST prevents email scanners from consuming the single-use link.
        return render(request, 'confirm.html', token=token)

    @app.post('/subscribe/confirm')
    async def confirm(request: Request):
        form = await request.form(max_fields=3, max_files=0)
        csrf_check(request, str(form.get('csrf', '')))
        try:
            random = signer.loads(str(form.get('token', '')), salt='confirmation', max_age=86400)
            digest = hashlib.sha256(random.encode()).hexdigest()
        except (BadSignature, SignatureExpired, AttributeError):
            raise HTTPException(400, 'Invalid or expired confirmation link')
        with sessions() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            subscriber = db.scalar(select(Subscriber).where(Subscriber.confirmation_token_hash == digest))
            if not subscriber or subscriber.status != 'pending' or subscriber.confirmation_expires_at < now():
                raise HTTPException(400, 'Invalid, expired, or already used confirmation link')
            identifier, unsubscribed = mailer.contact(subscriber.email)
            subscriber.resend_contact_id = identifier
            subscriber.status = 'unsubscribed' if unsubscribed else 'subscribed'
            subscriber.confirmed_at = now()
            subscriber.confirmation_token_hash = None
            subscriber.confirmation_expires_at = None
            db.commit()
        return render(request, 'message.html', title='Subscription confirmed' if not unsubscribed else 'Subscription remains paused',
                      message='Thank you for reading Ship Bytes.' if not unsubscribed else 'Your existing unsubscribe preference has been preserved.')

    @app.post('/api/v1/issues', dependencies=[Depends(auth)], status_code=201)
    def create_issue(payload: IssueInput):
        with sessions() as db:
            issue = Issue(**payload.model_dump(exclude={'stories'}))
            for order, item in enumerate(payload.stories):
                data = item.model_dump()
                data['source_url'] = str(item.source_url)
                if data['published_at']:
                    data['published_at'] = data['published_at'].replace(tzinfo=None)
                issue.stories.append(Story(**data, sort_order=order))
            db.add(issue)
            try:
                db.commit()
            except IntegrityError:
                raise HTTPException(409, 'Issue or story slug already exists')
            return {'status': 'draft', 'issue': issue.slug, 'stories': len(issue.stories)}

    @app.post('/api/v1/issues/{slug}/publish', dependencies=[Depends(auth)])
    def publish(slug: str):
        from .publishing import publish_issue, PublicationError
        try:
            return publish_issue(sessions, config, mailer, slug)
        except PublicationError as exc:
            raise HTTPException(exc.code, str(exc))

    @app.post('/webhooks/resend')
    async def webhook(request: Request):
        body = await request.body()
        if len(body) > 262144:
            raise HTTPException(413, 'Payload too large')
        if not config.resend_webhook_secret:
            raise HTTPException(503, 'Webhook not configured')
        try:
            event = Webhook(config.resend_webhook_secret).verify(body, dict(request.headers))
        except (WebhookVerificationError, ValueError):
            raise HTTPException(400, 'Invalid webhook signature')
        if not isinstance(event, dict) or not isinstance(event.get('data'), dict):
            raise HTTPException(422, 'Invalid webhook payload')
        data, kind = event['data'], event.get('type')
        with sessions() as db:
            db.execute(text('BEGIN IMMEDIATE'))
            identifier = request.headers['svix-id']
            if db.get(WebhookReceipt, identifier):
                return {'status': 'ok'}
            db.add(WebhookReceipt(id=identifier))
            status = {'contact.deleted': 'unsubscribed', 'email.bounced': 'bounced', 'email.complained': 'complained'}.get(kind)
            if kind == 'contact.updated' and data.get('unsubscribed') is True:
                status = 'unsubscribed'
            if status:
                emails = data.get('to', []) if kind.startswith('email.') else [data.get('email', '')]
                if not isinstance(emails, list) or not all(isinstance(e, str) for e in emails):
                    raise HTTPException(422, 'Invalid recipients')
                subscribers = list(db.scalars(select(Subscriber).where(Subscriber.email.in_([e.strip().lower() for e in emails]))))
                if kind.startswith('contact.') and data.get('id'):
                    contact = db.scalar(select(Subscriber).where(Subscriber.resend_contact_id == data['id']))
                    if contact and contact not in subscribers:
                        subscribers.append(contact)
                for subscriber in subscribers:
                    # Suppressions only move toward stronger restrictions; stale updates cannot re-subscribe.
                    priority = {'pending': 0, 'subscribed': 0, 'unsubscribed': 1, 'bounced': 2, 'complained': 3}
                    if priority[status] >= priority[subscriber.status]:
                        subscriber.status = status
                        subscriber.confirmation_token_hash = None
            db.commit()
        return {'status': 'ok'}

    return app
