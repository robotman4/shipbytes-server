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

SPEC={'path':'assets/2026/cover.png','alt':'An example test image','credit':'Test artist','source_url':'https://example.com/image','usage':'Test fixture'}

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
        assert cover.startswith('/media/')
        assert issue.image_usage=='Test fixture'
        assert cover in issue.newsletter_html
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

@pytest.mark.parametrize('kind',['missing','broken','oversized','symlink','pixels'])
def test_bad_optional_image_uses_default(setup,repository,kind,tmp_path):
    path=repository/SPEC['path'];path.parent.mkdir(parents=True,exist_ok=True)
    if kind=='broken':path.write_bytes(b'not an image')
    if kind=='oversized':path.write_bytes(b'x'*(4*1024*1024+1))
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
    with setup[1].state.sessions() as db:
        assert DEFAULT_IMAGE in db.scalar(select(Issue)).newsletter_html
