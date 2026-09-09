import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from test_app import setup, PAYLOAD
from shipbytes.models import Issue
from shipbytes.mail import MailError, RetryableMailError
from shipbytes.publish_repository import run_repository
from shipbytes.publishing import PublicationError

SHA = 'a' * 40
CONTENT = {**PAYLOAD, 'schema_version': 1, 'published_date': '2026-09-15'}

@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'publications'
    (root / 'issues' / '2026').mkdir(parents=True)
    return root

def write(root, content=CONTENT, name='2026-09-15.json'):
    path = root / 'issues' / '2026' / name
    path.write_text(json.dumps(content))
    return path

def run(setup, root):
    _, app, mailer, config = setup
    return run_repository(root, app.state.sessions, config, mailer, SHA, today=date(2026,9,15))

def test_valid_import_publication_and_skip(setup, repository):
    write(repository)
    assert run(setup, repository)['published'] == 1
    assert run(setup, repository)['skipped'] == 1
    setup[2].send_broadcast.assert_called_once()
    with setup[1].state.sessions() as db:
        issue = db.scalar(select(Issue))
        assert issue.source_filename == 'issues/2026/2026-09-15.json'
        assert issue.source_git_sha == SHA
        assert issue.status == 'published'
        assert issue.resend_broadcast_id == 'broadcast-1'
    assert setup[0].get('/issues/test-issue').status_code == 200

@pytest.mark.parametrize('change', [
    {'schema_version':2}, {'schema_version':True}, {'schema_version':'1'},
    {'stories':[]}, {'published_date':123}, {'unknown':'field'},
    {'stories':CONTENT['stories']*2},
    {'stories':[{**CONTENT['stories'][0], 'source_url':''}]},
    {'stories':[{**CONTENT['stories'][0], 'source_url':'javascript:alert(1)'}]},
    *[{'stories':[{k:v for k,v in CONTENT['stories'][0].items() if k != missing}]} for missing in ('byte','summary','title')]
])
def test_invalid_schema_never_sends(setup, repository, change):
    write(repository, {**CONTENT, **change})
    with pytest.raises(ValueError): run(setup, repository)
    setup[2].create_broadcast.assert_not_called()

@pytest.mark.parametrize('raw', ['{bad', '{"schema_version":1,"schema_version":1}', 'null'])
def test_malformed_json(setup, repository, raw):
    write(repository).write_text(raw)
    with pytest.raises(ValueError): run(setup, repository)
    setup[2].send_broadcast.assert_not_called()

def test_duplicate_issue_slug(setup, repository):
    write(repository)
    write(repository, name='duplicate.json')
    with pytest.raises(ValueError): run(setup, repository)
    setup[2].send_broadcast.assert_not_called()

def test_invalid_future_file_prevents_partial_send(setup, repository):
    write(repository)
    write(repository, {**CONTENT, 'slug':'future','published_date':'2027-01-01','stories':[]}, 'future.json')
    with pytest.raises(ValueError): run(setup, repository)
    setup[2].send_broadcast.assert_not_called()

def test_future_issue_deferred(setup, repository):
    write(repository, {**CONTENT,'published_date':'2027-01-01'})
    assert run(setup, repository)['deferred'] == 1
    setup[2].create_broadcast.assert_not_called()

@pytest.mark.parametrize('stage', ['create_broadcast','send_broadcast'])
def test_definite_failure_retries_safely(setup, repository, stage):
    write(repository)
    getattr(setup[2], stage).side_effect = RetryableMailError()
    with pytest.raises(PublicationError): run(setup, repository)
    with setup[1].state.sessions() as db:
        issue=db.scalar(select(Issue))
        assert issue.status == 'failed'
        assert issue.published_at is None
    assert setup[0].get('/issues/test-issue').status_code == 404
    getattr(setup[2], stage).side_effect = None
    assert run(setup, repository)['published'] == 1
    assert setup[2].create_broadcast.call_count == (2 if stage == 'create_broadcast' else 1)
    assert run(setup, repository)['skipped'] == 1

@pytest.mark.parametrize('stage', ['create_broadcast','send_broadcast'])
def test_uncertain_failure_never_retries(setup, repository, stage):
    write(repository)
    getattr(setup[2], stage).side_effect = MailError()
    with pytest.raises(PublicationError): run(setup, repository)
    with pytest.raises(PublicationError): run(setup, repository)
    assert getattr(setup[2], stage).call_count == 1

def test_two_publishers_and_api_cannot_duplicate(setup, repository):
    write(repository)
    def invoke(_):
        try: return run(setup,repository)
        except PublicationError: return {'locked':True}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(invoke,range(2)))
    assert sum(r.get('published',0) for r in results)==1
    setup[2].send_broadcast.assert_called_once()
    response=setup[0].post('/api/v1/issues/test-issue/publish',headers={'Authorization':'Bearer test-token'})
    assert response.status_code==409

def test_existing_api_draft_collision(setup, repository):
    setup[0].post('/api/v1/issues',json=PAYLOAD,headers={'Authorization':'Bearer test-token'})
    write(repository)
    with pytest.raises(ValueError): run(setup,repository)
    setup[2].send_broadcast.assert_not_called()

def test_render_failure_retry(setup, repository, monkeypatch):
    from shipbytes import publishing
    write(repository)
    original=publishing.Environment
    monkeypatch.setattr(publishing,'Environment',Mock(side_effect=RuntimeError('broken template')))
    with pytest.raises(PublicationError): run(setup,repository)
    setup[2].create_broadcast.assert_not_called()
    monkeypatch.setattr(publishing,'Environment',original)
    assert run(setup,repository)['published']==1

def test_corrected_content_after_safe_failure(setup, repository):
    write(repository)
    setup[2].create_broadcast.side_effect=RetryableMailError()
    with pytest.raises(PublicationError): run(setup,repository)
    write(repository,{**CONTENT,'title':'Corrected title'})
    setup[2].create_broadcast.side_effect=None
    assert run(setup,repository)['published']==1


def test_chronological_order(setup, repository):
    write(repository,{**CONTENT,'published_date':'2026-09-14','slug':'earlier'})
    write(repository,{**CONTENT,'slug':'later','stories':[{**CONTENT['stories'][0],'slug':'other-story'}]},'later.json')
    order=[]
    setup[2].create_broadcast.side_effect=lambda issue: order.append(issue.slug) or 'broadcast-'+issue.slug
    assert run(setup,repository)['published']==2
    assert order==['earlier','later']

@pytest.mark.parametrize('state', ['creating','sending','uncertain'])
def test_crash_markers_block_provider_writes(setup, repository, state):
    write(repository)
    setup[2].create_broadcast.side_effect=RetryableMailError()
    with pytest.raises(PublicationError): run(setup,repository)
    with setup[1].state.sessions() as db:
        issue=db.scalar(select(Issue)); issue.delivery_state=state; db.commit()
    setup[2].reset_mock()
    with pytest.raises(PublicationError): run(setup,repository)
    setup[2].create_broadcast.assert_not_called()
    setup[2].send_broadcast.assert_not_called()

def test_separate_processes_share_lock(setup, repository):
    import multiprocessing
    import time
    write(repository)
    context=multiprocessing.get_context('fork')
    queue=context.Queue()
    start=context.Event()
    def child():
        setup[1].state.engine.dispose(close=False)
        def sent(_):
            queue.put('sent')
            time.sleep(.2)
        setup[2].send_broadcast.side_effect=sent
        start.wait(5)
        try: run(setup,repository)
        except PublicationError: pass
    processes=[context.Process(target=child) for _ in range(2)]
    for process in processes: process.start()
    start.set()
    for process in processes:
        process.join(10)
        assert process.exitcode==0
    assert queue.get(timeout=2)=='sent'
    from queue import Empty
    with pytest.raises(Empty): queue.get(timeout=.2)
