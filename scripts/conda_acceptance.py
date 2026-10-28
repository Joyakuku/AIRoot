"""Real conda lifecycle using a new prefix and an explicit community channel."""
import argparse,json,pathlib,subprocess,time,uuid
p=argparse.ArgumentParser();p.add_argument('--conda',required=True);p.add_argument('--exe',default='target/debug/airoot.exe');a=p.parse_args()
repo=pathlib.Path(__file__).resolve().parent.parent;root=repo/'work'/('conda-'+uuid.uuid4().hex);root.mkdir(parents=True)
base=[str((repo/a.exe).resolve()),'--data-root',str(root/'data'),'--json'];passed=[]
def cli(*args):
    out=subprocess.run(base+list(args),capture_output=True,text=True,encoding='utf-8',timeout=40);obj=json.loads(out.stdout)
    assert out.returncode==0,obj;return obj['data']
def operation(obj):
    f=root/'plan.json';f.write_text(json.dumps(obj),encoding='utf-8');op=cli('operation','plan','--file',str(f))
    args=['operation','apply',op['id']]
    for c in op['requirements']:args+=['--confirm',c]
    cli(*args);until=time.monotonic()+900
    while time.monotonic()<until:
        result=cli('operation','show',op['id'])
        if result['status']!='executing':
            assert result['status']=='completed',result
            return result['resource']
        time.sleep(.5)
    raise RuntimeError('Conda operation timed out; retained at '+str(root))
try:
    cli('config','set','--env-root',str(root/'env'),'--tools-root',str(root/'tools'),'--initialize')
    channel='https://conda.anaconda.org/conda-forge'
    r=operation(dict(action='create',kind='conda',name='conda-check',project='acceptance',manager=a.conda,version='3.11',source=channel))
    assert r['projects']==['acceptance'];assert cli('resource','run',r['id'],'--','-c','import sys;print(sys.version)')['exit_code']==0
    passed.append('Create isolated conda Python 3.11 prefix, verify interpreter, project association and protected execution')
    for action in ['install-packages','upgrade-packages','remove-packages']:
        updated=operation(dict(action=action,resource_id=r['id'],packages=['six'],source=channel))
        assert any(d['name']=='six' for d in updated['dependencies']) == (action!='remove-packages'),updated
        assert updated['metadata']['dependency_status']=='verified'
        passed.append('Conda '+action+' through exact prefix using recorded manager')
    operation(dict(action='delete',resource_id=r['id']))
    assert not pathlib.Path(r['path']).exists();assert not cli('env','list')
    passed.append('Exact confirmed conda env removal and registry synchronization')
    cli('worker-stop')
    result=dict(passed=passed,root=str(root));(repo/'work/conda-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception:
    print('Conda test evidence retained at '+str(root));raise
