"""Create/export/delete only a freshly named WSL acceptance distribution."""
import hashlib,json,pathlib,subprocess,time,uuid,urllib.request
repo=pathlib.Path(__file__).resolve().parent.parent;root=repo/'work'/('wsl-'+uuid.uuid4().hex);root.mkdir(parents=True);base=[str(repo/'target/debug/airoot.exe'),'--data-root',str(root/'data'),'--json'];passed=[]
def cli(*args,ok=True):
    out=subprocess.run(base+list(args),capture_output=True,text=True,encoding='utf-8',timeout=60);obj=json.loads(out.stdout)
    if ok:assert out.returncode==0,obj;return obj['data']
    return obj

def operation(obj):
    f=root/'plan.json';f.write_text(json.dumps(obj),encoding='utf-8');op=cli('operation','plan','--file',str(f));args=['operation','apply',op['id']]
    for c in op['requirements']:args+=['--confirm',c]
    cli(*args);until=time.monotonic()+180
    while time.monotonic()<until:
        result=cli('operation','show',op['id'])
        if result['status']!='executing':
            assert result['status']=='completed',result;return result['resource']
        time.sleep(.2)
    raise RuntimeError('Timed out; test distribution retained at '+str(root))
try:
    url='https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/x86_64/alpine-minirootfs-3.22.2-x86_64.tar.gz'
    with urllib.request.urlopen(url+'.sha256',timeout=30) as response:expected=response.read().decode().split()[0]
    archive=root/'rootfs.tar.gz'
    with urllib.request.urlopen(url,timeout=30) as response:archive.write_bytes(response.read())
    actual=hashlib.sha256(archive.read_bytes()).hexdigest();assert actual==expected
    cli('config','set','--env-root',str(root/'env'),'--tools-root',str(root/'tools'),'--initialize')
    name='airoot-test-'+uuid.uuid4().hex[:12]
    r=operation(dict(action='import-wsl',name=name,project='wsl-acceptance',source=url,archive=str(archive),sha256=expected))
    passed.append('New isolated WSL2 distribution imported from upstream checksum-verified Alpine rootfs; registry identity and project verified')
    inspected=cli('platform-inspect',r['id'],'--path','/usr')
    f=root/'delete.json';f.write_text(json.dumps(dict(action='delete',resource_id=r['id'])),encoding='utf-8');rejected=cli('operation','plan','--file',str(f),ok=False);assert not rejected['ok'] and 'BACKUP_OR_MIGRATION_REQUIRED' in rejected['error']
    backup=root/'backup.tar';export=subprocess.run(['wsl.exe','--export',name,str(backup)],capture_output=True,timeout=180);assert export.returncode==0,(export.stdout,export.stderr)
    operation(dict(action='delete',resource_id=r['id'],archive=str(backup),sha256=hashlib.sha256(backup.read_bytes()).hexdigest()))
    names=subprocess.run(['wsl.exe','--list','--quiet'],capture_output=True,timeout=30).stdout.decode('utf-16-le',errors='replace');assert name not in names;assert backup.exists();assert not cli('env','list')
    passed.append('No-backup deletion rejected; exact test distribution export/hash verification and confirmed unregister succeed; backup retained')
    cli('worker-stop')
    result=dict(passed=passed,root=str(root),rootfs_sha256=actual,source=url);(repo/'work/wsl-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception:
    print('WSL test evidence retained at '+str(root));raise
