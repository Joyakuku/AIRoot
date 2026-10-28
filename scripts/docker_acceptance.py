"""Real Windows/local-Docker acceptance; only disposable identities are mutated.
Native Docker commands provision mounted fixtures/export backups, not AIRoot actions.
Never prunes, changes contexts, or removes pre-existing images/containers/volumes.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import tarfile
import time
import uuid

PINNED = 'docker.io/library/busybox@sha256:66a6306db78bf2dbf3487f293aa8d6990d8e506fdffab9cc43fe422becf886e4'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exe', required=True)
    p.add_argument('--workspace', required=True)
    p.add_argument('--result', required=True)
    a = p.parse_args()
    if not __debug__ or os.name != 'nt':
        raise RuntimeError('Requires Windows and enabled Python assertions')
    exe = pathlib.Path(a.exe).resolve(strict=True)
    out = pathlib.Path(a.result).resolve()
    if out.exists():
        raise FileExistsError('Refusing to overwrite evidence')
    root = pathlib.Path(a.workspace).resolve() / ('docker-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    token = uuid.uuid4().hex[:16]
    base = [str(exe), '--data-root', str(root / 'data'), '--json']
    events, passed, operations, children = [], [], [], []
    result = dict(started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  classification='real local Windows Docker engine',
                  exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
                  script_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
                  workspace=str(root), source=PINNED, passed=passed, operations=operations,
                  complete=False)

    def execute(cmd, timeout=60):
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        events.append(dict(command=cmd, returncode=r.returncode, stdout=r.stdout, stderr=r.stderr))
        return r

    def docker(*args, timeout=60):
        r = execute(['docker', '--host', host, *args], timeout)
        if r.returncode:
            raise RuntimeError('Docker command failed: ' + r.stderr)
        return r.stdout.strip()

    def snapshot():
        return {kind: sorted(set(docker(*args).splitlines())) for kind, args in {
            'images': ('image', 'ls', '--no-trunc', '--quiet'),
            'containers': ('ps', '--all', '--no-trunc', '--quiet'),
            'volumes': ('volume', 'ls', '--quiet')}.items()}

    def cli(*args, ok=True):
        r = execute([*base, *map(str, args)], 90)
        obj = json.loads(r.stdout)
        assert obj['api_version'] == 1, obj
        if ok:
            assert r.returncode == 0 and obj['ok'], obj
            return obj['data']
        assert r.returncode != 0 and not obj['ok'], obj
        return obj

    def plan(payload, ok=True):
        path = root / ('plan-' + uuid.uuid4().hex + '.json')
        path.write_text(json.dumps(payload), encoding='utf-8')
        return cli('operation', 'plan', '--file', path, ok=ok)

    def wait(oid):
        end = time.monotonic() + 240
        while time.monotonic() < end:
            op = cli('operation', 'show', oid)
            if op['status'] not in ('planned', 'queued', 'running', 'executing', 'recovering'):
                assert op['status'] == 'completed', op
                operations.append(op)
                return op
            time.sleep(.3)
        raise TimeoutError('Operation unfinished; inspect preserved evidence')

    def apply(op):
        args = ['operation', 'apply', op['id']]
        for requirement in op['requirements']:
            args.extend(['--confirm', requirement])
        cli(*args)
        return wait(op['id'])

    def operation(payload):
        return apply(plan(payload))['resource']

    def backup(cid):
        archive = root / (cid[:16] + '.tar')
        docker('container', 'export', '--output', str(archive), cid)
        assert archive.stat().st_size > 0
        return dict(archive=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest())

    def discover_container(cid):
        cli('platform-discover', 'docker')
        return next(r for r in cli('env', 'list') if r['kind'] == 'docker-container' and r['metadata']['object'] == cid)

    try:
        ctx = execute(['docker', 'context', 'show']).stdout.strip()
        inspected = execute(['docker', 'context', 'inspect', ctx])
        host = json.loads(inspected.stdout)[0]['Endpoints']['docker']['Host']
        assert host.startswith('npipe:////./pipe/') and '@' not in host
        result['host'] = host
        result['engine'] = json.loads(docker('version', '--format', '{{json .Server}}'))
        before = snapshot()
        result['baseline'] = before
        assert 'sha256:' in PINNED
        # Do not run against an image already owned by the user.
        probe = execute(['docker', '--host', host, 'image', 'inspect', PINNED])
        assert probe.returncode != 0, 'Pinned test image already exists; choose another test image'
        cli('config', 'set', '--env-root', root / 'env', '--tools-root', root / 'tools', '--initialize')
        resources = cli('platform-discover', 'docker')
        assert any(r and r['kind'] == 'docker-runtime' for r in resources['resources']), resources
        passed.append('Local named-pipe engine and baseline resources discovered through AIRoot')
        image = operation(dict(action='pull-image', source=PINNED, name='docker-acceptance', project='docker-' + token))
        iid = image['metadata']['object']
        assert iid not in before['images']
        assert PINNED.split('@')[1] in ' '.join(json.loads(docker('image', 'inspect', iid))[0]['RepoDigests'])
        passed.append('Real digest-pinned network image pull, verification, registration and project association')
        container = operation(dict(action='create-container', resource_id=image['id'], name='airoot-' + token,
                                   project='docker-' + token,
                                   packages=['sh', '-c', 'mkdir -p /project; printf airoot-fixture > /project/evidence.txt; exec sleep 600']))
        cid = container['metadata']['object']
        obj = json.loads(docker('container', 'inspect', cid))[0]
        assert not obj['State']['Running'] and not obj['Mounts'] and not obj['HostConfig']['Privileged']
        assert obj['Config']['Labels']['airoot.creation'] == container['metadata']['creation_token']
        assert obj['Image'] == iid
        passed.append('AIRoot creates a stopped isolated container with stable ID/creation label and no mounts/privilege')
        container = operation(dict(action='start', resource_id=container['id']))
        assert json.loads(docker('container', 'inspect', cid))[0]['State']['Running']
        end = time.monotonic() + 15
        while time.monotonic() < end:
            probe = execute(['docker', '--host', host, 'exec', cid, 'cat', '/project/evidence.txt'])
            if probe.returncode == 0 and probe.stdout == 'airoot-fixture':
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Fixture project file not created')
        stop = plan(dict(action='stop', resource_id=container['id']))
        leases = []
        for _ in range(2):
            child = subprocess.Popen(['cmd.exe', '/d', '/c', 'ping -n 120 127.0.0.1 >nul'], creationflags=subprocess.CREATE_NO_WINDOW)
            children.append(child)
            leases.append(cli('usage', 'acquire', container['id'], '--pid', child.pid, '--project', 'docker-' + token))
        blocked = cli('operation', 'apply', stop['id'], '--confirm', *stop['requirements'], ok=False)
        assert 'use' in blocked['error'].lower() or 'lease' in blocked['error'].lower(), blocked
        for child in children:
            subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
            child.wait(timeout=10)
        for lease in leases:
            cli('usage', 'release', lease['id'], '--descendants-checked')
        passed.append('Two simultaneous read leases accepted; conflicting AIRoot stop rejected until processes exit/release')
        # Even a verified backup does not permit deletion of a running container.
        archived = backup(cid)
        rejected = plan(dict(action='delete', resource_id=container['id'], **archived), ok=False)
        assert 'running' in rejected['error'].lower(), rejected
        container = apply(stop)['resource']
        assert not json.loads(docker('container', 'inspect', cid))[0]['State']['Running']
        rejected = plan(dict(action='delete', resource_id=container['id']), ok=False)
        assert 'BACKUP_OR_MIGRATION_REQUIRED' in rejected['error'], rejected
        archived = backup(cid)
        with tarfile.open(archived['archive']) as tar:
            member = next(m for m in tar.getmembers() if m.name.lstrip('./') == 'project/evidence.txt')
            assert tar.extractfile(member).read() == b'airoot-fixture'
        delete = plan(dict(action='delete', resource_id=container['id'], **archived))
        assert not cli('operation', 'apply', delete['id'], ok=False)['ok']
        archive = pathlib.Path(archived['archive'])
        with archive.open('ab') as f:
            f.write(b'changed-after-plan')
        rejected = cli('operation', 'apply', delete['id'], '--confirm', delete['requirements'][0], '--confirm', delete['requirements'][1], ok=False)
        assert 'Backup' in rejected['error'] or 'backup' in rejected['error'], rejected
        archived = backup(cid)
        operation(dict(action='delete', resource_id=container['id'], **archived))
        assert cid not in snapshot()['containers']
        passed.append('Running/no-backup/no-confirmation/changed-backup deletion refused; stopped backed-up exact instance removed and registry synced')
        # Native provisioning is necessary only for mount fixtures (AIRoot intentionally exposes no mount flags).
        bind = root / 'host project 中文'
        bind.mkdir()
        marker = bind / 'preserve.txt'
        marker.write_text('host-project-' + token, encoding='utf-8')
        volume = 'airoot-' + token
        docker('volume', 'create', '--label', 'airoot.acceptance=' + token, volume)
        mount_name = 'airoot-mount-' + token
        mount_id = docker('container', 'create', '--name', mount_name, '--label', 'airoot.acceptance=' + token,
                          '--mount', 'type=bind,source=' + str(bind) + ',target=/host-project,readonly',
                          '--mount', 'type=volume,source=' + volume + ',target=/volume-project', iid,
                          'sh', '-c', 'printf volume-project > /volume-project/preserve.txt; exec sleep 600')
        assert mount_id not in before['containers']
        mounted = discover_container(mount_id)
        mounted = operation(dict(action='start', resource_id=mounted['id']))
        end = time.monotonic() + 15
        while time.monotonic() < end:
            probe = execute(['docker', '--host', host, 'exec', mount_id, 'cat', '/volume-project/preserve.txt'])
            if probe.returncode == 0 and probe.stdout == 'volume-project':
                break
            time.sleep(.2)
        else:
            raise RuntimeError('Volume marker unavailable')
        mounted = operation(dict(action='stop', resource_id=mounted['id']))
        rejected = plan(dict(action='delete', resource_id=mounted['id']), ok=False)
        assert 'BACKUP_OR_MIGRATION_REQUIRED' in rejected['error']
        # docker export excludes mounts; preserve mounted project data separately.
        volume_backup = root / 'volume-project-backup'
        volume_backup.mkdir()
        docker('container', 'cp', mount_id + ':/volume-project/preserve.txt', str(volume_backup / 'preserve.txt'))
        assert (volume_backup / 'preserve.txt').read_bytes() == b'volume-project'
        migration = dict(target=str(volume_backup))
        delete = plan(dict(action='delete', resource_id=mounted['id'], **migration))
        assert 'retained' in ' '.join(delete['effects']).lower()
        apply(delete)
        assert marker.read_text(encoding='utf-8') == 'host-project-' + token
        v = json.loads(docker('volume', 'inspect', volume))[0]
        assert v['Labels']['airoot.acceptance'] == token
        passed.append('Mounted fixture: no-backup refusal; explicit migration permits container deletion while host file and named volume remain')
        # Verify persisted volume bytes with a read-only mount, then remove only our helper via AIRoot.
        helper = docker('container', 'create', '--name', 'airoot-check-' + token, '--label', 'airoot.acceptance=' + token,
                        '--mount', 'type=volume,source=' + volume + ',target=/volume-project,readonly', iid,
                        'cat', '/volume-project/preserve.txt')
        helper_r = discover_container(helper)
        # Native start -a is a fixture data check, not an AIRoot lifecycle assertion.
        assert docker('start', '--attach', helper) == 'volume-project'
        operation(dict(action='delete', resource_id=helper_r['id'], **migration))
        v = json.loads(docker('volume', 'inspect', volume))[0]
        assert v['Labels']['airoot.acceptance'] == token and volume not in before['volumes']
        docker('volume', 'rm', volume)
        # platform-discover may update registry metadata, so use its current stable resource ID.
        current_image = next(r for r in cli('env', 'list') if r['kind'] == 'docker-image' and r['metadata']['object'] == iid)
        assert iid not in before['images']
        operation(dict(action='delete', resource_id=current_image['id']))
        after = snapshot()
        assert after == before, dict(before=before, after=after)
        assert all(lease['state'] == 'released' for lease in cli('usage', 'list'))
        result['final_status'] = cli('status')
        assert not any(r['kind'] == 'docker-container' for r in cli('env', 'list'))
        passed.append('Read-only reattachment verifies preserved volume bytes; only this run resources removed; original Docker baseline exactly restored')
        result['final_baseline'] = after
        result['complete'] = True
    except Exception as e:
        result['error'] = repr(e)
        raise
    finally:
        for child in children:
            if child.poll() is None:
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
        try:
            cli('worker-stop')
        except Exception as e:
            result['worker_stop_error'] = repr(e)
        result['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (root / 'commands.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: result[k] for k in ('complete', 'passed', 'workspace', 'exe_sha256')} | {'error': result.get('error')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


