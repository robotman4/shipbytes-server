import json
from pathlib import Path
from PIL import Image
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_app import setup
from test_repository import repository, CONTENT, write, run
from shipbytes.media import DEFAULT_IMAGE, media_root
from shipbytes.models import Issue
from shipbytes.publication_schema import PublicationImage

SPEC={'type':'source','path':'assets/2026/cover.png','alt':'An example test image','credit':'Test artist','source_url':'https://example.com/image','usage':'Test fixture'}

def asset(root):
    path=root/SPEC['path']; path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(1800,1000),'navy').save(path)
    return path

def test_image_persists_after_source_removed(setup,repository):
    source=asset(repository)
    write(repository,{**CONTENT,'image':SPEC})
    assert run(setup,repository)['published']==1
    source.unlink()
    with setup[1].state.sessions() as db:
        issue=db.scalar(select(Issue))
        cover=issue.cover_url
        assert cover == '/media/issues/test-issue/cover-1200.jpg'
        assert issue.thumbnail_url == '/media/issues/test-issue/cover-600.jpg'
        assert issue.image_usage=='Test fixture'
        assert issue.thumbnail_url in issue.newsletter_html
        assert cover not in issue.newsletter_html
        assert issue.image_type == 'source'
        assert 'Test artist' in issue.newsletter_html
    response=setup[0].get(cover)
    assert response.status_code==200
    assert response.headers['content-type']=='image/jpeg'
    assert len(response.content)<=250000
    assert run(setup,repository)['skipped']==1
    setup[2].send_broadcast.assert_called_once()
    page=setup[0].get('/issues/test-issue').text
    assert 'og:image' in page and 'An example test image' in page
    assert cover in page
    response=setup[0].get('/stories/test-story')
    assert response.status_code==200
    assert f'<figure class="issue-cover"><img src="{cover}"' in response.text
    assert 'alt="An example test image"' in response.text
    assert '>Test artist</a>' in response.text
    assert f'content="http://localhost:8000{cover}"' in response.text

@pytest.mark.parametrize('kind',['missing','broken','oversized','symlink','pixels'])
def test_bad_optional_image_uses_default(setup,repository,kind,tmp_path):
    path=repository/SPEC['path'];path.parent.mkdir(parents=True,exist_ok=True)
    if kind=='broken':path.write_bytes(b'not an image')
    if kind=='oversized':path.write_bytes(b'x'*(2*1024*1024+1))
    if kind=='symlink':
        outside=tmp_path/'outside.png'; Image.new('RGB',(30,30)).save(outside);path.symlink_to(outside)
    if kind=='pixels':Image.new('RGB',(2100,2100)).save(path)
    write(repository,{**CONTENT,'image':SPEC})
    assert run(setup,repository)['published']==1
    with setup[1].state.sessions() as db:
        assert db.scalar(select(Issue)).cover_url==DEFAULT_IMAGE
    assert setup[0].get(DEFAULT_IMAGE).status_code==200

@pytest.mark.parametrize('path',['../outside.png','assets/../outside.png','/tmp/image.jpg','https://example.com/x.jpg','assets/image.svg'])
def test_invalid_asset_path(path):
    with pytest.raises(ValidationError):PublicationImage(**{**SPEC,'path':path})

def test_default_for_existing_issue(setup,repository):
    write(repository)
    run(setup,repository)
    for url in ('/','/issues','/issues/test-issue'):
        assert DEFAULT_IMAGE in setup[0].get(url).text
    page=setup[0].get('/stories/test-story').text
    assert f'<figure class="issue-cover"><img src="{DEFAULT_IMAGE}"' in page
    assert 'alt="Ship Bytes maritime technology logo"' in page
    with setup[1].state.sessions() as db:
        assert DEFAULT_IMAGE in db.scalar(select(Issue)).newsletter_html

@pytest.mark.parametrize('kind', ['generated','licensed','source','own'])
def test_supported_image_types(kind):
    spec=PublicationImage(**{**SPEC,'type':kind})
    assert spec.type==kind

@pytest.mark.parametrize('change', [{'type':'unknown'}, {'type':None}, {'source_url':None}, {'usage':''}, {'usage':'   '}])
def test_invalid_type_or_source_attribution(change):
    with pytest.raises(ValidationError):PublicationImage(**{**SPEC,**change})

def test_generated_default_attribution():
    spec=PublicationImage(path=SPEC['path'],type='generated',alt='Illustration',source_url=None)
    assert spec.credit=='Ship Bytes'
    assert spec.usage=='generated'
    assert spec.source_url is None

def test_derivatives_and_original(setup,repository):
    source=asset(repository)
    raw=source.read_bytes()
    write(repository,{**CONTENT,'image':{**SPEC,'type':'generated','source_url':None}})
    run(setup,repository)
    directory=media_root(setup[3])/'issues'/'test-issue'
    assert (directory/'source.png').read_bytes()==raw
    for name,size in [('cover-1200.jpg',(1200,630)),('cover-600.jpg',(600,315))]:
        with Image.open(directory/name) as image:
            assert image.size==size
    page=setup[0].get('/issues/test-issue').text
    assert 'content="http://localhost:8000/media/issues/test-issue/cover-1200.jpg"' in page
