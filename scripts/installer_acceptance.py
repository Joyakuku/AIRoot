"""Isolated installer lifecycle; never points setup at an existing application."""
import argparse,json,pathlib,subprocess,uuid,hashlib,time
p=argparse.ArgumentParser();p.add_argument('--setup',default='dist/AIRoot-0.1.1-preview-setup.exe');p.add_argument('--native-index',action='store_true');p.add_argument('--result',default='work/installer-results.json');args=p.parse_args()
repo=pathlib.Path(__file__).resolve().parent.parent;root=repo/'work'/('installer-'+uuid.uuid4().hex);root.mkdir(parents=True)
setup=str((repo/args.setup).resolve());target=root/'安装 app';data=root/'data';resources=root/'resources';resources.mkdir();sentinel=resources/'preserve.txt';sentinel.write_text('user resource',encoding='utf-8');passed=[]
def install(*extra,ok=True):
    out=subprocess.run([setup,'--install-dir',str(target),'--data-root',str(data),*extra],capture_output=True,text=True,encoding='utf-8',timeout=90)
    if ok:assert out.returncode==0,(out.stdout,out.stderr)
    else:assert out.returncode!=0,(out.stdout,out.stderr)
    return out
exe=target/'airoot.exe'
def cli(*a):
    out=subprocess.run([str(exe),'--data-root',str(data),'--json',*a],capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert out.returncode==0,(out.stdout,out.stderr);return json.loads(out.stdout)['data']
try:
    invalid=root/'rejected app';invalid_data=invalid/'information'
    out=subprocess.run([setup,'--install-dir',str(invalid),'--data-root',str(invalid_data)],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert out.returncode!=0 and 'must not overlap' in out.stderr,(out.stdout,out.stderr)
    assert not invalid.exists(),'Rejected overlapping layout created files in program location'
    passed.append('Overlapping program/information layout rejected before creating directories')
    install(*(['--native-index'] if args.native_index else []))
    assert cli('status')['resources']==0
    cli('config','set','--env-root',str(resources),'--tools-root',str(root/'tools'),'--initialize')
    cli('registry','backup')
    alias=resources/'resource app'
    out=subprocess.run([setup,'--install-dir',str(alias).upper(),'--data-root',str(data)],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert out.returncode!=0 and 'Installation and resource roots must not overlap' in out.stderr,(out.stdout,out.stderr)
    assert not alias.exists(),'Program directory was created inside configured resource root'
    passed.append('Case-aliased configured resource root cannot contain program installation')
    assert all(x['status']=='verified' for x in cli('components','status'))
    passed.append('Fresh offline install into Chinese/spaced directory; all embedded component hashes verified')
    extra=target/'user-owned.txt';extra.write_text('keep',encoding='utf-8')
    install(ok=False);assert extra.read_text(encoding='utf-8')=='keep';extra.unlink()
    passed.append('Upgrade rejects unowned file without moving/deleting it')
    file=target/'README.md';original=file.read_bytes();file.write_bytes(original+b'\nuser modification')
    install(ok=False);assert file.read_bytes().endswith(b'user modification');file.write_bytes(original)
    passed.append('Upgrade rejects modified tracked file without overwriting it')
    metadata_before=(data/'installation.json').read_bytes();config_before=(data/'config.json').read_bytes()
    executable_before=hashlib.sha256(exe.read_bytes()).digest();stages_before=set(target.parent.glob('.airoot-install-*'))
    with (target/'README.md').open('rb') as held_file:
        blocked=install(ok=False)
        assert 'Upgrade blocked before publication' in blocked.stderr,(blocked.stdout,blocked.stderr)
        assert target.is_dir() and exe.is_file(),'Locked upgrade moved the previous installation'
        assert (data/'installation.json').read_bytes()==metadata_before,'Blocked upgrade changed installation metadata'
        assert (data/'config.json').read_bytes()==config_before,'Blocked upgrade changed configuration'
        assert hashlib.sha256(exe.read_bytes()).digest()==executable_before,'Blocked upgrade replaced installed executable'
        assert set(target.parent.glob('.airoot-install-*'))==stages_before,'Blocked upgrade retained unexpected staging material'
    passed.append('Locked owned Windows file blocks upgrade before directory move or metadata commit; retry remains possible')
    install();assert sentinel.read_text(encoding='utf-8')=='user resource';assert cli('config','show')['env_root']==str(resources)
    passed.append('Upgrade replaces owned binaries and retains config, registry and resources')
    fixture=resources/'indexed env'/'pyvenv.cfg';fixture.parent.mkdir();fixture.write_text('home = test',encoding='utf-8')
    index=cli('components','start','--root',str(resources));assert index['pid']
    if args.native_index:assert index['mode']=='native-index',index
    until=time.monotonic()+45
    while time.monotonic()<until:
        query=subprocess.run([str(target/'components/es.exe'),'-instance',index['instance'],'-cp','65001','-timeout','25000','-full-path-and-name',str(fixture)],capture_output=True,text=True,encoding='utf-8',timeout=30)
        if str(fixture).lower() in query.stdout.lower():break
        time.sleep(.2)
    else:raise RuntimeError('Embedded index did not return known fixture: '+query.stdout+query.stderr)
    passed.append('Embedded index actually finds known Chinese/spaced fixture through its private instance')
    with (target/'README.md').open('rb') as held_file:
        install('--uninstall',ok=False)
        assert json.loads((data/'installation.json').read_text(encoding='utf-8'))['uninstall_started']
    passed.append('Interrupted uninstall retains progress and safely resumes after locked owned file is released')
    install('--uninstall');assert not exe.exists();assert sentinel.read_text(encoding='utf-8')=='user resource';assert (data/'registry.sqlite3').exists();assert not (data/'components/everything/Everything.db').exists()
    passed.append('Uninstall stops owned worker/index and preserves managed resource plus registry')
    if args.native_index:passed.append('Dedicated native service install, upgrade replacement and uninstall')
    result=dict(passed=passed,root=str(root),native_index=args.native_index,setup_sha256=hashlib.sha256(pathlib.Path(setup).read_bytes()).hexdigest());(repo/args.result).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception:
    print('Installer evidence retained at '+str(root));raise
