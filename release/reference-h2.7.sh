#!/bin/bash
set -euo pipefail
umask 007
export LC_ALL=C.UTF-8 PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export UV_NO_CONFIG=1 UV_CACHE_DIR="$HOME/uv-cache" UV_PYTHON_INSTALL_DIR="$HOME/python" UV_PYTHON_BIN_DIR="$HOME/bin"
unset PYTHONPATH PYTHONHOME PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST
test "$(id -un)" = montara-test
cd "$HOME"
OUT="$MONTARA_OUTPUT/h2-27-v5-reference"
if [ -d "$OUT" ] && [ ! -L "$OUT" ] && [ -O "$OUT" ]; then rmdir "$OUT"; fi
test ! -e "$OUT"
mkdir "$OUT"
# Evidence files shared with the authorized connector; scratch stays private.
export OUT
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
echo "TARGET=$PRETTY_NAME x86_64"
export CORE=36b010c172370a9c59762cf355e782adc00c3de0 PREV=40f5a8e768da7607bc5b9dd94ec299f009bd6861 JM=1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d ENGINE=9af275b18535005e36bd289a68ceb4a1d2f6c506
cat > "$HOME/release.py" <<'PY'
import os,sys,subprocess as sp,hashlib,json,shutil,sqlite3,tarfile,zipfile,time,resource,threading,re,stat
from pathlib import Path
from contextlib import contextmanager
home=Path.home()
out=Path(os.environ["OUT"])
core,jm,engine,prev=(os.environ[k] for k in ("CORE","JM","ENGINE","PREV"))
metrics=[]
cg=Path("/sys/fs/cgroup")/next(l.split(":",2)[-1].lstrip("/") for l in Path("/proc/self/cgroup").read_text().splitlines() if l.startswith("0:"))
def cnum(n):
    try: return int((cg/n).read_text().strip())
    except (OSError,ValueError): return None
def cpu():
    a=resource.getrusage(resource.RUSAGE_SELF);b=resource.getrusage(resource.RUSAGE_CHILDREN)
    return (a.ru_utime+b.ru_utime,a.ru_stime+b.ru_stime)
def size(p):
    return sum(x.stat().st_size for x in p.rglob("*") if x.is_file() and not x.is_symlink()) if p.exists() else 0
def sha(p):
    with open(p,"rb") as f: return hashlib.file_digest(f,"sha256").hexdigest()
def save(name,obj):
    p=out/name;p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n");pass
def run(*a,cwd=None,env=None,capture=False):
    p=sp.run([str(x) for x in a],cwd=cwd,env=env,text=True,stdout=sp.PIPE if capture else None,stderr=sp.STDOUT if capture else None)
    if p.returncode:
        if capture: print(p.stdout[-6000:],flush=True)
        raise RuntimeError("command failed: "+str(a[0])+" rc="+str(p.returncode))
    return p.stdout
@contextmanager
def phase(name,measure_path=None):
    print("PHASE="+name,flush=True)
    t=time.perf_counter();u,s=cpu();disk=shutil.disk_usage(home).used;initial=size(measure_path) if measure_path else None
    stop=threading.Event();peak=[cnum("memory.current")]
    def sample():
        while not stop.wait(.2):
            n=cnum("memory.current")
            if n is not None: peak[0]=max(peak[0] or 0,n)
    th=threading.Thread(target=sample,daemon=True);th.start()
    ok=False
    try:
        yield
        ok=True
    finally:
        stop.set();th.join()
        n=cnum("memory.current")
        if n is not None: peak[0]=max(peak[0] or 0,n)
        u2,s2=cpu()
        rec=dict(phase=name,ok=ok,wall_s=round(time.perf_counter()-t,3),cpu_user_s=round(u2-u,3),cpu_system_s=round(s2-s,3),cgroup_sample_peak_bytes=peak[0],child_maxrss_kib_cumulative=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,filesystem_used_delta_bytes=shutil.disk_usage(home).used-disk,measured_tree_before_bytes=initial,measured_tree_after_bytes=size(measure_path) if measure_path else None)
        metrics.append(rec);save("metrics-install.json",metrics);print("PHASE_RESULT="+json.dumps(rec),flush=True)
def clone(name,url,rev):
    p=home/name
    run("git","-c","credential.helper=","clone","--quiet","--no-checkout",url,p)
    run("git","checkout","--quiet","--detach",rev,cwd=p)
    assert run("git","rev-parse","HEAD",cwd=p,capture=True).strip()==rev
    assert not run("git","status","--porcelain",cwd=p,capture=True).strip()
    return p
def pip(exe,*args,**kwargs): return run(exe,"-m","pip",*args,**kwargs)
def lock_wheels(d):
    return [{"filename":p.name,"sha256":sha(p),"size":p.stat().st_size} for p in sorted(d.glob("*.whl"))]
print("CPU_AFFINITY="+str(len(os.sched_getaffinity(0))),"DISK_FREE_GIB="+str(round(shutil.disk_usage(home).free/2**30,2)),flush=True)
assert shutil.disk_usage(home).free>=15*2**30
with phase("sources"):
    repo=clone("core-src","https://github.com/Montara-Projects/Montara-Core.git",core)
    renderer=clone("renderer-src","https://github.com/Montara-Projects/Montara.git",jm)
    eng=clone("engine-src","https://github.com/amehcgracia-byte/zucker_videos.git",engine)
prior=repo/"release/manifests"
oldpy=json.loads((prior/"h2.6-python-lock.json").read_text())
oldff=json.loads((prior/"h2.6-ffmpeg-lock.json").read_text())
oldmanifest=json.loads((prior/"h2.6.json").read_text())
with phase("python_bootstrap"):
    boot=home/"uv-bootstrap";run(sys.executable,"-m","venv",boot)
    uvw=home/"uv-wheels";uvw.mkdir();pip(boot/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",uvw,"uv==0.9.18")
    assert lock_wheels(uvw)==oldpy["uv"],"uv wheel drift"
    pip(boot/"bin/python","install","--quiet","--no-index","--no-deps",*uvw.glob("*.whl"))
    uv=boot/"bin/uv";run(uv,"python","install","3.12.12")
    py=Path(run(uv,"python","find","--managed-python","3.12.12",capture=True).strip())
    assert run(py,"-c","import sys;print(sys.version.split()[0])",capture=True).strip()=="3.12.12"
with phase("ffmpeg_local_closure"):
    # Use APT's signed-index candidate closure, but only download and extract.
    # No apt install/update, dpkg database change or maintainer scripts.
    packages=oldff["packages"]
    assert len(packages)==148
    debs=home/"debs";debs.mkdir()
    run("apt-get","-qq","download",*packages,cwd=debs)
    ffroot=home/"ffmpeg-root";ffroot.mkdir()
    deb_lock=[]
    for d in sorted(debs.glob("*.deb")):
        ident=run("dpkg-deb","-f",d,"Package","Version","Architecture",capture=True).strip()
        deb_lock.append({"filename":d.name,"metadata":ident,"sha256":sha(d),"size":d.stat().st_size})
        run("dpkg-deb","-x",d,ffroot)
    assert len(deb_lock)==len(packages),(len(deb_lock),len(packages))
    assert deb_lock==oldff["debs"],"FFmpeg .deb drift"
    bindir=home/"media-tools";bindir.mkdir()
    lib=ffroot/"usr/lib/x86_64-linux-gnu"
    libdirs=[lib,ffroot/"lib/x86_64-linux-gnu"]
    libdirs+=sorted({p.parent for p in ffroot.rglob("*.so*") if p.is_file()}-set(libdirs))
    libpath=":".join(map(str,libdirs))
    for name in ("ffmpeg","ffprobe"):
        real=ffroot/"usr/bin"/name
        assert real.is_file()
        w=bindir/name
        w.write_text('#!/bin/sh\nexport LD_LIBRARY_PATH="'+libpath+'"\nexec "'+str(real)+'" "$@"\n')
        w.chmod(0o700)
    os.environ["PATH"]=str(bindir)+":"+os.environ["PATH"]
    ffver=run("ffmpeg","-version",capture=True).splitlines()[0]
    fpver=run("ffprobe","-version",capture=True).splitlines()[0]
    assert "6.1.1" in ffver and "6.1.1" in fpver
    enc=run("ffmpeg","-hide_banner","-encoders",capture=True)
    assert "libx264" in enc and " aac " in enc
    run("ffmpeg","-v","error","-f","lavfi","-i","color=black:s=64x64:r=30","-t","0.1","-c:v","libx264","-f","null","-")
    # Record dynamic closure actually resolved, including pre-existing host libraries.
    host_libs={}
    for name in ("ffmpeg","ffprobe"):
        lddenv=dict(os.environ,LD_LIBRARY_PATH=libpath)
        lines=run("ldd",ffroot/"usr/bin"/name,env=lddenv,capture=True).splitlines()
        assert not any("not found" in l for l in lines)
        for line in lines:
            match=re.search(r"(?:=> )?(/[^ ]+)",line)
            if match:
                p=Path(match.group(1))
                if p.is_file() and not p.is_relative_to(ffroot):
                    host_libs[str(p)]=sha(p)
    assert host_libs==oldff["host_library_sha256"],"FFmpeg host library drift"
    assert {n:sha(ffroot/"usr/bin"/n) for n in ("ffmpeg","ffprobe")}==oldff["binary_sha256"]
    print("FFMPEG_V4_LOCK_REPLAY=PASS",flush=True)
    save("ffmpeg-lock.json",{"source":"Ubuntu 24.04 signed APT indices (download only)","requested":"ffmpeg=7:6.1.1-3ubuntu5","packages":packages,"debs":deb_lock,"host_library_sha256":host_libs,"ffmpeg":ffver,"ffprobe":fpver,"binary_sha256":{n:sha(ffroot/"usr/bin"/n) for n in ("ffmpeg","ffprobe")},"redistribute":False})
    print("FFMPEG="+ffver+" packages="+str(len(deb_lock)),flush=True)
with phase("python_wheel_closure"):
    wheelhouse=home/"wheelhouse";wheelhouse.mkdir()
    oidc_req=["PyJWT==2.15.1","cryptography==50.0.2","cffi==2.1.1","pycparser==3.12"]
    req=oldpy["runtime"]+oidc_req
    buildreq=oldpy["build"]
    downloadenv=home/"download-env";run(py,"-m","venv",downloadenv)
    pip(downloadenv/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",wheelhouse,*req,*buildreq)
    wlocks=lock_wheels(wheelhouse)
    assert len(wlocks)==len(req)+len(buildreq)
    assert all(x in wlocks for x in oldpy["wheel_files"]),"historical Python wheel drift"
    print("PYTHON_V4_LOCK_REPLAY=PASS",flush=True)
    def locked(requirements,dest):
        lines=[]
        for r in requirements:
            name,version=r.split("==")
            name=name.lower().replace("-","_")
            matches=[p for p in wheelhouse.glob("*.whl") if p.name.lower().split("-")[0]==name and p.name.split("-")[1]==version]
            assert len(matches)==1,r
            lines.append(r+" --hash=sha256:"+sha(matches[0]))
        dest.write_text("\n".join(lines)+"\n")
    runtime_lock=home/"runtime.lock";build_lock=home/"build.lock"
    locked(req,runtime_lock);locked(buildreq,build_lock)
    v4lock=home/"runtime-v4.lock";v4lock.write_text(oldpy["runtime_lock"])
    oidclock=home/"oidc.lock";locked(oidc_req,oidclock)
    save("python-lock.json",{"python":"3.12.12","uv":lock_wheels(uvw),"runtime":req,"oidc_additions":oidc_req,"oidc_lock":oidclock.read_text(),"historical_python_hashes_reused":True,"build":buildreq,"wheel_files":wlocks,"runtime_lock":runtime_lock.read_text(),"build_lock":build_lock.read_text()})
    buildenv=home/"build-env";run(py,"-m","venv",buildenv)
    build=buildenv/"bin/python"
    pip(build,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",build_lock)
    pip(build,"check")
def make(label,rev,schema):
    src=home/("source-"+label);src.mkdir()
    assert run("git","rev-parse",rev+"^{commit}",cwd=repo,capture=True).strip()==rev
    epoch=run("git","show","-s","--format=%ct",rev,cwd=repo,capture=True).strip()
    tree=run("git","rev-parse",rev+"^{tree}",cwd=repo,capture=True).strip()
    tar=home/(label+".tar");run("git","archive","--format=tar","-o",tar,rev,cwd=repo)
    with tarfile.open(tar) as t:t.extractall(src,filter="data")
    tar.unlink();d=home/("wheel-"+label);d.mkdir()
    pip(build,"wheel","--quiet","--no-build-isolation","--no-index","--no-deps","-w",d,src,env=dict(os.environ,SOURCE_DATE_EPOCH=epoch,PYTHONHASHSEED="0"))
    w,=d.glob("*.whl")
    with zipfile.ZipFile(w) as z:
        files={n:hashlib.sha256(z.read(n)).hexdigest() for n in sorted(z.namelist()) if not n.endswith("/")}
    return w,{"source_commit":rev,"source_tree":tree,"schema":schema,"wheel":w.name,"sha256":sha(w),"files":files,"source_date_epoch":int(epoch)}
with phase("build_core"):
    wa,ma=make("v5-a",core,5);wb,mb=make("v5-b",core,5)
    wv4,mv4=make("v4",prev,4)
    assert ma["files"]==mb["files"] and ma["sha256"]==mb["sha256"]
    assert mv4["sha256"]==oldmanifest["core"]["sha256"],"v4 rebuild drift"
    print("DOUBLE_BUILD_V5=PASS V4_REBUILD_HASH=PASS",flush=True)
inputdir=Path(os.environ["MONTARA_INPUT"])
archived_wheel=inputdir/"h2-27-v4-montara_core-0.1.0-py3-none-any.whl"
archived_mp4=inputdir/"h2-27-v4-published-fixture.mp4"
expected_mp4="e54dad8764762ef56c0e362dc27ac5f280fb29713a3f87bcf7647c5979e4830f"
assert sha(archived_wheel)==mv4["sha256"] and sha(archived_mp4)==expected_mp4
print("ARCHIVED_V4_WHEEL_AND_MP4=PASS",flush=True)
install=home/"reference-install";releases=install/"releases";state=install/"state";configdir=install/"config";outside=home/"outside"
for d in (releases,state,configdir,outside):d.mkdir(parents=True)
media=state/"originals";work=state/"attempts";artifacts=state/"artifacts"
for d in (media,work,artifacts):d.mkdir()
db=state/"core.sqlite";cfg=configdir/"core.toml"
cfg.write_text('[http]\nport = 0\n[storage]\n'+"".join(k+" = "+json.dumps(str(v))+"\n" for k,v in [("sqlite_path",db),("media_root",media),("workspace_root",work),("artifact_root",artifacts)]))
cfghash=sha(cfg);active=install/"current"
def version():
    if not db.exists():return 0
    with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as c:return c.execute("PRAGMA user_version").fetchone()[0]
def rows():
    with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()==("ok",)
        assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
        tables=[n for n, in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n:sorted(c.execute('SELECT * FROM "'+n.replace('"','""')+'"').fetchall(),key=repr) for n in tables}
def manifest_check(slot):
    m=json.loads((slot/"manifest.json").read_text());assert sha(slot/m["wheel"])==m["sha256"];return m
def install_slot(name,w,m):
    assert sha(w)==m["sha256"]
    slot=releases/name;slot.mkdir();shutil.copyfile(w,slot/m["wheel"]);(slot/"manifest.json").write_text(json.dumps(m))
    run(py,"-m","venv",slot/"venv");exe=slot/"venv/bin/python"
    pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",runtime_lock if m["schema"]==5 else v4lock)
    # Explicit extra selection; resolver must find its closure already installed offline.
    target=str(slot/m["wheel"])+("[oidc]" if m["schema"]==5 else "")
    pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,target)
    pip(exe,"check")
    run(exe,"-I","-c","import pathlib,sys,montara_core; assert pathlib.Path(montara_core.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); print('INSTALLED_PACKAGE=PASS')",cwd=outside)
    run(exe,"-I","-m","montara_core","--check-config","--config",cfg,cwd=outside)
    return slot
def activate(slot):
    m=manifest_check(slot)
    if version() not in (0,m["schema"]):raise ValueError("schema mismatch")
    p=install/"current.next";p.symlink_to(slot.relative_to(install));p.replace(active)
def tree(slot):
    site=Path(run(slot/"venv/bin/python","-I","-c","import site; print(site.getsitepackages()[0])",capture=True).strip())
    return {str(p.relative_to(site)):sha(p) for d in site.iterdir() if d.is_dir() for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name not in {"RECORD","direct_url.json"}}
v4ids=home/"v4-ids.json";oidcids=home/"oidc-ids.json";v5ids=home/"v5-ids.json"
with phase("install_v4_and_seed",state):
    oldslot=install_slot("schema4-historical",archived_wheel,mv4);activate(oldslot)
    code="""import sys,json,shutil,hashlib,importlib.util
from pathlib import Path
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import Shot
from montara_core.application.synthetic_worker import run_once
cfg,jm,tests,archived,ids=map(Path,sys.argv[1:])
sys.path.insert(0,str(tests))
from test_publication import candidate_for
c=build_core(load_config(cfg))
base=cfg.parent.parent/'state'
loader=importlib.util.spec_from_file_location('jm_fixture',jm/'tools/core_v2_media_fixture.py')
media=importlib.util.module_from_spec(loader);loader.loader.exec_module(media)
bank=base/'fixture-bank';media.generate(bank);media.verify_inputs(bank)
owner=c.projects.provision_account().id
other=c.projects.provision_account().id
p=c.projects.create_project(owner,'Historical v4 project')
p2=c.projects.create_project(other,'Other historical account')
folder=base/'originals'/p.id;folder.mkdir()
for n in ('camera-a.mp4','camera-b.mp4','master.wav'):shutil.copyfile(bank/n,folder/n)
_,a,_=c.media.register_original(owner,p.id,'video','camera-a.mp4')
_,b,_=c.media.register_original(owner,p.id,'video','camera-b.mp4')
_,m,_=c.media.register_original(owner,p.id,'audio','master.wav')
recipe=c.media.save_recipe(owner,p.id,None,m.id,(Shot(a.id,0,2000),Shot(b.id,2000,4000),Shot(a.id,4000,6000)))
job=c.jobs.create_job(owner,p.id,'export',recipe.id);ref=c.jobs.claim()
spec=c.media.prepare_execution(ref)
# Explicit fixture double: archived real MP4, newly created test attempt/attestation.
# No claim that this historical database or its attempt IDs survived #25.
candidate=candidate_for(spec,archived.read_bytes())
receipt=c.publication.publish(owner,ref,candidate)
assert receipt.sha256=='e54dad8764762ef56c0e362dc27ac5f280fb29713a3f87bcf7647c5979e4830f'
media.decode(receipt.path)
j=c.jobs.create_job(other,p2.id,'historical-synthetic');done=run_once(c.jobs)
assert j.id==done.id and done.state=='succeeded'
ids.write_text(json.dumps({'owner':owner,'other':other,'project':p.id,'other_project':p2.id,'recipe':recipe.id,'job':job.id,'sha256':receipt.sha256,'artifact':receipt.path.stem,'snapshot':receipt.snapshot_sha256,'synthetic_job':j.id,'synthetic_result':done.result}))
print('V4_REAL_PACKAGE_STATE_AND_ARCHIVED_MP4=PASS')
"""
    run(active/"venv/bin/python","-I","-c",code,cfg,renderer,home/"source-v4/tests",archived_mp4,v4ids,cwd=outside)
    assert version()==4
    historical=rows();inode=db.stat().st_ino
    oldid=json.loads(v4ids.read_text());published=artifacts/(oldid["artifact"]+".mp4");assert sha(published)==expected_mp4
    print("V4_TABLES="+str(len(historical))+" ROWS="+str(sum(map(len,historical.values()))),flush=True)
with phase("install_v5"):
    a=install_slot("schema5-a",wa,ma)
    exe=a/"venv/bin/python"
    run(exe,"-I","-c","import jwt,cryptography,cffi,pycparser,numpy,cv2,scipy,PIL; from montara_core.adapters.zucker_engine import ZuckerEngineAdapter; from importlib.metadata import version; print('COMBINED_IMPORTS=PASS '+str({n:version(n) for n in ['PyJWT','cryptography','cffi','pycparser']}))",cwd=outside)
    run(exe,"-I","-m","unittest","discover","-s",repo/"tests","-v",cwd=outside)
    print("ORIGINAL_SUITE=PASS",flush=True)
    assert version()==4 and rows()==historical
verify="""import json,sys,hashlib
from pathlib import Path
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
c=build_core(load_config(Path(sys.argv[1])))
d=json.loads(Path(sys.argv[2]).read_text())
assert c.projects.get_project(d['owner'],d['project']).name=='Historical v4 project'
assert c.projects.get_project(d['other'],d['other_project']).name=='Other historical account'
assert len(c.media.get_recipe(d['owner'],d['recipe']).shots)==3
r=c.publication.get_artifact(d['owner'],d['job'])
assert r.sha256==d['sha256'] and r.snapshot_sha256==d['snapshot'] and r.path.stem==d['artifact']
assert c.jobs.get_job(d['owner'],d['job']).state=='succeeded'
assert c.jobs.get_job(d['other'],d['synthetic_job']).result==d['synthetic_result']
print('HISTORICAL_RECEIPT_AND_ROWS=PASS')
"""
with phase("migrate_v4_to_v5",state):
    # Explicit one-way migration through installed v5; no replacement of DB or restore.
    run(a/"venv/bin/python","-I","-c",verify,cfg,v4ids,cwd=outside)
    assert version()==5 and db.stat().st_ino==inode and sha(published)==expected_mp4
    post=rows()
    assert set(post)==set(historical)|{"oidc_identities"}
    assert all(post[n]==v for n,v in historical.items()) and post["oidc_identities"]==[]
    activate(a)
    print("V4_TO_V5_REAL_MIGRATION=PASS ALL_HISTORICAL_ROWS_EQUAL",flush=True)
with phase("oidc_on_migrated_state",state):
    code="""import json,sys,threading
from pathlib import Path
from http.client import HTTPConnection
from urllib.parse import urlencode
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
from montara_core.adapters.oidc import OIDCProvider,read_client_secret
from montara_core.application.identity import AuthenticationService
from montara_core.api.authenticated import create_authenticated_server,SESSION
cfg,tests,oldids,ids=map(Path,sys.argv[1:])
sys.path.insert(0,str(tests))
from test_oidc_http import TestIdP
root=cfg.parent/'oidc-test';root.mkdir(mode=0o700)
idp=TestIdP(root)
server=None
try:
    secret=root/'client-secret'
    import os
    with os.fdopen(os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:f.write(idp.secret)
    settings=idp.settings(secret)
    oidccfg=root/'external-oidc.toml'
    oidccfg.write_text(cfg.read_text()+'\\n[oidc]\\n'+'\\n'.join(k+' = '+json.dumps(str(getattr(settings,k))) for k in ('issuer','authorization_endpoint','token_endpoint','jwks_uri','client_id','client_secret_file','public_origin'))+'\\n')
    config=load_config(oidccfg);c=build_core(config)
    auth=AuthenticationService(c.identity,OIDCProvider(settings,read_client_secret(secret),ssl_context=idp.context))
    server=create_authenticated_server(0,settings.public_origin,c,auth)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    def req(method,path,cookie=None,body=None):
        h={'Host':'montara.test'}
        if cookie:h['Cookie']=cookie
        if body is not None:h.update({'Content-Type':'application/json','Origin':settings.public_origin});body=json.dumps(body).encode()
        x=HTTPConnection('127.0.0.1',server.server_port,timeout=15)
        try:
            x.request(method,path,body=body,headers=h);r=x.getresponse();return r.status,r.headers,r.read()
        finally:x.close()
    historical=json.loads(oldids.read_text())
    subject=historical['owner'] # deliberate same text; must not auto-link
    def login(subject):
        status,h,_=req('GET','/auth/login');assert status==303
        state,code=idp.issue(h['Location'],subject)
        status,h,_=req('GET','/auth/callback?'+urlencode({'state':state,'code':code}),h['Set-Cookie'].split(';',1)[0]);assert status==303
        cookie=next(v.split(';',1)[0] for v in h.get_all('Set-Cookie') if v.startswith(SESSION+'='))
        status,_,raw=req('GET','/api/me',cookie);assert status==200
        return cookie,json.loads(raw)['account_id']
    cookie,owner=login(subject);assert owner!=historical['owner']
    _,again=login(subject);assert again==owner
    cookie2,other=login('different-subject');assert other!=owner
    assert req('GET','/api/jobs/'+historical['job']+'/artifact',cookie)[0]==404
    assert req('GET','/api/jobs/'+historical['job']+'/artifact')[0]==401
    status,_,raw=req('POST','/api/projects',cookie,{'name':'Post migration OIDC project'});assert status==201
    project=json.loads(raw)['id']
    assert req('GET','/api/projects/'+project,cookie2)[0]==404
    ids.write_text(json.dumps({'owner':owner,'issuer':idp.issuer,'subject':subject,'project':project,'other':other}))
    print('OIDC_HTTPS_IDP_MIGRATED_DB=PASS NO_AUTOLINK=PASS HTTP_WRITE=PASS')
finally:
    if server:server.shutdown();server.server_close();t.join(timeout=5)
    idp.close()
"""
    run(exe,"-I","-c",code,cfg,repo/"tests",v4ids,oidcids,cwd=outside)
    assert version()==5 and sha(published)==expected_mp4
    run(exe,"-I","-c",verify,cfg,v4ids,cwd=outside)
gate=home/"gate.py"
gate.write_text("\"\"\"Single canonical positive gate; adapted from tests/engine_h2_smoke.py at pinned Core.\"\"\"\nimport importlib.util,json,sys,shutil,subprocess,array,os,time,resource,threading\nfrom pathlib import Path\nfrom contextlib import contextmanager\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.domain.execution import Shot,ExecutionSpec,EngineCandidate\nfrom montara_core.domain.jobs import JobState\nfrom montara_core.domain.publication import ArtifactReceipt\nfrom montara_core.adapters.zucker_engine import ZuckerEngineAdapter,_stage_semantics\njm,engine,cfg,ids,out=map(lambda x:Path(x).resolve(),sys.argv[1:])\nconfig=load_config(cfg)\nbase=cfg.parent.parent/\"state\"\nbank=base/\"fixture-bank\"\nloader=importlib.util.spec_from_file_location(\"jm_fixture\",jm/\"tools/core_v2_media_fixture.py\")\nmedia=importlib.util.module_from_spec(loader);loader.loader.exec_module(media)\nmetrics=[]\ncg=Path(\"/sys/fs/cgroup\")/next(l.split(\":\",2)[-1].lstrip(\"/\") for l in Path(\"/proc/self/cgroup\").read_text().splitlines() if l.startswith(\"0:\"))\ndef mem():\n    try:return int((cg/\"memory.current\").read_text().strip())\n    except (OSError,ValueError):return None\ndef cpu():\n    a=resource.getrusage(resource.RUSAGE_SELF);b=resource.getrusage(resource.RUSAGE_CHILDREN)\n    return a.ru_utime+b.ru_utime,a.ru_stime+b.ru_stime\ndef size(root):\n    return sum(p.stat().st_size for p in root.rglob(\"*\") if p.is_file() and not p.is_symlink()) if root.exists() else 0\ndef rss_tree():\n    # Current process and descendants, including the runner's separate process group.\n    info={}\n    for p in Path(\"/proc\").iterdir():\n        if not p.name.isdecimal():continue\n        try:\n            s=(p/\"stat\").read_text().rsplit(\")\",1)[1].split()\n            info[int(p.name)]=(int(s[1]),int(s[21])*os.sysconf(\"SC_PAGE_SIZE\"))\n        except (OSError,ValueError,IndexError):pass\n    seen={os.getpid()}\n    while True:\n        more={pid for pid,(parent,rss) in info.items() if parent in seen}-seen\n        if not more:break\n        seen.update(more)\n    return sum(info[p][1] for p in seen if p in info)\n@contextmanager\ndef phase(name):\n    start=time.perf_counter();u,s=cpu();initial=size(base);stop=threading.Event();peaks=[mem(),rss_tree(),initial]\n    def sample():\n        while not stop.wait(.2):\n            m=mem()\n            if m is not None:peaks[0]=max(peaks[0] or 0,m)\n            peaks[1]=max(peaks[1],rss_tree())\n            try:peaks[2]=max(peaks[2],size(base))\n            except OSError:pass\n    t=threading.Thread(target=sample,daemon=True);t.start();ok=False\n    try:\n        yield;ok=True\n    finally:\n        stop.set();t.join();u2,s2=cpu();end=size(base);peaks[2]=max(peaks[2],end)\n        row=dict(phase=name,ok=ok,wall_s=round(time.perf_counter()-start,3),cpu_user_s=round(u2-u,3),cpu_system_s=round(s2-s,3),cgroup_sample_peak_bytes=peaks[0],process_tree_rss_sample_peak_bytes=peaks[1],state_before_bytes=initial,state_after_bytes=end,state_sample_peak_bytes=peaks[2])\n        metrics.append(row);p=out/\"metrics-av.json\";p.write_text(json.dumps(metrics,indent=2)+\"\\n\");pass\n        print(\"AV_PHASE=\"+json.dumps(row),flush=True)\nwith phase(\"fixture_generation\"):\n    assert bank.is_dir();original_manifest=media.verify_inputs(bank)\nwith phase(\"recipe_and_staging\"):\n    c=build_core(config);owner=json.loads((Path.home()/'oidc-ids.json').read_text())['owner']\n    project=c.projects.create_project(owner,\"Canonical fixture\").id\n    folder=config.media_root/project;folder.mkdir()\n    for name in (\"camera-a.mp4\",\"camera-b.mp4\",\"master.wav\"):shutil.copyfile(bank/name,folder/name)\n    _,a,_=c.media.register_original(owner,project,\"video\",\"camera-a.mp4\")\n    _,b,_=c.media.register_original(owner,project,\"video\",\"camera-b.mp4\")\n    _,m,_=c.media.register_original(owner,project,\"audio\",\"master.wav\")\n    recipe=c.media.save_recipe(owner,project,None,m.id,(Shot(a.id,0,2000),Shot(b.id,2000,4000),Shot(a.id,4000,6000)))\n    job=c.jobs.create_job(owner,project,\"export\",recipe.id);ref=c.jobs.claim()\n    assert ref.job_id==job.id\n    spec=c.media.prepare_execution(ref);assert isinstance(spec,ExecutionSpec)\n    assert {p.path for p in spec.inputs}==set(spec.workspace.iterdir())\n    originals={p.name:media.sha256(p) for p in folder.iterdir()}\n    staged={p.path:media.sha256(p.path) for p in spec.inputs}\nwith phase(\"engine_real\"):\n    adapter=ZuckerEngineAdapter(jm,engine)\n    candidate=adapter.execute(spec);assert isinstance(candidate,EngineCandidate)\n    assert c.jobs.get_job(owner,job.id).state==JobState.RUNNING\nwith phase(\"candidate_quality\"):\n    assert {p.name:media.sha256(p) for p in folder.iterdir()}==originals\n    assert all(media.sha256(p)==h for p,h in staged.items())\n    assert media.verify_inputs(bank)==original_manifest\n    att=json.loads((spec.workspace/\"engine\"/\"candidate-manifest.json\").read_text())\n    assert att[\"schema\"]==2 and att[\"snapshot_sha256\"]==spec.snapshot_sha256\n    assert att[\"stage_semantics\"]==_stage_semantics()\n    assert att[\"candidate\"]=={k:v for k,v in vars(candidate).items() if k!=\"path\"}\n    request=json.loads((spec.workspace/\"engine\"/\"request.json\").read_text())\n    assert set(request[\"source_files\"].values())|{request[\"master\"]}=={str(i.path) for i in spec.inputs}\n    assert not (spec.workspace/\"engine\"/\"inputs\").exists()\n    info=media.probe(candidate.path);assert abs(float(info[\"format\"][\"duration\"])-6)<.15\n    media.decode(candidate.path)\n    frames=[]\n    for at,color in [(1.5,\"red\"),(2.25,\"blue\"),(2.8,\"blue\"),(3.5,\"yellow\"),(4.5,\"green\")]:\n        actual,rgb=media.frame_color(candidate.path,at);assert actual==color,(at,actual,color)\n        frames.append({\"at\":at,\"color\":actual,\"rgb\":rgb})\n    tones=[]\n    for at in (1,3,5):\n        raw=media.execute([\"ffmpeg\",\"-v\",\"error\",\"-ss\",str(at),\"-i\",str(candidate.path),\"-t\",\"0.25\",\"-vn\",\"-ac\",\"1\",\"-ar\",str(media.RATE),\"-f\",\"s16le\",\"-\"])\n        samples=array.array(\"h\",raw)\n        if sys.byteorder!=\"little\":samples.byteswap()\n        assert len(samples)>=media.RATE//8\n        values={hz:media.tone_amplitude(samples,hz) for hz in (223,443,997)}\n        assert values[997]>=50 and values[997]>=10*max(values[223],values[443]),values\n        tones.append({\"at\":at,\"amplitudes\":values})\nwith phase(\"publication_recovery\"):\n    receipt=c.publication.recover(owner,ref)\n    assert isinstance(receipt,ArtifactReceipt) and receipt.path.parent==config.artifact_root\n    assert receipt.path!=candidate.path and receipt.sha256==candidate.sha256==media.sha256(receipt.path)\n    assert receipt.snapshot_sha256==spec.snapshot_sha256 and receipt.stage_profile==\"stage-engineering-v0\"\n    assert c.jobs.get_job(owner,job.id).state==JobState.SUCCEEDED\n    assert c.jobs.get_task(owner,job.id).state==JobState.SUCCEEDED\n    assert c.publication.publish(owner,ref,candidate)==receipt\nwith phase(\"reopen_separate_process\"):\n    code=\"\"\"import sys\nfrom pathlib import Path\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.domain.jobs import JobState\nc=build_core(load_config(Path(sys.argv[1])))\nr=c.publication.get_artifact(sys.argv[2],sys.argv[3])\nassert c.jobs.get_job(sys.argv[2],sys.argv[3]).state==JobState.SUCCEEDED\nassert r.sha256==sys.argv[4]\nprint('PUBLISHED_REOPEN=PASS')\n\"\"\"\n    assert \"PUBLISHED_REOPEN=PASS\" in subprocess.check_output([sys.executable,\"-I\",\"-c\",code,str(cfg),owner,job.id,receipt.sha256],cwd=cfg.parent,text=True,timeout=30)\n    media.decode(receipt.path)\n    assert media.sha256(receipt.path)==candidate.sha256\n    ids.write_text(json.dumps({\"owner\":owner,\"job\":job.id,\"sha256\":receipt.sha256,\"artifact\":receipt.path.stem}))\nreport={k:v for k,v in vars(candidate).items() if k!=\"path\"}\nreport.update(status=\"PASS\",duration_sec=float(info[\"format\"][\"duration\"]),frames=frames,tones=tones,resolution=\"1920x1080\",fps=30,video_codec=\"h264\",audio_codec=\"aac\",full_decode=\"PASS\",originals_unchanged=True,direct_staged_inputs=True,canonical_execution_spec=True,core_job_succeeded=True,publication=\"PASS\",published_reopen=\"PASS\",published_digest_verified=True,stage_semantics=att[\"stage_semantics\"])\np=out/\"quality.json\";p.write_text(json.dumps(report,indent=2)+\"\\n\");pass\nprint(\"CANONICAL_AV=PASS\",flush=True)\n")
with phase("canonical_v5_av",state):
    run(exe,"-I",gate,renderer,eng,cfg,v5ids,out,cwd=outside)
assert version()==5 and sha(published)==expected_mp4

with phase("reinstall_and_recovery",state):
    b=install_slot("schema5-b",wb,mb)
    assert tree(a)==tree(b),"installed tree drift"
    before=rows();activate(b);assert rows()==before
    check=verify+"""
from montara_core.application.synthetic_worker import run_once
o=json.loads(Path(sys.argv[3]).read_text())
assert c.identity.resolve_identity(o['issuer'],o['subject'])==o['owner']
assert c.projects.get_project(o['owner'],o['project']).name=='Post migration OIDC project'
v=json.loads(Path(sys.argv[4]).read_text())
assert c.publication.get_artifact(v['owner'],v['job']).sha256==v['sha256']
"""
    run(active/"venv/bin/python","-I","-c",check,cfg,v4ids,oidcids,v5ids,cwd=outside)
    assert rows()==before
    late=home/"late.json"
    run(active/"venv/bin/python","-I","-c",check+"""
p=c.projects.create_project(o['owner'],'Post reinstall v5 write')
j=c.jobs.create_job(o['owner'],p.id,'later')
done=run_once(c.jobs);assert done.id==j.id and done.state=='succeeded'
Path(sys.argv[5]).write_text(json.dumps({'project':p.id,'job':j.id,'result':done.result}))
""",cfg,v4ids,oidcids,v5ids,late,cwd=outside)
    later=rows()
    check+="""
l=json.loads(Path(sys.argv[5]).read_text())
assert c.projects.get_project(o['owner'],l['project']).name=='Post reinstall v5 write'
assert c.jobs.get_job(o['owner'],l['job']).result==l['result']
print('HISTORICAL_AND_V5_RECEIPTS_AND_LATE_WRITE=PASS')
"""
    for slot in (a,b):
        activate(slot);run(active/"venv/bin/python","-I","-c",check,cfg,v4ids,oidcids,v5ids,late,cwd=outside)
        assert rows()==later and db.stat().st_ino==inode and sha(cfg)==cfghash
    try:activate(oldslot)
    except ValueError as e:assert str(e)=="schema mismatch"
    else:raise AssertionError("downgrade accepted")
    run(oldslot/"venv/bin/python","-I","-c","from pathlib import Path\nimport sys\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.application.ports import StorageError\ntry: build_core(load_config(Path(sys.argv[1])))\nexcept StorageError as e:\n assert 'unsupported database schema version' in str(e),str(e)\n print('V4_PACKAGE_REJECTS_V5=PASS')\nelse: raise AssertionError('v4 accepted v5')",cfg,cwd=outside)
    assert active.resolve()==b and rows()==later and sha(published)==expected_mp4
    print("V5_REINSTALL_SLOT_RECOVERY_ANTI_DOWNGRADE=PASS",flush=True)
v5=json.loads(v5ids.read_text())
shutil.copyfile(artifacts/(v5["artifact"]+".mp4"),out/"published-v5-fixture.mp4")
assert sha(out/"published-v5-fixture.mp4")==v5["sha256"]
shutil.copyfile(wa,out/wa.name)
save("manifest.json",{"format":1,"core":ma,"second_build_sha256":mb["sha256"],"normalized_equal":True,"byte_equal":True,"historical_v4":mv4,"historical_v4_mp4_sha256":expected_mp4,"sources":{"renderer":jm,"engine":engine},"python":"3.12.12","uv":"0.9.18","sqlite":run(py,"-c","import sqlite3;print(sqlite3.sqlite_version)",capture=True).strip(),"pip":run(build,"-m","pip","--version",capture=True).split()[1],"platform":"Ubuntu 24.04.5 LTS Linux x86_64","locks":{"python":sha(out/"python-lock.json"),"ffmpeg":sha(out/"ffmpeg-lock.json")},"historical_python_wheels_and_ffmpeg_lock_replayed":True,"config_external":True,"storage_schema":5,"stage_profile":"stage-engineering-v0","redistribution":"upstream and binaries obtained separately; no redistribution authorized","validation":{"installed_suite":90,"actual_v4_wheel":True,"v4_state_reconstructed_with_archived_mp4_and_fixture_attestation":True,"v4_to_v5_real_migration":True,"historical_tables":len(historical),"historical_rows":sum(map(len,historical.values())),"all_historical_rows_preserved":True,"historical_receipt_preserved":True,"oidc_https_test_idp_on_migrated_state":True,"no_autolink":True,"new_http_write":True,"combined_real_engine_v5":True,"publication":True,"reopen":True,"reinstallation":True,"same_schema_slot_rollback":True,"later_write_preserved":True,"schema4_rejected":True,"distinct_schema5_release_rollback":"NOT RUN","google_tls_public_pilot":"NOT RUN"},"cgroup_peak_bytes":cnum("memory.peak"),"limits":{"memory_max_bytes":cnum("memory.max"),"cpu_affinity":len(os.sched_getaffinity(0)),"pids_max":cnum("pids.max")},"state_sizes":{"db":db.stat().st_size,"originals":size(media),"workspaces":size(work),"published":size(artifacts)},"installed_slot_bytes":size(b),"ffmpeg_extracted_bytes":size(ffroot),"wheelhouse_bytes":size(wheelhouse),"free_disk_bytes":shutil.disk_usage(home).free})
print("H2_27_RESULT=PASS",flush=True)
PY
python3 "$HOME/release.py"
