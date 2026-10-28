"""Exercise scan/link coverage through a real CLI, using disposable fixtures only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--exe', required=True)
parser.add_argument('--workspace', required=True)
parser.add_argument('--result', required=True)
args = parser.parse_args()
exe = Path(args.exe).resolve(strict=True)
root = Path(args.workspace).resolve() / ('scan-links-' + uuid.uuid4().hex)
root.mkdir(parents=True)
scan_root = root / '中文 scan root'
ordinary = scan_root / 'ordinary environment'
outside = root / 'outside environment'
for path in (ordinary, outside):
    path.mkdir(parents=True)
    (path / 'pyvenv.cfg').write_text('version = 3.13.9\n', encoding='utf-8')
links = [scan_root / name for name in ('symlink', 'junction', 'cycle')]
os.symlink(outside, links[0], target_is_directory=True)
proc = subprocess.run(
    ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
     "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:AIROOT_TEST_LINK -Value $env:AIROOT_TEST_TARGET | Out-Null"],
    env=dict(os.environ, AIROOT_TEST_LINK=str(links[1]), AIROOT_TEST_TARGET=str(outside)),
    capture_output=True, text=True, timeout=30)
assert proc.returncode == 0, proc.stderr
os.symlink(scan_root, links[2], target_is_directory=True)
base = [str(exe), '--data-root', str(root / 'data'), '--json']

def cli(*commands):
    proc = subprocess.run(base + list(commands), capture_output=True, text=True,
                          encoding='utf-8', timeout=45)
    result = json.loads(proc.stdout)
    assert proc.returncode == 0 and result['ok'], result
    return result['data']

def norm(path):
    return os.path.normcase(str(path))

def reports_links(result):
    skipped = {norm(item['path']) for item in result['skipped']}
    assert all(norm(link) in skipped for link in links), result

try:
    cli('config', 'set', '--env-root', str(root / 'env'), '--tools-root',
        str(root / 'tools'), '--initialize')
    negative = cli('resource', 'find', outside.name, '--root', str(scan_root))
    assert not negative['matches'] and not negative['uncovered'], negative
    reports_links(negative)
    positive = cli('resource', 'find', ordinary.name, '--root', str(scan_root))
    assert len(positive['matches']) == 1, positive
    assert norm(positive['matches'][0]['path']) == norm(ordinary), positive
    scan = cli('scan', 'start', '--root', str(scan_root))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        scan = cli('scan', 'status', scan['id'])
        if scan['coverage'].get('queue_complete'):
            break
        time.sleep(0.1)
    assert scan['coverage']['queue_complete'], scan
    assert scan['coverage']['state'] == 'partial', scan
    reports_links(scan)
    rows = cli('env', 'list')
    assert len(rows) == 1 and norm(rows[0]['path']) == norm(ordinary), rows
    missing = root / 'missing root'
    missing_result = cli('resource', 'find', 'absent-name', '--root', str(missing))
    assert any(norm(item['path']) == norm(missing) for item in missing_result['skipped'])
    result = dict(exe=str(exe), exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                  root=str(root), scan=scan, positive=positive, negative=negative,
                  missing=missing_result,
                  passed=['Real symlink, junction and cycle exclusions reported by both walkers',
                          'Ordinary environment found; external linked environment not registered',
                          'Completed queue reports partial coverage; supplementary queue finishes',
                          'Missing supplementary root explicitly reported'])
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(result=args.result, passed=result['passed']), ensure_ascii=False))
finally:
    cli('worker-stop')
