"""Real CLI acceptance using isolated resources; no pre-existing resource is mutated."""
import argparse, json, pathlib, subprocess, time, uuid, sys
p=argparse.ArgumentParser();p.add_argument('--exe',default='target/debug/airoot.exe');p.add_argument('--python',default=sys.executable);args=p.parse_args()
workspace=pathlib.Path(__file__).resolve().parent.parent
exe=str((workspace/args.exe).resolve());root=workspace/'work'/('extended-'+uuid.uuid4().hex);root.mkdir(parents=True)
data=root/'data';base=[exe,'--data-root',str(data),'--json'];passed=[];limited=[]
def cli(*a,ok=True):
    out=subprocess.run(base+list(a),capture_output=True,text=True,encoding='utf-8',timeout=65)
    result=json.loads(out.stdout)
    if ok and (out.returncode or not result['ok']):raise RuntimeError(result)
    return result['data'] if ok else result
def plan(obj):
    path=root/'plan.json';path.write_text(json.dumps(obj),encoding='utf-8');return cli('operation','plan','--file',str(path))
def wait(id):
    until=time.monotonic()+90
    while time.monotonic()<until:
        r=cli('operation','show',id)
        if r['status']!='executing':return r
        time.sleep(.05)
    raise RuntimeError('Timed out')
def apply(op,*confirms):
    a=['operation','apply',op['id']]
    for c in confirms:a+=['--confirm',c]
    cli(*a);r=wait(op['id']);assert r['status']=='completed',r;return r
try:
    cli('config','set','--env-root',str(root/'env'),'--tools-root',str(root/'tools'),'--initialize')
    r=apply(plan(dict(action='create',kind='venv',name='concurrency',project='test',manager=args.python)))['resource']
    calls=[subprocess.Popen(base+['resource','run',r['id'],'--','-c','import time;time.sleep(3);print("ok")'],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    until=time.monotonic()+5
    while time.monotonic()<until:
        leases=cli('usage','list')
        if sum(l['state']=='running' and l['mode']=='read' for l in leases)>=2:break
        time.sleep(.05)
    else:raise RuntimeError('Both read leases did not become active')
    op=plan(dict(action='delete',resource_id=r['id']))
    rejected=cli('operation','apply',op['id'],'--confirm','delete:'+r['id'],'--confirm','external-use-reviewed',ok=False)
    assert not rejected['ok'],rejected
    for call in calls:
        stdout,stderr=call.communicate(timeout=20);assert call.returncode==0,(stdout,stderr)
    apply(op,'delete:'+r['id'],'external-use-reviewed')
    passed.append('Two actual resource processes run concurrently; deletion is blocked while in use and succeeds after both finish')
    lock=json.loads((workspace/'packaging/components.lock.json').read_text(encoding='utf-8-sig'));fd=next(c for c in lock['components'] if c['name']=='fd')
    op=plan(dict(action='install-portable',class_='tool',kind='cli',name='fd',source=fd['source'],sha256=fd['archive_sha256'],project='download-test'))
    r=apply(op,'source-reviewed')['resource']
    assert 'fd 10.5.0' in cli('resource','run',r['id'],'--','--version')['stdout']
    apply(plan(dict(action='delete',resource_id=r['id'])),'delete:'+r['id'],'external-use-reviewed')
    passed.append('HTTPS ZIP download into normal tools cache, pinned checksum, extraction, protected executable run and deletion')
    wsl=cli('platform-discover','wsl',ok=False)
    if wsl['ok']:
        resources=[r for r in wsl['data']['resources'] if r and r['kind']=='wsl']
        user=next((r for r in resources if not r['name'].startswith('docker')),None)
        if user:
            result=cli('platform-inspect',user['id'],'--path','/usr',ok=False)
            if result['ok']:passed.append('Read-only WSL /usr structural lookup with scoped Linux identities')
            else:limited.append({'WSL internal lookup':result['error']})
            path=root/'delete.json';path.write_text(json.dumps(dict(action='delete',resource_id=user['id'])),encoding='utf-8')
            rejected=cli('operation','plan','--file',str(path),ok=False)
            assert not rejected['ok'] and 'BACKUP_OR_MIGRATION_REQUIRED' in rejected['error'],rejected
            passed.append('Real installed WSL distribution deletion planning blocked without backup/migration; no deletion executed')
    else:limited.append({'WSL discovery':wsl['error']})
    docker=cli('platform-discover','docker',ok=False)
    if not docker['ok']:limited.append({'Docker live validation':docker['error']})
    cli('worker-stop')
    result=dict(passed=passed,limitations=limited,root=str(root));(workspace/'work/extended-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception:
    print('Test evidence retained at '+str(root),file=sys.stderr);raise
