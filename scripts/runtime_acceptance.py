"""Exercise real Node/Java binaries from local installations in isolated ZIP fixtures.
Fixtures are test-only derived packages, not claimed to be official distributions.
"""
import argparse,hashlib,json,pathlib,subprocess,time,uuid,zipfile
p=argparse.ArgumentParser();p.add_argument('--node',required=True);p.add_argument('--java-root',required=True);p.add_argument('--exe',default='target/debug/airoot.exe');a=p.parse_args()
repo=pathlib.Path(__file__).resolve().parent.parent;root=repo/'work'/('runtimes-'+uuid.uuid4().hex);root.mkdir(parents=True);base=[str((repo/a.exe).resolve()),'--data-root',str(root/'data'),'--json'];passed=[]
def cli(*args):
    out=subprocess.run(base+list(args),capture_output=True,text=True,encoding='utf-8',timeout=40);obj=json.loads(out.stdout);assert out.returncode==0,obj;return obj['data']
def operation(obj):
    f=root/'plan.json';f.write_text(json.dumps(obj),encoding='utf-8');op=cli('operation','plan','--file',str(f));args=['operation','apply',op['id']]
    for c in op['requirements']:args+=['--confirm',c]
    cli(*args);until=time.monotonic()+120
    while time.monotonic()<until:
        result=cli('operation','show',op['id'])
        if result['status']!='executing':
            assert result['status']=='completed',result;return result['resource']
        time.sleep(.1)
    raise RuntimeError('Timed out: '+str(root))
try:
    cli('config','set','--env-root',str(root/'env'),'--tools-root',str(root/'tools'),'--initialize')
    for kind in ['node','java']:
        archive=root/(kind+'.zip')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            if kind=='node':z.write(pathlib.Path(a.node).resolve(),'fixture/node.exe')
            else:
                java=pathlib.Path(a.java_root).resolve()
                for f in [java/'release',*sorted((java/'bin').rglob('*')),*sorted((java/'lib').rglob('*'))]:
                    if f.is_file() and not f.is_symlink() and f.name!='src.zip':z.write(f,'fixture/'+f.relative_to(java).as_posix())
        resources=[]
        for n in ['a','b']:
            r=operation(dict(action='install-portable',kind=kind,name=kind+'-'+n,project='project-'+n,source='https://example.invalid/local-runtime-fixture',archive=str(archive),sha256=hashlib.sha256(archive.read_bytes()).hexdigest()));resources.append(r)
            assert cli('resource','run',r['id'],'--','-version' if kind=='java' else '--version')['exit_code']==0
        assert resources[0]['id']!=resources[1]['id'];assert resources[0]['entry']!=resources[1]['entry']
        operation(dict(action='delete',resource_id=resources[0]['id']))
        assert cli('resource','run',resources[1]['id'],'--','-version' if kind=='java' else '--version')['exit_code']==0
        operation(dict(action='delete',resource_id=resources[1]['id']))
        passed.append(kind+': real locally derived ZIP install, two independent instances/projects, protected run, deletion preserves other copy')
    assert not cli('env','list');cli('worker-stop')
    result=dict(passed=passed,root=str(root),fixture_provenance='Local read-only binaries repackaged for acceptance; not official archive/source verification');(repo/'work/runtime-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception:
    print('Runtime test evidence retained at '+str(root));raise
