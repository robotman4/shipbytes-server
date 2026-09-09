import os
import subprocess
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/sync-and-publish.sh'

@pytest.mark.parametrize('mode,attempts,sleeps,success', [('unchanged',2,1,True),('changed',1,0,True),('published',1,0,True),('giterror',0,0,False),('publisherror',1,0,False)])
def test_sync_retry_contract(tmp_path,mode,attempts,sleeps,success):
    bin=tmp_path/'bin'; bin.mkdir()
    (bin/'git').write_text('''#!/bin/bash
case "$1" in
status) exit 0;;
rev-parse) if [[ "$MODE" == changed && -f "$STATE/fetched" ]]; then echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; else echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; fi;;
fetch) [[ "$MODE" != giterror ]] || exit 1; touch "$STATE/fetched";;
reset) exit 0;;
esac
''')
    (bin/'docker').write_text('''#!/bin/bash
echo attempt >> "$STATE/attempts"
[[ "$MODE" != publisherror ]] || exit 1
if [[ "$MODE" == published ]]; then echo '{"published":1}'; else echo '{"published":0}'; fi
''')
    (bin/'sleep').write_text('''#!/bin/bash
[[ "$1" == 3600 ]] || exit 1
echo sleep >> "$STATE/sleeps"
''')
    for path in bin.iterdir(): path.chmod(0o755)
    env={**os.environ,'PATH':str(bin)+':'+os.environ['PATH'],'MODE':mode,'STATE':str(tmp_path),'PUBLICATIONS_PATH':str(tmp_path),'SHIPBYTES_DEPLOY_PATH':str(tmp_path)}
    result=subprocess.run(['bash',str(SCRIPT)],env=env,capture_output=True,text=True,timeout=10)
    assert (result.returncode==0)==success, result.stderr
    for name,count in [('attempts',attempts),('sleeps',sleeps)]:
        path=tmp_path/name
        assert (len(path.read_text().splitlines()) if path.exists() else 0)==count
