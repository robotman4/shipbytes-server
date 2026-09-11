import hashlib
import io
import json
import socket
from datetime import date
from unittest.mock import Mock
import httpx
import pytest
from PIL import Image
from sqlalchemy import select
from test_app import setup
from test_repository import repository, CONTENT, SHA, write, run
from shipbytes.assets import AssetResolver, AssetError, download
from shipbytes.models import Issue, Story
from shipbytes.media import media_root
from shipbytes.publication_schema import PublicationImage, RepositoryIssue
from shipbytes.publish_repository import run_repository

SPEC = dict(url='https://www.dropbox.com/scl/fo/test/folder?dl=1&preview=cover.jpg',
            type='generated', alt='Illustration of a vessel')

def photo(color='navy'):
    buffer = io.BytesIO()
    Image.new('RGB', (1200, 630), color).save(buffer, 'JPEG', quality=95)
    return buffer.getvalue()

def content():
    return {**CONTENT, 'image': SPEC, 'stories': [{**CONTENT['stories'][0], 'image': {**SPEC, 'url': 'https://www.dropbox.com/scl/fo/test/folder?dl=1&preview=test-story.jpg'}}]}

def test_schema():
    issue = RepositoryIssue.model_validate(content())
    assert str(issue.image.url) == SPEC['url']
    assert issue.stories[0].image.url is not None
    assert RepositoryIssue.model_validate(CONTENT).stories[0].image is None

@pytest.mark.parametrize('url', ['ftp://example.com/a.jpg','file:///etc/passwd','data:image/png,abc','not-a-url','https://user:password@example.com/a.jpg'])
def test_invalid_url(url):
    with pytest.raises(ValueError): PublicationImage(**{**SPEC, 'url':url})

def test_full_import_and_cached_refresh(setup, repository, monkeypatch):
    raw = photo()
    fetch = Mock(return_value=raw)
    monkeypatch.setattr('shipbytes.assets.download', fetch)
    write(repository, content())
    assert run(setup, repository)['published'] == 1
    assert fetch.call_count == 2
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
    assert fetch.call_count == 2
    # Overwrites at Dropbox stay pinned until an explicit refresh (or reference/hash change).
    raw2 = photo('teal')
    fetch.return_value = raw2
    result = run_repository(repository, setup[1].state.sessions, setup[3], setup[2], SHA,
                            today=date(2026,9,15), refresh_images=True)
    assert result['skipped'] == 1
    with setup[1].state.sessions() as db:
        issue = db.scalar(select(Issue))
        assert issue.cover_url != cover
        assert issue.resend_broadcast_id == broadcast
        assert issue.status == 'published'
    setup[2].send_broadcast.assert_called_once()
    assert fetch.call_count == 4

@pytest.mark.parametrize('raw', [None, b'<html>not an image</html>', b'a'*(2*1024*1024+1)])
def test_required_failure_no_email(setup, repository, monkeypatch, raw):
    monkeypatch.setattr('shipbytes.assets.download', Mock(side_effect=AssetError('HTTP 404')) if raw is None else Mock(return_value=raw))
    write(repository, {**CONTENT, 'image':SPEC})
    with pytest.raises(AssetError): run(setup, repository)
    setup[2].create_broadcast.assert_not_called()

def test_story_image_failure_preserves_published_issue(setup, repository, monkeypatch):
    write(repository); run(setup, repository)
    monkeypatch.setattr('shipbytes.assets.download', Mock(side_effect=[photo(),AssetError('HTTP 404')]))
    write(repository, content())
    with pytest.raises(AssetError): run(setup, repository)
    with setup[1].state.sessions() as db:
        issue = db.scalar(select(Issue))
        assert issue.status == 'published'
        assert issue.image_file is None
    setup[2].send_broadcast.assert_called_once()

def test_checksum_mismatch(setup, repository, monkeypatch):
    monkeypatch.setattr('shipbytes.assets.download', Mock(return_value=photo()))
    write(repository,{**CONTENT,'image':{**SPEC,'sha256':'0'*64}})
    with pytest.raises(AssetError): run(setup,repository)
    setup[2].send_broadcast.assert_not_called()

@pytest.fixture
def fake_dns(monkeypatch):
    def resolve(host, port, **kwargs):
        address = '127.0.0.1' if host in ('internal.example', '127.0.0.1') else '93.184.216.34'
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]
    monkeypatch.setattr('shipbytes.assets.socket.getaddrinfo',resolve)

@pytest.mark.parametrize('target', ['http://127.0.0.1/a','https://internal.example/a','file:///etc/passwd'])
def test_redirect_rejected(monkeypatch, fake_dns, target):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'Location':target})
    real = httpx.Client
    monkeypatch.setattr('shipbytes.assets.httpx.Client', lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(AssetError): download(SPEC['url'],1000)
    assert len(calls)==1

def test_download_size_limit(monkeypatch, fake_dns):
    real=httpx.Client
    monkeypatch.setattr('shipbytes.assets.httpx.Client',lambda **kwargs:real(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*1001)),**kwargs))
    with pytest.raises(AssetError): download(SPEC['url'],1000)

def test_redirect_binary_jpeg_import(setup, repository, monkeypatch, fake_dns):
    calls=[]
    raw=photo()
    def handler(request):
        # The network transport gets only the validated IP; Host and SNI remain original.
        assert request.url.host=='93.184.216.34'
        calls.append(request.headers['host'])
        assert request.extensions['sni_hostname']==request.headers['host']
        if request.headers['host']=='www.dropbox.com':
            return httpx.Response(302,headers={'Location':'https://dl.dropboxusercontent.com/cover.jpg'})
        return httpx.Response(200,headers={'Content-Type':'application/binary'},content=raw)
    real=httpx.Client
    monkeypatch.setattr('shipbytes.assets.httpx.Client',lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs))
    write(repository,{**CONTENT,'image':SPEC})
    assert run(setup,repository)['published']==1
    assert calls==['www.dropbox.com','dl.dropboxusercontent.com']

def test_changed_url_refreshes(setup,repository,monkeypatch):
    fetch=Mock(return_value=photo())
    monkeypatch.setattr('shipbytes.assets.download',fetch)
    write(repository,{**CONTENT,'image':SPEC}); run(setup,repository)
    new={**SPEC,'url':'https://www.dropbox.com/new-image.jpg'}
    write(repository,{**CONTENT,'image':new})
    assert run(setup,repository)['skipped']==1
    assert fetch.call_count==2
    assert run(setup,repository)['skipped']==1
    assert fetch.call_count==2
    setup[2].send_broadcast.assert_called_once()
    with setup[1].state.sessions() as db:
        assert json.loads(db.scalar(select(Issue)).image_reference)['url']==new['url']

@pytest.mark.parametrize('address',['127.0.0.1','10.1.2.3','169.254.169.254','::1','fc00::1','224.0.0.1'])
def test_private_dns_rejected(monkeypatch,address):
    monkeypatch.setattr('shipbytes.assets.socket.getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))])
    with pytest.raises(AssetError): download('https://private.example/image.jpg')

def test_failure_log_redacts_query(setup,repository,monkeypatch):
    monkeypatch.setattr('shipbytes.assets.download',Mock(side_effect=AssetError('HTTP 403')))
    write(repository,{**CONTENT,'image':{**SPEC,'url':'https://www.dropbox.com/image?secret=hidden'}})
    with pytest.raises(AssetError) as error: run(setup,repository)
    assert 'issues/test-issue' in str(error.value)
    assert 'www.dropbox.com' in str(error.value)
    assert 'hidden' not in str(error.value)
