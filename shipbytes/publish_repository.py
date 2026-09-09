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
from .media import store_image
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

def run_repository(root, sessions, config, mailer, git_sha=None, today=None):
    root = Path(root)
    with publication_lock(sessions):
        records = scan(root)
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
                        continue
                    if existing.source_filename != filename:
                        raise ValueError(f'Issue slug conflicts with existing issue: {content.slug}')
                    if existing.source_content_hash != digest and existing.delivery_state != 'new':
                        raise ValueError(f'Cannot change content after provider attempt: {content.slug}')
                for story in content.stories:
                    owner = db.scalar(select(Story).where(Story.slug == story.slug))
                    if owner and (not existing or owner.issue_id != existing.id):
                        raise ValueError(f'Story slug conflicts with database: {story.slug}')
        published = skipped = deferred = 0
        for content, filename, digest in records:
            if content.published_date > today:
                deferred += 1
                continue
            with sessions() as db:
                issue = db.scalar(select(Issue).where(Issue.slug == content.slug))
                if issue and issue.status == 'published':
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
                        data = story.model_dump()
                        data['source_url'] = str(story.source_url)
                        issue.stories.append(Story(**data, sort_order=order))
                    issue.source_filename = filename
                    issue.source_git_sha = sha
                    issue.source_content_hash = digest
                    issue.publication_date = datetime.combine(content.published_date, time())
                    for field in ('image_file', 'image_thumbnail', 'image_alt', 'image_credit', 'image_source_url', 'image_usage'):
                        setattr(issue, field, None)
                    if content.image:
                        stored = store_image(root, content.image, config)
                        if stored:
                            issue.image_file, issue.image_thumbnail = stored
                            issue.image_alt = content.image.alt
                            issue.image_credit = content.image.credit
                            issue.image_source_url = str(content.image.source_url)
                            issue.image_usage = content.image.usage
                        else:
                            log(f'issue {content.slug}: optional image unavailable or invalid; using Ship Bytes default')
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
    args = parser.parse_args()
    try:
        config = settings()
        engine, sessions = database(config.database_url)
        try:
            result = run_repository(args.repository, sessions, config, Mailer(config), args.git_sha)
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
