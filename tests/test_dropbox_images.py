import hashlib
import io
import json
import zipfile
from datetime import date
from unittest.mock import Mock
import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from test_app import setup
from test_repository import repository, CONTENT, SHA, write, run
from shipbytes.assets import AssetResolver, AssetError, DEFAULT_DROPBOX_FOLDER, download
from shipbytes.models import Issue, Story
from shipbytes.media import media_root
from shipbytes.publication_schema import PublicationImage, RepositoryIssue
from shipbytes.publish_repository import run_repository

SPEC = dict(provider='dropbox', shared_folder_url=DEFAULT_DROPBOX_FOLDER,
            path='2026/2026-09-15/cover.jpg', type='generated', alt='Illustration of a vessel')

def photo(color='navy'):
    buffer = io.BytesIO()
    Image.new('RGB', (1200, 630), color).save(buffer, 'JPEG', quality=95)
    return buffer.getvalue()

def archive(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, raw in entries.items():
            z.writestr(name, raw)
    return buffer.getvalue()

def content():
    return {**CONTENT, 'image': SPEC, 'stories': [{**CONTENT['stories'][0], 'image': {**SPEC, 'path': '2026/2026-09-15/test-story.jpg'}}]}

def test_schema():
    issue = RepositoryIssue.model_validate(content())
    assert issue.image.provider == 'dropbox'
    assert issue.stories[0].image.provider == 'dropbox'
    assert RepositoryIssue.model_validate(CONTENT).stories[0].image is None

@pytest.mark.parametrize('path', ['../a.jpg','/a.jpg','https://example.com/a.jpg','2026/../a.jpg','a\\b.jpg','a//b.jpg','a/%2e%2e/b.jpg','a.svg','./a.jpg'])
def test_paths(path):
    with pytest.raises(ValueError):
        PublicationImage(**{**SPEC, 'path': path})

@pytest.mark.parametrize('url', ['https://evil.example/a','http://www.dropbox.com/scl/fo/a/b?rlkey=x','https://www.dropbox.com/scl/fo/other/folder?rlkey=other'])
def test_untrusted_folder(setup, repository, monkeypatch, url):
    fetch = Mock(); monkeypatch.setattr('shipbytes.assets.download', fetch)
    write(repository, {**CONTENT, 'image': {**SPEC, 'shared_folder_url': url}})
    with pytest.raises(ValueError): run(setup, repository)
    fetch.assert_not_called()
    setup[2].create_broadcast.assert_not_called()

def test_full_import_and_cached_refresh(setup, repository, monkeypatch):
    raw = photo()
    fetch = Mock(return_value=archive({SPEC['path']: raw, content()['stories'][0]['image']['path']: raw}))
    monkeypatch.setattr('shipbytes.assets.download', fetch)
    write(repository, content())
    assert run(setup, repository)['published'] == 1
    fetch.assert_called_once()
    with setup[1].state.sessions() as db:
        issue = db.scalar(select(Issue)); story = db.scalar(select(Story))
        cover, story_cover = issue.cover_url, story.cover_url
        assert '/issues/test-issue/' in cover
        assert '/stories/test-story/' in story_cover
        assert hashlib.sha256(raw).hexdigest() in cover
        for item in (issue, story):
            for url, size in [(item.cover_url,(1200,630)),(item.thumbnail_url,(600,315))]:
                with Image.open(media_root(setup[3])/url.removeprefix('/media/')) as im:
                    assert im.size == size
        broadcast = issue.resend_broadcast_id
    page = setup[0].get('/stories/test-story').text
    assert f'<img src="{story_cover}"' in page
    assert 'Illustration · Ship Bytes' in page
    assert 'dropbox.com' not in page
    assert '/media/stories/' in setup[0].get('/').text
    assert run(setup, repository)['skipped'] == 1
    fetch.assert_called_once()
    # Overwrites at Dropbox stay pinned until an explicit refresh (or reference/hash change).
    raw2 = photo('teal')
    fetch.return_value = archive({SPEC['path']:raw2, content()['stories'][0]['image']['path']:raw2})
    result = run_repository(repository, setup[1].state.sessions, setup[3], setup[2], SHA,
                            today=date(2026,9,15), refresh_images=True)
    assert result['skipped'] == 1
    with setup[1].state.sessions() as db:
        issue = db.scalar(select(Issue))
        assert issue.cover_url != cover
        assert issue.resend_broadcast_id == broadcast
        assert issue.status == 'published'
    setup[2].send_broadcast.assert_called_once()
    assert fetch.call_count == 2

@pytest.mark.parametrize('raw', [None, b'<html>not an image</html>', b'a'*(2*1024*1024+1)])
def test_required_failure_no_email(setup, repository, monkeypatch, raw):
    monkeypatch.setattr('shipbytes.assets.download', Mock(return_value=archive({} if raw is None else {SPEC['path']:raw})))
    write(repository, {**CONTENT, 'image':SPEC})
    with pytest.raises(AssetError): run(setup, repository)
    setup[2].create_broadcast.assert_not_called()

def test_story_image_failure_preserves_published_issue(setup, repository, monkeypatch):
    write(repository); run(setup, repository)
    monkeypatch.setattr('shipbytes.assets.download', Mock(return_value=archive({SPEC['path']:photo()})))
    write(repository, content())
    with pytest.raises(AssetError): run(setup, repository)
    with setup[1].state.sessions() as db:
        issue = db.scalar(select(Issue))
        assert issue.status == 'published'
        assert issue.image_file is None
    setup[2].send_broadcast.assert_called_once()

def test_checksum_mismatch(setup, repository, monkeypatch):
    monkeypatch.setattr('shipbytes.assets.download', Mock(return_value=archive({SPEC['path']:photo()})))
    write(repository,{**CONTENT,'image':{**SPEC,'sha256':'0'*64}})
    with pytest.raises(AssetError): run(setup,repository)
    setup[2].send_broadcast.assert_not_called()

def test_api_child_download(setup, repository, monkeypatch):
    config = setup[3]; config.dropbox_app_key = 'test-key'; config.dropbox_app_secret = 'test-secret'
    fetch = Mock(return_value=photo()); monkeypatch.setattr('shipbytes.assets.download', fetch)
    AssetResolver(repository, config).read(PublicationImage(**SPEC))
    kwargs = fetch.call_args.kwargs
    assert json.loads(kwargs['headers']['Dropbox-API-Arg']) == {'url': DEFAULT_DROPBOX_FOLDER, 'path':'/'+SPEC['path']}
    assert kwargs['auth'] == ('test-key','test-secret')

@pytest.mark.parametrize('target', ['http://127.0.0.1/a','https://evil.example/a','https://www.dropbox.com.evil.example/a'])
def test_redirect_rejected(monkeypatch, target):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'Location':target})
    real = httpx.Client
    monkeypatch.setattr('shipbytes.assets.httpx.Client', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(AssetError): download(DEFAULT_DROPBOX_FOLDER,1000)
    assert len(calls)==1

def test_download_size_limit(monkeypatch):
    real=httpx.Client
    monkeypatch.setattr('shipbytes.assets.httpx.Client',lambda **kwargs:real(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*1001)),**kwargs))
    with pytest.raises(AssetError): download(DEFAULT_DROPBOX_FOLDER,1000)
