"""Run ONE vendor case in a disposable Windows VM, then revert its snapshot.
Never bypass the machine check. Sources and installer hashes must be pinned by the operator.
No host supplier installers are run by other AIRoot acceptance scripts.
"""
import argparse,hashlib,json,pathlib,subprocess,time,uuid
p=argparse.ArgumentParser()
p.add_argument('--exe',required=True);p.add_argument('--installer',required=True);p.add_argument('--sha256',required=True);p.add_argument('--source',required=True)
p.add_argument('--kind',choices=['python','conda'],required=True);p.add_argument('--case',choices=['success','wrong-version','interrupt','reboot'],required=True)
p.add_argument('--expected-vm-uuid',required=True);p.add_argument('--disposable-vm',action='store_true');p.add_argument('--output',required=True)
a=p.parse_args()
def ps(script):return subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],text=True,encoding='utf-8').strip('\ufeff\r\n ')
machine=json.loads(ps("$c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_ComputerSystemProduct; @{model=$c.Model;manufacturer=$c.Manufacturer;uuid=$p.UUID}|ConvertTo-Json -Compress"))
assert a.disposable_vm and machine['uuid'].lower()==a.expected_vm_uuid.lower(),'Explicit disposable VM identity required'
assert any(x in (machine['model']+' '+machine['manufacturer']).lower() for x in ['virtual machine','vmware','virtualbox','qemu','kvm','parallels']),'Physical or unidentified host refused'
installer=pathlib.Path(a.installer).resolve();assert hashlib.sha256(installer.read_bytes()).hexdigest().lower()==a.sha256.lower(),'Installer hash mismatch'
root=pathlib.Path(a.output).resolve()/('vendor-'+a.kind+'-'+a.case+'-'+uuid.uuid4().hex);root.mkdir(parents=True)
base=[str(pathlib.Path(a.exe).resolve()),'--data-root',str(root/'data'),'--json']
def cli(*args):
 out=subprocess.run(base+list(args),text=True,encoding='utf-8',capture_output=True,timeout=90);obj=json.loads(out.stdout);assert out.returncode==0,obj;return obj['data']
cli('config','set','--env-root',str(root/'env'),'--tools-root',str(root/'tools'),'--initialize')
request=dict(action='bootstrap',kind=a.kind,name='vm-'+a.case,project='vendor-acceptance',source=a.source,archive=str(installer),sha256=a.sha256)
if a.case=='wrong-version':request['version']='0.0.0'
f=root/'plan.json';f.write_text(json.dumps(request),encoding='utf-8');op=cli('operation','plan','--file',str(f));worker=cli('status')['worker_pid']
apply=['operation','apply',op['id']]
for requirement in op['requirements']:apply+=['--confirm',requirement]
cli(*apply)
if a.case=='interrupt':
 deadline=time.monotonic()+300
 while time.monotonic()<deadline:
  op=cli('operation','show',op['id']);leases=list((root/'data/usage').glob('*.json'))
  live=[json.loads(path.read_text(encoding='utf-8')) for path in leases]
  own=next((l for l in live if l['id']==op['reliability']['lease_id'] and l.get('pid')),None)
  if (op['reliability'].get('vendor') or {}).get('installer_started') and own:
   # Only a captured, operation-owned process in this disposable VM is terminated.
   subprocess.run(['taskkill','/PID',str(own['pid']),'/T','/F'],capture_output=True)
   subprocess.run(['taskkill','/PID',str(worker),'/F'],capture_output=True,check=True);break
  time.sleep(.1)
 else:raise RuntimeError('Installer interrupt checkpoint not reached')
 time.sleep(1);cli('status')
deadline=time.monotonic()+900
while time.monotonic()<deadline:
 op=cli('operation','show',op['id'])
 terminal=op['status']!='executing' and op['reliability']['recovery']['status']!='running' and op['reliability']['cleanup']['status']!='running'
 if a.case=='wrong-version':terminal=terminal and op['reliability']['cleanup']['status'] in ('completed','blocked')
 if a.case=='interrupt':terminal=terminal and op['reliability']['recovery']['status'] in ('completed','blocked')
 if terminal:break
 time.sleep(.3)
else:raise RuntimeError('Operation timeout')
status='real_passed';detail=''
if a.case=='success':assert op['status']=='completed',op
elif a.case=='wrong-version':assert op['reliability']['cleanup']['status']=='completed',op;assert not pathlib.Path(op['resource']['path']).exists()
elif a.case=='interrupt':
 assert op['attempts']==1,op
 assert op['reliability']['recovery']['status'] in ('completed','blocked'),op
 detail='Uncertain product identity must stay blocked; no reinstall or guessed uninstall'
else:
 if op['resource']['metadata'].get('reboot_required'):
  assert op['reliability']['cleanup']['status']!='completed';detail='Actual vendor restart requirement preserved; no automatic restart'
 else:status='not_executed';detail='Installer did not emit restart-required; simulation cannot certify actual 3010 handling'
result=dict(status=status,detail=detail,machine=machine,kind=a.kind,case=a.case,installer_sha256=a.sha256,source=a.source,operation=op)
(root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
print('Revert the disposable VM snapshot before another case. AIRoot does not delete installed success-case runtimes.')
