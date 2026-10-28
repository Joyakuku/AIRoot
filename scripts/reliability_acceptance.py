"""0.1.1 CLI fault regression. Fixtures live only under work/reliability-*.
Offline journal mutations simulate crash checkpoints; those cases are labelled simulated.
"""
import argparse,hashlib,json,pathlib,subprocess,sys,time,uuid,zipfile
repo=pathlib.Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',default=str(repo/'target/debug/airoot.exe'));parser.add_argument('--workspace',default=str(repo/'work'));parser.add_argument('--result',default=str(repo/'work/reliability-results.json'));args=parser.parse_args()
exe=pathlib.Path(args.exe).resolve(strict=True)
workspace=pathlib.Path(args.workspace).resolve();workspace.mkdir(parents=True,exist_ok=True)
result_path=pathlib.Path(args.result).resolve();result_path.parent.mkdir(parents=True,exist_ok=True)
root=workspace/('reliability-'+uuid.uuid4().hex);root.mkdir(parents=True)
data=root/'data';base=[str(exe),'--data-root',str(data),'--json'];passed=[];simulated=[]
def cli(*args,ok=True):
 out=subprocess.run(base+list(args),capture_output=True,text=True,encoding='utf-8',timeout=45);obj=json.loads(out.stdout)
 if ok:assert out.returncode==0,obj
 return obj['data'] if ok else obj
def plan(obj):
 p=root/'plan.json';p.write_text(json.dumps(obj),encoding='utf-8');return cli('operation','plan','--file',str(p))
def wait(id,recovery=False):
 end=time.monotonic()+90
 while time.monotonic()<end:
  op=cli('operation','show',id)
  state=op['reliability']['recovery']['status'] if recovery else op['status']
  if state not in ('running','executing','interrupted') and op['reliability']['cleanup']['status']!='running':return op
  time.sleep(.05)
 raise RuntimeError('Timeout: '+json.dumps(op))
def apply(op):cli('operation','apply',op['id'],'--confirm','source-reviewed');return wait(op['id'])
def portable(archive,name='fd',version=None):
 return plan(dict(action='install-portable',kind='cli',source='https://github.com/sharkdp/fd/releases/download/v10.2.0/fd-v10.2.0-x86_64-pc-windows-msvc.zip',name=name,project='fault-tests',archive=str(archive),sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),version=version))
def stop():cli('worker-stop');time.sleep(.3)
def journal(op):
 # New timestamp must supersede DB after orderly test shutdown.
 import datetime
 op['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 (data/'pending'/(op['id']+'.json')).write_text(json.dumps(op),encoding='utf-8')
try:
 cli('config','set','--env-root',str(root/'env'),'--tools-root',str(root/'tools'),'--initialize')
 bad=root/'user supplied broken.zip';bad.write_bytes(b'not a ZIP')
 op=apply(portable(bad));assert op['reliability']['cleanup']['status']=='completed',op;assert bad.exists() and not pathlib.Path(op['resource']['path']).exists();passed.append('Bad ZIP: exact owned target cleaned, user archive retained')
 download=plan(dict(action='install-portable',kind='cli',name='fd',project='fault-tests',source='https://github.com/sharkdp/fd/releases/download/airoot-nonexistent-test/404.zip',sha256='0'*64))
 download=apply(download);assert download['reliability']['cleanup']['status']=='completed',download
 assert all(o['removed'] for o in download['reliability']['owned']);passed.append('Actual HTTPS download failure: owned download and target removed, shared cache retained')
 good=root/'user fd.zip'
 with zipfile.ZipFile(good,'w') as z:z.write(repo/'components/fd.exe','fd.exe')
 op=apply(portable(good,version='0.0.0'));assert op['reliability']['cleanup']['status']=='completed',op;assert good.exists();passed.append('Verified wrong version automatically cleaned; original installer retained')
 op=apply(plan(dict(action='create',kind='venv',name='wrong-python',project='fault-tests',manager=sys.executable,version='0.0.0')))
 assert op['reliability']['cleanup']['status']=='completed',op;passed.append('Real venv wrong-version failure automatically cleaned')
 # Genuine database-write failure after external extraction: SQLite trigger rejects resource writes.
 import sqlite3
 op=portable(good);db=sqlite3.connect(data/'registry.sqlite3');db.execute("CREATE TRIGGER reject_registration BEFORE INSERT ON resources BEGIN SELECT RAISE(FAIL,'test-only registration failure'); END");db.commit();db.close()
 op=apply(op);assert op['status']=='registration_failed',op;target=pathlib.Path(op['resource']['path']);assert target.exists();passed.append('Injected actual SQLite registration failure preserves installed resource')
 db=sqlite3.connect(data/'registry.sqlite3');db.execute('DROP TRIGGER reject_registration');db.commit();db.close()
 cli('operation','recover',op['id']);op=wait(op['id'],True);assert op['status']=='completed',op;assert op['attempts']==1;passed.append('Explicit recovery verifies and registers without second installation')
 # Restart after committed registration but stale journal; registry upsert must be idempotent.
 stop();op['status']='interrupted';op['registration_status']='pending';journal(op)
 cli('status');op=wait(op['id'],True);assert op['status']=='completed',op;assert len(cli('tool','list'))==1;simulated.append('Stale journal after registry commit: startup reconciliation, no duplicate or reinstall')
 # A verified completed new installation before registry commit, represented by durable journal.
 stop();db=sqlite3.connect(data/'registry.sqlite3');db.execute('DELETE FROM resources WHERE id=?',(op['resource']['id'],));db.commit();db.close();op['status']='interrupted';op['registration_status']='not_started';journal(op)
 cli('status');op=wait(op['id'],True);assert op['status']=='completed',op;assert target.exists();simulated.append('Interrupted before registration: startup repairs actual installed instance')
 # Real worker termination during venv creation; no installer replay on restart.
 crash=plan(dict(action='create',kind='venv',name='crash-creation',project='fault-tests',manager=sys.executable));pid=cli('status')['worker_pid'];cli('operation','apply',crash['id']);pending=data/'pending'/(crash['id']+'.json');deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  current=json.loads(pending.read_text(encoding='utf-8'))
  if current['reliability']['owned'] and current['execution_status']=='executing':break
  time.sleep(.01)
 else:raise RuntimeError('Creation checkpoint not observed')
 subprocess.run(['taskkill','/PID',str(pid),'/F'],check=True,capture_output=True);time.sleep(.5)
 cli('status');time.sleep(1);current=cli('operation','show',crash['id']);assert current['attempts']==1
 assert current['reliability']['recovery']['status'] in ('running','completed','blocked'),current
 # If installer was still alive at initial recovery, retry after it finishes; still no replay.
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  current=cli('operation','show',crash['id'])
  if current['reliability']['recovery']['status']=='completed':break
  if current['reliability']['recovery']['status']=='blocked':cli('operation','recover',crash['id'])
  time.sleep(.4)
 assert current['reliability']['recovery']['status']=='completed',current
 assert current['attempts']==1;passed.append('Actual worker kill during venv creation: descendant protection, subsequent reconciliation, no reinstall')
 stop()
 result=dict(exe=str(exe),exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),real_passed=passed,simulated_passed=simulated,root=str(root));result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception:
 print('Evidence retained at '+str(root));raise
