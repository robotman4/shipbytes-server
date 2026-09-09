from datetime import datetime
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base, now

class Timestamps:
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)

class Subscriber(Timestamps, Base):
    __tablename__ = 'subscribers'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    status: Mapped[str] = mapped_column(default='pending')
    resend_contact_id: Mapped[str | None]
    confirmation_token_hash: Mapped[str | None] = mapped_column(unique=True)
    confirmation_expires_at: Mapped[datetime | None]
    confirmed_at: Mapped[datetime | None]

class Issue(Timestamps, Base):
    __tablename__ = 'issues'
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    subject: Mapped[str]
    intro: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(default='draft', index=True)
    published_at: Mapped[datetime | None]
    resend_broadcast_id: Mapped[str | None]
    image_file: Mapped[str | None]
    image_thumbnail: Mapped[str | None]
    image_alt: Mapped[str | None]
    image_credit: Mapped[str | None]
    image_source_url: Mapped[str | None]
    image_usage: Mapped[str | None]

    @property
    def cover_url(self):
        from .media import DEFAULT_IMAGE
        return self.image_file or DEFAULT_IMAGE

    @property
    def thumbnail_url(self):
        return self.image_thumbnail or self.cover_url

    @property
    def cover_alt(self):
        return self.image_alt if self.image_file else 'Ship Bytes maritime technology logo'

    source_filename: Mapped[str | None]
    source_git_sha: Mapped[str | None]
    source_content_hash: Mapped[str | None]
    publication_date: Mapped[datetime | None]
    delivery_state: Mapped[str] = mapped_column(default='new')
    newsletter_html: Mapped[str | None] = mapped_column(Text)
    newsletter_text: Mapped[str | None] = mapped_column(Text)
    stories: Mapped[list['Story']] = relationship(back_populates='issue', cascade='all, delete-orphan', order_by='Story.sort_order')

class Story(Timestamps, Base):
    __tablename__ = 'stories'
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey('issues.id'), index=True)
    slug: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    byte: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(default='')
    source_url: Mapped[str] = mapped_column(default='')
    source_type: Mapped[str]
    sort_order: Mapped[int] = mapped_column(default=0)
    published_at: Mapped[datetime | None]
    issue: Mapped[Issue] = relationship(back_populates='stories')

class WebhookReceipt(Base):
    __tablename__ = 'webhook_receipts'
    id: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=now)

class RateLimit(Base):
    __tablename__ = 'rate_limits'
    key: Mapped[str] = mapped_column(primary_key=True)
    count: Mapped[int]
    expires: Mapped[int]
