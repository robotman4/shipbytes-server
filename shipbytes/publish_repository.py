"""Validate the full checkout before importing or sending anything."""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .config import settings
from .db import database
from .mail import Mailer
from .media import store_image, media_root
from .assets import AssetResolver, validate_folder
from .models import Issue, Story
from .publication_schema import RepositoryIssue
from .publishing import publication_lock, publish_locked

ZONE = ZoneInfo('Europe/Stockholm')

def log(message):
    print(f'{datetime.now(ZONE).isoformat(timespec="seconds")} {message}', file=sys.stderr, flush=True)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result

def checkout_sha(root):
    git = root / '.git'
    head = (git / 'HEAD').read_text().strip()
    if head.startswith('ref: '):
        ref = head[5:]
        if not ref.startswith('refs/') or '..' in ref:
            raise ValueError('Invalid Git HEAD')
        path = git / ref
        if path.exists():
            head = path.read_text().strip()
        else:
            entries = (git / 'packed-refs').read_text().splitlines()
            head = next(line.split()[0] for line in entries if line.endswith(' '+ref))
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head):
        raise ValueError('No valid checkout commit; use --git-sha for an exported tree')
    return head

def scan(root):
    if not root.is_dir():
        raise ValueError('Publications directory does not exist')
    records, slugs, story_slugs = [], set(), set()
    for file in sorted((root / 'issues').rglob('*.json')):
        relative = file.relative_to(root).as_posix()
        try:
            if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
                raise ValueError('Publication symlinks are not supported')
            if file.stat().st_size > 262144:
                raise ValueError('Publication exceeds 256 KB')
            raw = file.read_bytes()
            data = json.loads(raw, object_pairs_hook=unique_object)
            issue = RepositoryIssue.model_validate(data)
            if issue.slug in slugs:
                raise ValueError('Duplicate issue slug')
            if story_slugs.intersection(s.slug for s in issue.stories):
                raise ValueError('Duplicate story slug across files')
            slugs.add(issue.slug)
            story_slugs.update(s.slug for s in issue.stories)
            records.append((issue, relative, hashlib.sha256(raw).hexdigest()))
        except Exception as exc:
            # Validation errors may contain input; log only filename and error class.
            raise ValueError(f'Invalid publication {relative}: {type(exc).__name__}') from None
    return sorted(records, key=lambda row: (row[0].published_date, row[0].slug))

IMAGE_FIELDS = ('image_file', 'image_thumbnail', 'image_type', 'image_alt', 'image_credit', 'image_source_url', 'image_usage', 'image_reference')

def prepare_image(root, spec, config, resolver, existing, slug, collection, refresh):
    if spec is None:
        # Missing metadata in old JSON must not clear existing published media.
        return None
    if spec.provider == 'dropbox':
        validate_folder(spec.shared_folder_url, config.dropbox_shared_folder_url)
    reference = spec.model_dump_json()
    if not refresh and existing and existing.image_reference == reference:
        paths = (existing.image_file, existing.image_thumbnail)
        if all(path and path.startswith('/media/') and (media_root(config) / path.removeprefix('/media/')).is_file() for path in paths):
            return {name: getattr(existing, name) for name in IMAGE_FIELDS}
    stored = store_image(root, spec, config, slug, resolver=resolver, collection=collection)
    if not stored:
        log(f'{collection}/{slug}: optional repository image unavailable; using default')
        return None if existing else {name: None for name in IMAGE_FIELDS}
    return dict(image_file=stored[0], image_thumbnail=stored[1], image_type=spec.type,
                image_alt=spec.alt, image_credit=spec.credit,
                image_source_url=str(spec.source_url) if spec.source_url else None,
                image_usage=spec.usage, image_reference=reference)

def apply_image(record, values):
    if values is not None:
        for key, value in values.items():
            setattr(record, key, value)


def run_repository(root, sessions, config, mailer, git_sha=None, today=None, refresh_images=False):
    root = Path(root)
    with publication_lock(sessions):
        records = scan(root)
        for content, _, _ in records:
            for spec in [content.image, *(story.image for story in content.stories)]:
                if spec and spec.provider == 'dropbox':
                    validate_folder(spec.shared_folder_url, config.dropbox_shared_folder_url)
        sha = git_sha or checkout_sha(root)
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha):
            raise ValueError('Invalid source Git SHA')
        today = today or datetime.now(ZONE).date()
        # Preflight all DB conflicts, including future files, before any email.
        with sessions() as db:
            for content, filename, digest in records:
                existing = db.scalar(select(Issue).where(Issue.slug == content.slug))
                if existing:
                    if existing.status == 'published':
                        if {story.slug for story in existing.stories} != {story.slug for story in content.stories}:
                            raise ValueError('Cannot change story membership of a published issue')
                        continue
                    if existing.source_filename != filename:
                        raise ValueError(f'Issue slug conflicts with existing issue: {content.slug}')
                    if existing.source_content_hash != digest and existing.delivery_state != 'new':
                        raise ValueError(f'Cannot change content after provider attempt: {content.slug}')
                for story in content.stories:
                    owner = db.scalar(select(Story).where(Story.slug == story.slug))
                    if owner and (not existing or owner.issue_id != existing.id):
                        raise ValueError(f'Story slug conflicts with database: {story.slug}')
        # Resolve every required image before any provider send or DB image update.
        resolver = AssetResolver(root, config)
        prepared = {}
        with sessions() as db:
            for content, filename, digest in records:
                if content.published_date > today:
                    continue
                existing = db.scalar(select(Issue).where(Issue.slug == content.slug))
                if existing and existing.status != 'published' and existing.delivery_state != 'new':
                    continue
                old_stories = {story.slug: story for story in existing.stories} if existing else {}
                prepared[content.slug] = (
                    prepare_image(root, content.image, config, resolver, existing, content.slug, 'issues', refresh_images),
                    {story.slug: prepare_image(root, story.image, config, resolver, old_stories.get(story.slug),
                        story.slug, 'stories', refresh_images) for story in content.stories})
        published = skipped = deferred = 0
        for content, filename, digest in records:
            if content.published_date > today:
                deferred += 1
                continue
            with sessions() as db:
                issue = db.scalar(select(Issue).where(Issue.slug == content.slug))
                if issue and issue.status == 'published':
                    cover, images = prepared[content.slug]
                    apply_image(issue, cover)
                    for story in issue.stories:
                        apply_image(story, images[story.slug])
                    db.commit()
                    skipped += 1
                    log(f'issue {content.slug} already published; skipped')
                    continue
                if not issue:
                    issue = Issue(slug=content.slug, status='pending')
                    db.add(issue)
                if issue.delivery_state in (None, 'new'):
                    for name in ('title', 'subject', 'intro'):
                        setattr(issue, name, getattr(content, name))
                    issue.stories.clear()
                    db.flush()
                    for order, story in enumerate(content.stories):
                        data = story.model_dump(exclude={'image'})
                        data['source_url'] = str(story.source_url)
                        issue.stories.append(Story(**data, sort_order=order))
                    issue.source_filename = filename
                    issue.source_git_sha = sha
                    issue.source_content_hash = digest
                    issue.publication_date = datetime.combine(content.published_date, time())
                    cover, images = prepared[content.slug]
                    apply_image(issue, cover)
                    for story in issue.stories:
                        apply_image(story, images[story.slug])
                db.commit()
            log(f'publishing issue {content.slug}')
            result = publish_locked(sessions, config, mailer, content.slug)
            log(f'Resend broadcast: {result["broadcast_id"]}; issue {content.slug} published')
            published += 1
        if not published:
            log('no unpublished issues published')
        return {'published': published, 'skipped': skipped, 'deferred': deferred}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', type=Path)
    parser.add_argument('--git-sha', help='Commit of the host-synchronized checkout')
    parser.add_argument('--refresh-images', action='store_true', help='Re-fetch images, including published issues, without resending published newsletters')
    args = parser.parse_args()
    try:
        config = settings()
        engine, sessions = database(config.database_url)
        try:
            result = run_repository(args.repository, sessions, config, Mailer(config), args.git_sha, refresh_images=args.refresh_images)
        finally:
            engine.dispose()
        print(json.dumps(result))
        return 0
    except Exception as exc:
        # Do not dump provider payloads, settings, SQL parameters, or tracebacks.
        from .publishing import PublicationError
        reason = str(exc) if isinstance(exc, (ValueError, PublicationError)) and not hasattr(exc, 'errors') else type(exc).__name__
        log(f'publisher failed: {reason}')
        return 1

if __name__ == '__main__':
    sys.exit(main())
