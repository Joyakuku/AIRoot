"""Real packaged-CLI barrier regression in a new isolated fixture.
Operation checkpoints are constructed only in the test's own registry; external
usage uses an actual child process. No install/delete steps are applied.
"""
import argparse, datetime, hashlib, json, pathlib, subprocess, time, uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--exe', required=True)
parser.add_argument('--workspace', required=True, help='Parent for a newly allocated isolated fixture')
parser.add_argument('--result', required=True)
a = parser.parse_args()
if not __debug__:
    raise RuntimeError('Run acceptance without Python optimization; assertions are required')
output = pathlib.Path(a.result).resolve()
if output.exists():
    raise FileExistsError('Refusing to overwrite previous evidence or another file: ' + str(output))
started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
exe = pathlib.Path(a.exe).resolve(strict=True)
root = pathlib.Path(a.workspace).resolve() / ('usage-barrier-' + uuid.uuid4().hex)
root.mkdir(parents=True, exist_ok=False)
data = root / 'data'
base = [str(exe), '--data-root', str(data), '--json']
passed = []
child = None

def call(*args, ok=True):
    result = subprocess.run(base + list(args), capture_output=True, encoding='utf-8', timeout=45)
    obj = json.loads(result.stdout)
    assert obj.get('api_version') == 1, obj
    if ok:
        assert result.returncode == 0, obj
        return obj['data']
    assert result.returncode != 0, obj
    return obj

def patch_operation(op):
    # The worker is live and idle; never open a Store concurrently with it.
    # Only this fixture's SQLite row is changed for deliberate checkpoint injection.
    import sqlite3
    op['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with sqlite3.connect(data / 'registry.sqlite3') as db:
        cursor = db.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(op), op['id']))
        assert cursor.rowcount == 1

def operation():
    plan = root / ('plan-' + uuid.uuid4().hex + '.json')
    plan.write_text(json.dumps(dict(action='delete', resource_id=resource['id'])), encoding='utf-8')
    return call('operation', 'plan', '--file', str(plan))

try:
    call('config', 'set', '--env-root', str(root/'envs'), '--tools-root', str(root/'tools'), '--initialize')
    target = root / 'envs' / 'project with spaces'
    target.mkdir()
    sentinel = target / 'project-data.txt'
    sentinel.write_bytes(b'never delete project data')
    (target/'pyvenv.cfg').write_text('version = 3.12.0\n', encoding='utf-8')
    resource = call('registry', 'add', '--path', str(target), '--project', 'barrier-regression')[0]
    # Exercise each independent side of the association, not only both together.
    for association in ('forward-only', 'forward-and-reverse', 'legacy-reverse-only'):
        op = operation()
        lease = dict(id=str(uuid.uuid4()), resource_ids=[resource['id']], mode='write', pid=None,
                     process_stamp=None, process_job=None, project='barrier-regression',
                     state='verifying', created_at=op['created_at'])
        if association != 'legacy-reverse-only':
            lease['operation_id'] = op['id']
        op['status'] = 'executing'
        if association != 'forward-only':
            op['reliability']['lease_id'] = lease['id']
        path = data / 'usage' / (lease['id'] + '.json')
        path.write_text(json.dumps(lease), encoding='utf-8')
        patch_operation(op)
        before = path.read_bytes()
        denied = call('usage', 'release', lease['id'], '--descendants-checked', ok=False)
        assert 'OPERATION_RECOVERY_REQUIRED' in denied['error'], denied
        assert path.read_bytes() == before
        assert sentinel.read_bytes() == b'never delete project data'
        # A live external task conflicts with the maintenance protection too.
        child = subprocess.Popen(['cmd.exe', '/d', '/c', 'ping -n 3 127.0.0.1 >nul'],
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        blocked = call('usage', 'acquire', resource['id'], '--pid', str(child.pid), ok=False)
        assert 'Resource in use' in blocked['error'], blocked
        child.wait(timeout=10)
        child = None
        # Simulate a durably reconciled failed operation. Recover must only
        # finalize; it must not replay this saved delete plan.
        lease['state'] = 'reserved'
        path.write_text(json.dumps(lease), encoding='utf-8')
        # Restore the reverse link for legitimate finalize-only reconciliation.
        # This reset is deliberate checkpoint injection, not real crash recovery.
        op['reliability']['lease_id'] = lease['id']
        op['status'] = 'failed'
        op['reliability']['recovery']['status'] = 'completed'
        patch_operation(op)
        recovered = call('operation', 'recover', op['id'])
        assert recovered['reliability']['finalization']['status'] == 'completed', recovered
        assert json.loads(path.read_text(encoding='utf-8'))['state'] == 'released'
        assert sentinel.exists()
        passed.append(association + ' association blocks manual release and conflicting task; after explicit checkpoint reset, finalize-only releases without applying delete')
    # A true external lease has no operation association and follows old rules.
    child = subprocess.Popen(['cmd.exe', '/d', '/c', 'ping -n 4 127.0.0.1 >nul'],
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    external = call('usage', 'acquire', resource['id'], '--pid', str(child.pid))
    running = call('usage', 'release', external['id'], '--descendants-checked', ok=False)
    assert 'Recorded process is still running' in running['error'], running
    child.wait(timeout=10)
    child = None
    unreviewed = call('usage', 'release', external['id'], ok=False)
    assert 'DESCENDANT_VERIFICATION_REQUIRED' in unreviewed['error'], unreviewed
    assert call('usage', 'release', external['id'], '--descendants-checked')['state'] == 'released'
    passed.append('Actual external child blocks release while alive; after exit requires descendant review then releases')
    assert all(x['state'] == 'released' for x in call('usage', 'list'))
    assert sentinel.read_bytes() == b'never delete project data'
    status = call('status')
    call('worker-stop')
    report = dict(executable=str(exe), exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                  fixture=str(root), run_id=root.name, started_at=started_at,
                  finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  passed=passed, active_leases=0, worker_stopped=True,
                  checkpoint_scope='Constructed operation checkpoints; real packaged CLI/IPC/SQLite/lease files and external processes; no install/delete applied',
                  status=status)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as report_file:
        report_file.write(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(dict(passed=len(passed), result=str(output), exe_sha256=report['exe_sha256'])))
except BaseException:
    print('Failure fixture retained: ' + str(root))
    raise
finally:
    if child is not None:
        # Own short-lived child only; do not kill any shared process or worker.
        child.wait(timeout=10)
