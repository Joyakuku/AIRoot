"""Collect pinned component dependency notices from matching upstream source checkouts."""
import argparse,json,pathlib,re,shutil,subprocess,hashlib
p=argparse.ArgumentParser();p.add_argument('--fd-source',required=True);p.add_argument('--pet-source',required=True);p.add_argument('--extension-source',required=True);p.add_argument('--cargo',default='cargo');a=p.parse_args();repo=pathlib.Path(__file__).resolve().parent.parent
records=[]
for name,source in [('fd',a.fd_source),('PET',a.pet_source)]:
    source=pathlib.Path(source).resolve()
    def cmd(*args):return subprocess.check_output([a.cargo,*args],cwd=source,text=True,encoding='utf-8')
    meta=json.loads(cmd('metadata','--locked','--format-version','1'))
    tree=cmd('tree','--locked','--target','x86_64-pc-windows-msvc','--edges','normal,build','--prefix','none','--format','{p}')
    needed={(n,v) for n,v in re.findall(r'^([\w-]+) v([^\s]+)',tree,re.M)}
    inventory=[]
    for package in meta['packages']:
        if not package.get('source') or (package['name'],package['version']) not in needed:continue
        directory=pathlib.Path(package['manifest_path']).parent
        files=[f for f in directory.iterdir() if f.is_file() and f.name.upper().startswith(('LICENSE','COPYING','NOTICE'))]
        if package.get('license_file'):
            f=directory/package['license_file']
            if f.is_file() and f not in files:files.append(f)
        assert files,'Missing notice '+package['name']
        dest=repo/'packaging/licenses'/('dependencies-'+name)/(package['name']+'-'+package['version']);dest.mkdir(parents=True,exist_ok=True)
        for f in files:shutil.copy2(f,dest/f.name)
        inventory.append({k:package.get(k) for k in ['name','version','license','repository']})
    (repo/'packaging/licenses'/('dependencies-'+name)/'inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    records.append(dict(component=name,source_lock_sha256=hashlib.sha256((source/'Cargo.lock').read_bytes()).hexdigest(),packages=len(inventory)))
for name in ['ThirdPartyNotices.txt','ThirdPartyNotices-Repository.txt']:
    shutil.copy2(pathlib.Path(a.extension_source)/name,repo/'packaging/licenses'/('Microsoft-Python-'+name))
files=[dict(path=f.relative_to(repo/'packaging').as_posix(),sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sorted((repo/'packaging/licenses').rglob('*')) if f.is_file()]
(repo/'packaging/notices.lock.json').write_text(json.dumps(dict(schema_version=1,components=records,files=files),indent=2),encoding='utf-8')
print(json.dumps(records,indent=2))
