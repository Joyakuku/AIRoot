"""Build an auditable, pinned offline payload. Does not change the machine installation."""
import argparse,hashlib,json,pathlib,re,shutil,subprocess,zipfile
p=argparse.ArgumentParser();p.add_argument('--stage',required=True);p.add_argument('--cargo',default='cargo');args=p.parse_args()
repo=pathlib.Path(__file__).resolve().parent.parent;stage=pathlib.Path(args.stage).resolve();stage.mkdir(parents=True,exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def command(*a):return subprocess.check_output(list(a),cwd=repo,text=True,encoding='utf-8')
lock=json.loads((repo/'packaging/components.lock.json').read_text(encoding='utf-8-sig'))
notices=json.loads((repo/'packaging/notices.lock.json').read_text(encoding='utf-8-sig'))
notice_paths=set()
for item in notices['files']:
    relative=pathlib.PurePosixPath(item['path'])
    if relative.is_absolute() or '..' in relative.parts or relative.parts[0]!='licenses':
        raise ValueError('Invalid notice path: '+item['path'])
    source=repo/'packaging'/relative
    if not source.is_file() or sha(source)!=item['sha256']:
        raise ValueError('Missing or modified pinned notice: '+item['path'])
    notice_paths.add(item['path'])
actual_notices={f.relative_to(repo/'packaging').as_posix() for f in (repo/'packaging/licenses').rglob('*') if f.is_file()}
if notice_paths!=actual_notices:
    raise ValueError('Notice inventory differs from notices.lock.json')
if not {'Everything.exe','es.exe','fd.exe','pet.exe'} <= {c['file'] for c in lock['components']}:
    raise ValueError('Mandatory embedded component missing')
shutil.copy2(repo/'target/release/airoot.exe',stage/'airoot.exe')
(stage/'components').mkdir()
for item in lock['components']:
    source=repo/'components'/item['file']
    if not source.is_file() or sha(source)!=item['sha256']:raise ValueError('Component hash mismatch: '+item['name'])
    shutil.copy2(source,stage/'components'/item['file'])
# Hashes alone cannot detect a missing app-local runtime on clean Windows.
pet_check=subprocess.run([str(stage/'components/pet.exe'),'--version'],capture_output=True,text=True,encoding='utf-8',timeout=15)
if pet_check.returncode or 'pet 0.1.0' not in pet_check.stdout:
    raise ValueError('PET executable readiness failed: '+repr((pet_check.returncode,pet_check.stdout,pet_check.stderr)))
(stage/'components/manifest.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
shutil.copytree(repo/'packaging/licenses',stage/'components/licenses')
shutil.copy2(repo/'packaging/notices.lock.json',stage/'components/notices.lock.json')
shutil.copytree(repo/'integrations/deepseek-harness',stage/'integrations/deepseek-harness',ignore=shutil.ignore_patterns('node_modules','*.tgz'))
(stage/'scripts').mkdir()
shutil.copy2(repo/'scripts/run-deepseek-airoot.ps1',stage/'scripts/run-deepseek-airoot.ps1')
shutil.copytree(repo/'docs',stage/'docs');shutil.copy2(repo/'README.md',stage/'README.md')
shutil.copy2(repo/'LICENSE',stage/'LICENSE')
(stage/'command-help.txt').write_text(command(str(repo/'target/release/airoot.exe'),'--help'),encoding='utf-8')
meta=json.loads(command(args.cargo,'metadata','--locked','--format-version','1'))
tree=command(args.cargo,'tree','--locked','--target','x86_64-pc-windows-msvc','--edges','normal,build','--prefix','none','--format','{p}')
needed={(a,b) for a,b in re.findall(r'^([\w-]+) v([^\s]+)',tree,re.M)}
notice=stage/'licenses';notice.mkdir();report=[]
for package in meta['packages']:
    if package['name']=='airoot' or (package['name'],package['version']) not in needed:continue
    root=pathlib.Path(package['manifest_path']).parent
    files=[f for f in root.iterdir() if f.is_file() and f.name.upper().startswith(('LICENSE','COPYING','NOTICE'))]
    if package.get('license_file'):
        f=root/package['license_file']
        if f.is_file() and f not in files:files.append(f)
    assert files, 'Missing third-party notice: '+package['name']
    dest=notice/(package['name']+'-'+package['version']);dest.mkdir()
    for f in files:shutil.copy2(f,dest/f.name)
    report.append({k:package.get(k) for k in ['name','version','license','repository','source']})
    if package['name']=='libsqlite3-sys':
        for f in root.rglob('sqlite3.c'):
            (dest/'SQLite-public-domain-notice.txt').write_text('\n'.join(f.read_text(encoding='utf-8').splitlines()[:40]),encoding='utf-8');break
rustc=str(pathlib.Path(args.cargo).with_name('rustc.exe')) if pathlib.Path(args.cargo).is_absolute() else 'rustc'
sysroot=pathlib.Path(command(rustc,'--print','sysroot').strip());rustdoc=sysroot/'share/doc/rust'
shutil.copytree(rustdoc/'licenses',notice/'rust-standard-library')
shutil.copy2(rustdoc/'COPYRIGHT-library.html',notice/'Rust-COPYRIGHT-library.html')
(notice/'dependency-inventory.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
(notice/'README.txt').write_text('AIRoot-owned source code is licensed under MIT; see LICENSE (copyright Joyakuku). This license does not cover third-party components. Third-party notices for the pinned build are included. Component licenses are in components/licenses. Rust package license expressions and upstream sources are in dependency-inventory.json. Preview for local evaluation; production publication requires the release gates in docs/实现与验收.md.\n',encoding='utf-8')
files=[{'path':f.relative_to(stage).as_posix(),'sha256':sha(f)} for f in sorted(stage.rglob('*')) if f.is_file()]
(stage/'package-manifest.json').write_text(json.dumps({'version':'0.1.1','channel':'preview','files':files},indent=2),encoding='utf-8')
archive=stage.parent/'payload.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,strict_timestamps=False) as z:
    for f in sorted(stage.rglob('*')):
        if f.is_file():z.write(f,f.relative_to(stage).as_posix())
print(str(archive))
