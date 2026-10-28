"""Real 0.1.0 -> 0.1.1 install and data compatibility, isolated under work/."""
import json,pathlib,subprocess,time,uuid,shutil
repo=pathlib.Path(__file__).resolve().parent.parent;root=repo/'work'/('upgrade-'+uuid.uuid4().hex);root.mkdir(parents=True)
target=root/'app';data=root/'data';env=root/'env';tools=root/'tools';exe=target/'airoot.exe';passed=[]
def install(version,*extra):
 out=subprocess.run([str(repo/'dist'/f'AIRoot-{version}-preview-setup.exe'),'--install-dir',str(target),'--data-root',str(data),*extra],text=True,encoding='utf-8',capture_output=True,timeout=120);assert out.returncode==0,(out.stdout,out.stderr)
def cli(*args):
 out=subprocess.run([str(exe),'--data-root',str(data),'--json',*args],text=True,encoding='utf-8',capture_output=True,timeout=45);obj=json.loads(out.stdout);assert out.returncode==0,obj;return obj['data']
install('0.1.0');assert cli('status')['version']=='0.1.0';cli('config','set','--env-root',str(env),'--tools-root',str(tools),'--initialize')
entry=tools/'fd.exe';shutil.copy2(repo/'components/fd.exe',entry)
row=dict(id='fixture',**{'class':'tool'},kind='cli',name='fd',path=str(entry),entry=str(entry),version=None,parent_id=None,scope='host',status='unverified',projects=['old-project'],dependencies=[],metadata={},updated_at='')
f=root/'resource.json';f.write_text(json.dumps(row),encoding='utf-8');resource=cli('registry','add','--file',str(f))[0];cli('registry','ignore',resource['id']);cli('registry','backup');cli('worker-stop');time.sleep(.4)
install('0.1.1');assert cli('status')['version']=='0.1.1';assert cli('tool','list')==[]
# A 0.1.0 ignore row has no physical-instance evidence. Preserve it instead of
# silently accepting a potentially replaced file at the same path.
out=subprocess.run([str(exe),'--data-root',str(data),'--json','registry','restore',resource['id']],text=True,encoding='utf-8',capture_output=True,timeout=45)
error=json.loads(out.stdout);assert out.returncode!=0 and 'LEGACY_IGNORED_RESOURCE_HAS_NO_INSTANCE_IDENTITY' in error['error'],error
assert cli('tool','list')==[]
import sqlite3
with sqlite3.connect(data/'registry.sqlite3') as db:
    rows=db.execute('SELECT body,physical_identity FROM ignored').fetchall()
    saved=next((json.loads(body),identity) for body,identity in rows if json.loads(body)['id']==resource['id'])
    assert saved[0]['projects']==['old-project'] and saved[1] is None,saved
assert entry.exists();assert list((data/'backups').glob('*.sqlite3'));assert json.loads((data/'storage-version.json').read_text())['version']=='0.1.1'
passed.append('Real 0.1.0 upgrade preserves ignored identity, historical project associations, installed files and backups; legacy restore safely refuses absent physical-instance evidence')
install('0.1.1','--uninstall');assert not exe.exists();assert entry.exists() and (data/'registry.sqlite3').exists();passed.append('Uninstall after upgrade preserves old managed resource and registry')
result=dict(real_passed=passed,root=str(root));(repo/'work/upgrade-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
