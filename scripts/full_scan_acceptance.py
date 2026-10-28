"""Default disk boundaries and explicit pause across daemon restart; not a full coverage claim."""
import json,pathlib,subprocess,time,uuid
repo=pathlib.Path(__file__).resolve().parent.parent;root=repo/'work'/('scan-'+uuid.uuid4().hex);root.mkdir(parents=True);base=[str(repo/'target/debug/airoot.exe'),'--data-root',str(root/'data'),'--json']
def cli(*args):
    out=subprocess.run(base+list(args),capture_output=True,text=True,encoding='utf-8',timeout=40);obj=json.loads(out.stdout);assert out.returncode==0,obj;return obj['data']
status=cli('status');scan=cli('scan','start');assert set(scan['roots'])=={d['root'] for d in status['disks'] if d['eligible']}
cli('scan','pause',scan['id']);time.sleep(1)
assert cli('scan','status',scan['id'])['status']=='paused'
cli('worker-stop');time.sleep(.5)
assert cli('scan','status',scan['id'])['status']=='paused'
cli('scan','resume',scan['id']);cli('scan','pause',scan['id']);time.sleep(1)
assert cli('scan','status',scan['id'])['status']=='paused'
cli('components','stop');cli('worker-stop')
result=dict(passed=['Default scan selects only identified internal fixed disks; inventory reports eligibility','Explicit pause survives daemon restart; explicit resume and repause preserve scan ID'],disks=status['disks'],root=str(root))
(repo/'work/scan-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
