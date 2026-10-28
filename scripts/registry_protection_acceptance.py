"""Isolated real Windows CLI/IPC tests for registry verify/restore protection.
Uses real fd and a disposable local MCP probe, not models or install/delete.
Registration declarations are test fixtures, not a discovered Python environment.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', required=True)
    parser.add_argument('--fd', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--result', required=True)
    a = parser.parse_args()
    if not __debug__:
        raise RuntimeError('Run without Python optimization; assertions are required')
    if os.name != 'nt':
        raise RuntimeError('This acceptance requires Windows process/job support')
    output = pathlib.Path(a.result).resolve()
    if output.exists():
        raise FileExistsError('Refusing to overwrite evidence: ' + str(output))
    exe = pathlib.Path(a.exe).resolve(strict=True)
    fd = pathlib.Path(a.fd).resolve(strict=True)
    root = pathlib.Path(a.workspace).resolve() / ('registry-protection-' + uuid.uuid4().hex)
    root.mkdir(parents=True, exist_ok=False)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data = root / 'data'
    base = [str(exe), '--data-root', str(data), '--json']
    passed = []
    children = []

    def decode(result, ok=True):
        obj = json.loads(result.stdout)
        assert obj.get('api_version') == 1, obj
        if ok:
            assert result.returncode == 0 and obj['ok'] is True, obj
            return obj['data']
        assert result.returncode != 0 and obj['ok'] is False, obj
        return obj

    def call(*args, ok=True, timeout=20):
        return decode(subprocess.run(base + list(args), capture_output=True,
                                    encoding='utf-8', timeout=timeout), ok)

    def register(path, name, kind='cli', entry=None, parent=None, metadata=None):
        resource = dict(id='', **{'class': 'tool'}, kind=kind, name=name, path=str(path),
                        entry=str(entry) if entry else None, version=None, parent_id=parent,
                        scope='host', status='unverified', projects=['isolation-regression'],
                        dependencies=[], metadata=metadata or {}, updated_at=started)
        resource_file = root / ('resource-' + uuid.uuid4().hex + '.json')
        resource_file.write_text(json.dumps(resource), encoding='utf-8')
        return call('registry', 'add', '--file', str(resource_file))[0]

    try:
        target = root / 'envs' / '项目 with spaces'
        target.mkdir(parents=True)
        sentinel = target / 'project-data.txt'
        sentinel.write_bytes(b'keep project data')
        (target / 'pyvenv.cfg').write_text('version = 3.12.0\n', encoding='utf-8')
        parent = call('registry', 'add', '--path', str(target), '--project', 'isolation-regression')[0]
        entry = target / 'fd.exe'
        shutil.copyfile(fd, entry)
        tool = register(entry, 'fd', entry=entry, parent=parent['id'])
        call('registry', 'ignore', tool['id'])
        external = subprocess.Popen(['cmd.exe', '/d', '/c', 'ping -n 3 127.0.0.1 >nul'],
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(external)
        lease = call('usage', 'acquire', parent['id'], '--pid', str(external.pid))
        blocked = call('registry', 'restore', tool['id'], ok=False)
        assert 'Resource in use' in blocked['error'], blocked
        assert not any(r['id'] == tool['id'] for r in call('tool', 'list'))
        external.wait(timeout=10)
        call('usage', 'release', lease['id'], '--descendants-checked')
        passed.append('Restore refuses an in-use parent; ignored tool stays unmanaged')
        restored = call('registry', 'restore', tool['id'])
        assert restored['id'] == tool['id'] and restored['parent_id'] == tool['parent_id']
        assert restored['projects'] == tool['projects'] and restored['status'] == 'available'
        probes = [l for l in call('usage', 'list') if l['pid'] and l['id'] != lease['id']]
        assert probes and all(l['state'] == 'released' and l['process_job'] for l in probes), probes
        passed.append('Successful fd restore preserves associations and releases tracked probe')

        # This declared fixture runs a known local Python only as the disposable
        # MCP server. No package install, model, environment mutation or API call.
        server_root = target / 'disposable-server'
        server_root.mkdir()
        server = register(server_root, 'isolated MCP fixture', entry=sys.executable, parent=parent['id'])
        for mode in ('restore', 'verify'):
            marker, gate = root / (mode + '-started'), root / (mode + '-continue')
            code = (
                'import sys,json,pathlib,time; '
                'sys.stdin.readline(); '
                f'pathlib.Path({str(marker)!r}).write_text("started"); '
                'deadline=time.monotonic()+12\n'
                f'while not pathlib.Path({str(gate)!r}).exists():\n'
                ' if time.monotonic()>deadline: sys.exit(3)\n'
                ' time.sleep(0.02)\n'
                'print(json.dumps({"jsonrpc":"2.0","id":1,"result":'
                '{"protocolVersion":"2024-11-05","serverInfo":{"name":"isolation-fixture","version":"1"},'
                '"capabilities":{}}}),flush=True)\nsys.stdin.readline()\n'
            )
            connector_path = server_root / (mode + '-connector.json')
            connector_path.write_text('{}', encoding='utf-8')
            connector = register(connector_path, mode + '-connector', 'mcp-connector',
                                 parent=server['id'], metadata={'transport': 'stdio', 'args': ['-u', '-c', code]})
            if mode == 'restore':
                call('registry', 'ignore', connector['id'])
            child = subprocess.Popen(base + ['registry', mode, connector['id']],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            children.append(child)
            try:
                deadline = time.monotonic() + 8
                while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert marker.exists(), 'Disposable MCP probe did not start'
                query_start = time.monotonic()
                leases = call('usage', 'list', timeout=3)
                query_seconds = time.monotonic() - query_start
                active = [l for l in leases if l['state'] != 'released']
                assert len(active) == 1 and active[0]['process_job'], active
                blocked = call('usage', 'release', active[0]['id'], '--descendants-checked', ok=False, timeout=3)
                assert 'VERIFICATION_IN_PROGRESS' in blocked['error'], blocked
            finally:
                gate.write_text('continue', encoding='utf-8')
                stdout, stderr = child.communicate(timeout=20)
            restored = decode(subprocess.CompletedProcess(child.args, child.returncode, stdout, stderr))
            assert restored['id'] == connector['id'] and restored['status'] == 'available', restored
            assert all(l['state'] == 'released' for l in call('usage', 'list'))
            passed.append(f'{mode}: queries remain available ({query_seconds:.3f}s); manual release denied until request ends')
        assert sentinel.read_bytes() == b'keep project data'
        status = call('status')
        call('worker-stop')
        report = dict(run_id=root.name, started_at=started,
                      finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      executable=str(exe), exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                      fixture=str(root), passed=passed, active_leases=0, worker_stopped=True,
                      scope='Real CLI/IPC, fd, Windows jobs, disposable local MCP; synthetic resource declarations; no VM/model/install/delete',
                      status=status)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(json.dumps(dict(passed=len(passed), result=str(output), exe_sha256=report['exe_sha256'])))
    except BaseException:
        print('Failure fixture retained: ' + str(root))
        raise
    finally:
        for child in children:
            if child.poll() is None:
                child.wait(timeout=20)


if __name__ == '__main__':
    main()
