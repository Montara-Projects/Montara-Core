#!/bin/bash
set -euo pipefail
umask 007
export LC_ALL=C.UTF-8 PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export UV_NO_CONFIG=1 UV_CACHE_DIR="$HOME/uv-cache" UV_PYTHON_INSTALL_DIR="$HOME/python" UV_PYTHON_BIN_DIR="$HOME/bin"
unset PYTHONPATH PYTHONHOME PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST
test "$(id -un)" = montara-test
cd "$HOME"
OUT="$MONTARA_OUTPUT/h2-25-v4-reference"
if [ -d "$OUT" ] && [ ! -L "$OUT" ] && [ -O "$OUT" ]; then rmdir "$OUT"; fi
test ! -e "$OUT"
mkdir "$OUT"
# Evidence files shared with the authorized connector; scratch stays private.
export OUT
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
echo "TARGET=$PRETTY_NAME x86_64"
export CORE=40f5a8e768da7607bc5b9dd94ec299f009bd6861 PREV=ee471e14d222367c1292a0b7464a0513e26b1f23 JM=1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d ENGINE=9af275b18535005e36bd289a68ceb4a1d2f6c506
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
with phase("python_bootstrap"):
    boot=home/"uv-bootstrap";run(sys.executable,"-m","venv",boot)
    uvw=home/"uv-wheels";uvw.mkdir();pip(boot/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",uvw,"uv==0.9.18")
    pip(boot/"bin/python","install","--quiet","--no-index","--no-deps",*uvw.glob("*.whl"))
    uv=boot/"bin/uv";run(uv,"python","install","3.12.12")
    py=Path(run(uv,"python","find","--managed-python","3.12.12",capture=True).strip())
    assert run(py,"-c","import sys;print(sys.version.split()[0])",capture=True).strip()=="3.12.12"
with phase("ffmpeg_local_closure"):
    # Use APT's signed-index candidate closure, but only download and extract.
    # No apt install/update, dpkg database change or maintainer scripts.
    sim=run("apt-get","-s","--no-install-recommends","install","ffmpeg=7:6.1.1-3ubuntu5",capture=True)
    packages=[]
    for line in sim.splitlines():
        m=re.match(r"Inst (\S+)(?: \[[^\]]+\])? \((\S+)",line)
        if m: packages.append(m.group(1)+"="+m.group(2))
    assert any(x.startswith("ffmpeg=") for x in packages) and len(packages)<250
    debs=home/"debs";debs.mkdir()
    run("apt-get","-qq","download",*packages,cwd=debs)
    ffroot=home/"ffmpeg-root";ffroot.mkdir()
    deb_lock=[]
    for d in sorted(debs.glob("*.deb")):
        ident=run("dpkg-deb","-f",d,"Package","Version","Architecture",capture=True).strip()
        deb_lock.append({"filename":d.name,"metadata":ident,"sha256":sha(d),"size":d.stat().st_size})
        run("dpkg-deb","-x",d,ffroot)
    assert len(deb_lock)==len(packages),(len(deb_lock),len(packages))
    bindir=home/"media-tools";bindir.mkdir()
    lib=ffroot/"usr/lib/x86_64-linux-gnu"
    for name in ("ffmpeg","ffprobe"):
        real=ffroot/"usr/bin"/name
        assert real.is_file()
        w=bindir/name
        w.write_text('#!/bin/sh\nexport LD_LIBRARY_PATH="'+str(lib)+':'+str(ffroot/"lib/x86_64-linux-gnu")+'"\nexec "'+str(real)+'" "$@"\n')
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
        lddenv=dict(os.environ,LD_LIBRARY_PATH=str(lib)+":"+str(ffroot/"lib/x86_64-linux-gnu"))
        lines=run("ldd",ffroot/"usr/bin"/name,env=lddenv,capture=True).splitlines()
        assert not any("not found" in l for l in lines)
        for line in lines:
            match=re.search(r"(?:=> )?(/[^ ]+)",line)
            if match:
                p=Path(match.group(1))
                if p.is_file() and not p.is_relative_to(ffroot):
                    host_libs[str(p)]=sha(p)
    save("ffmpeg-lock.json",{"source":"Ubuntu 24.04 signed APT indices (download only)","requested":"ffmpeg=7:6.1.1-3ubuntu5","packages":packages,"debs":deb_lock,"host_library_sha256":host_libs,"ffmpeg":ffver,"ffprobe":fpver,"binary_sha256":{n:sha(ffroot/"usr/bin"/n) for n in ("ffmpeg","ffprobe")},"redistribute":False})
    print("FFMPEG="+ffver+" packages="+str(len(deb_lock)),flush=True)
with phase("python_wheel_closure"):
    wheelhouse=home/"wheelhouse";wheelhouse.mkdir()
    req=["pytest==9.2.0","numpy==1.26.4","opencv-python-headless==4.11.0.86","pillow==13.0.0","Flask==3.1.3","scipy==1.17.1","blinker==1.9.0","click==8.5.1","iniconfig==2.3.1","itsdangerous==2.2.0","jinja2==3.1.6","markupsafe==3.0.4","packaging==26.4","pluggy==1.7.0","pygments==2.21.0","werkzeug==3.1.9"]
    buildreq=["setuptools==75.8.2","wheel==0.45.1"]
    downloadenv=home/"download-env";run(py,"-m","venv",downloadenv)
    pip(downloadenv/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",wheelhouse,*req,*buildreq)
    wlocks=lock_wheels(wheelhouse)
    assert len(wlocks)==len(req)+len(buildreq)
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
    save("python-lock.json",{"python":"3.12.12","uv":lock_wheels(uvw),"runtime":req,"build":buildreq,"wheel_files":wlocks,"runtime_lock":runtime_lock.read_text(),"build_lock":build_lock.read_text()})
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
    wa,ma=make("a",core,4);wb,mb=make("b",core,4);wv3,mv3=make("old",prev,3)
    assert ma["files"]==mb["files"]
    print("DOUBLE_BUILD=PASS bytes_equal="+str(ma["sha256"]==mb["sha256"]),flush=True)
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
def logical():
    with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()==("ok",)
        assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
        return hashlib.sha256("\n".join(c.iterdump()).encode()).hexdigest()
def install_slot(name,w,manifest,guard=True):
    assert sha(w)==manifest["sha256"]
    if guard and version() not in (0,manifest["schema"]):raise ValueError("schema mismatch")
    slot=releases/name;slot.mkdir()
    run(py,"-m","venv",slot/"venv");exe=slot/"venv/bin/python"
    if manifest["schema"]==4:pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",runtime_lock)
    pip(exe,"install","--quiet","--no-index","--no-deps",w);pip(exe,"check")
    run(exe,"-I","-c","import pathlib,sys,montara_core; assert pathlib.Path(montara_core.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); print('INSTALLED_PACKAGE=PASS')",cwd=outside)
    if manifest["schema"]==4:run(exe,"-I","-m","montara_core","--check-config","--config",cfg,cwd=outside)
    (slot/"manifest.json").write_text(json.dumps(manifest))
    return slot
def activate(slot):
    m=json.loads((slot/"manifest.json").read_text())
    if version() not in (0,m["schema"]):raise ValueError("schema mismatch")
    p=install/"current.next";p.symlink_to(slot.relative_to(install));p.replace(active)
def tree(slot):
    site=Path(run(slot/"venv/bin/python","-I","-c","import site; print(site.getsitepackages()[0])",capture=True).strip())
    return {str(p.relative_to(site)):sha(p) for d in (site/"montara_core",site/"montara_core-0.1.0.dist-info") for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name not in {"RECORD","direct_url.json"}}
with phase("install_clean"):
    a=install_slot("schema4-a",wa,ma);activate(a)
    exe=active/"venv/bin/python"
    run(exe,"-I","-m","unittest","discover","-s",repo/"tests","-v",cwd=outside)
    print("ORIGINAL_SUITE=PASS",flush=True)
# One canonical positive fixture, no re-render of previously accredited negatives.
gate=home/"gate.py"
gate.write_text("\"\"\"Single canonical positive gate; adapted from tests/engine_h2_smoke.py at pinned Core.\"\"\"\nimport importlib.util,json,sys,shutil,subprocess,array,os,time,resource,threading\nfrom pathlib import Path\nfrom contextlib import contextmanager\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.domain.execution import Shot,ExecutionSpec,EngineCandidate\nfrom montara_core.domain.jobs import JobState\nfrom montara_core.domain.publication import ArtifactReceipt\nfrom montara_core.adapters.zucker_engine import ZuckerEngineAdapter,_stage_semantics\njm,engine,cfg,ids,out=map(lambda x:Path(x).resolve(),sys.argv[1:])\nconfig=load_config(cfg)\nbase=cfg.parent.parent/\"state\"\nbank=base/\"fixture-bank\"\nloader=importlib.util.spec_from_file_location(\"jm_fixture\",jm/\"tools/core_v2_media_fixture.py\")\nmedia=importlib.util.module_from_spec(loader);loader.loader.exec_module(media)\nmetrics=[]\ncg=Path(\"/sys/fs/cgroup\")/next(l.split(\":\",2)[-1].lstrip(\"/\") for l in Path(\"/proc/self/cgroup\").read_text().splitlines() if l.startswith(\"0:\"))\ndef mem():\n    try:return int((cg/\"memory.current\").read_text().strip())\n    except (OSError,ValueError):return None\ndef cpu():\n    a=resource.getrusage(resource.RUSAGE_SELF);b=resource.getrusage(resource.RUSAGE_CHILDREN)\n    return a.ru_utime+b.ru_utime,a.ru_stime+b.ru_stime\ndef size(root):\n    return sum(p.stat().st_size for p in root.rglob(\"*\") if p.is_file() and not p.is_symlink()) if root.exists() else 0\ndef rss_tree():\n    # Current process and descendants, including the runner's separate process group.\n    info={}\n    for p in Path(\"/proc\").iterdir():\n        if not p.name.isdecimal():continue\n        try:\n            s=(p/\"stat\").read_text().rsplit(\")\",1)[1].split()\n            info[int(p.name)]=(int(s[1]),int(s[21])*os.sysconf(\"SC_PAGE_SIZE\"))\n        except (OSError,ValueError,IndexError):pass\n    seen={os.getpid()}\n    while True:\n        more={pid for pid,(parent,rss) in info.items() if parent in seen}-seen\n        if not more:break\n        seen.update(more)\n    return sum(info[p][1] for p in seen if p in info)\n@contextmanager\ndef phase(name):\n    start=time.perf_counter();u,s=cpu();initial=size(base);stop=threading.Event();peaks=[mem(),rss_tree(),initial]\n    def sample():\n        while not stop.wait(.2):\n            m=mem()\n            if m is not None:peaks[0]=max(peaks[0] or 0,m)\n            peaks[1]=max(peaks[1],rss_tree())\n            try:peaks[2]=max(peaks[2],size(base))\n            except OSError:pass\n    t=threading.Thread(target=sample,daemon=True);t.start();ok=False\n    try:\n        yield;ok=True\n    finally:\n        stop.set();t.join();u2,s2=cpu();end=size(base);peaks[2]=max(peaks[2],end)\n        row=dict(phase=name,ok=ok,wall_s=round(time.perf_counter()-start,3),cpu_user_s=round(u2-u,3),cpu_system_s=round(s2-s,3),cgroup_sample_peak_bytes=peaks[0],process_tree_rss_sample_peak_bytes=peaks[1],state_before_bytes=initial,state_after_bytes=end,state_sample_peak_bytes=peaks[2])\n        metrics.append(row);p=out/\"metrics-av.json\";p.write_text(json.dumps(metrics,indent=2)+\"\\n\");pass\n        print(\"AV_PHASE=\"+json.dumps(row),flush=True)\nwith phase(\"fixture_generation\"):\n    media.generate(bank);original_manifest=media.verify_inputs(bank)\nwith phase(\"recipe_and_staging\"):\n    c=build_core(config);owner=c.projects.provision_account().id\n    project=c.projects.create_project(owner,\"Canonical fixture\").id\n    folder=config.media_root/project;folder.mkdir()\n    for name in (\"camera-a.mp4\",\"camera-b.mp4\",\"master.wav\"):shutil.copyfile(bank/name,folder/name)\n    _,a,_=c.media.register_original(owner,project,\"video\",\"camera-a.mp4\")\n    _,b,_=c.media.register_original(owner,project,\"video\",\"camera-b.mp4\")\n    _,m,_=c.media.register_original(owner,project,\"audio\",\"master.wav\")\n    recipe=c.media.save_recipe(owner,project,None,m.id,(Shot(a.id,0,2000),Shot(b.id,2000,4000),Shot(a.id,4000,6000)))\n    job=c.jobs.create_job(owner,project,\"export\",recipe.id);ref=c.jobs.claim()\n    assert ref.job_id==job.id\n    spec=c.media.prepare_execution(ref);assert isinstance(spec,ExecutionSpec)\n    assert {p.path for p in spec.inputs}==set(spec.workspace.iterdir())\n    originals={p.name:media.sha256(p) for p in folder.iterdir()}\n    staged={p.path:media.sha256(p.path) for p in spec.inputs}\nwith phase(\"engine_real\"):\n    adapter=ZuckerEngineAdapter(jm,engine)\n    candidate=adapter.execute(spec);assert isinstance(candidate,EngineCandidate)\n    assert c.jobs.get_job(owner,job.id).state==JobState.RUNNING\nwith phase(\"candidate_quality\"):\n    assert {p.name:media.sha256(p) for p in folder.iterdir()}==originals\n    assert all(media.sha256(p)==h for p,h in staged.items())\n    assert media.verify_inputs(bank)==original_manifest\n    att=json.loads((spec.workspace/\"engine\"/\"candidate-manifest.json\").read_text())\n    assert att[\"schema\"]==2 and att[\"snapshot_sha256\"]==spec.snapshot_sha256\n    assert att[\"stage_semantics\"]==_stage_semantics()\n    assert att[\"candidate\"]=={k:v for k,v in vars(candidate).items() if k!=\"path\"}\n    request=json.loads((spec.workspace/\"engine\"/\"request.json\").read_text())\n    assert set(request[\"source_files\"].values())|{request[\"master\"]}=={str(i.path) for i in spec.inputs}\n    assert not (spec.workspace/\"engine\"/\"inputs\").exists()\n    info=media.probe(candidate.path);assert abs(float(info[\"format\"][\"duration\"])-6)<.15\n    media.decode(candidate.path)\n    frames=[]\n    for at,color in [(1.5,\"red\"),(2.25,\"blue\"),(2.8,\"blue\"),(3.5,\"yellow\"),(4.5,\"green\")]:\n        actual,rgb=media.frame_color(candidate.path,at);assert actual==color,(at,actual,color)\n        frames.append({\"at\":at,\"color\":actual,\"rgb\":rgb})\n    tones=[]\n    for at in (1,3,5):\n        raw=media.execute([\"ffmpeg\",\"-v\",\"error\",\"-ss\",str(at),\"-i\",str(candidate.path),\"-t\",\"0.25\",\"-vn\",\"-ac\",\"1\",\"-ar\",str(media.RATE),\"-f\",\"s16le\",\"-\"])\n        samples=array.array(\"h\",raw)\n        if sys.byteorder!=\"little\":samples.byteswap()\n        assert len(samples)>=media.RATE//8\n        values={hz:media.tone_amplitude(samples,hz) for hz in (223,443,997)}\n        assert values[997]>=50 and values[997]>=10*max(values[223],values[443]),values\n        tones.append({\"at\":at,\"amplitudes\":values})\nwith phase(\"publication_recovery\"):\n    receipt=c.publication.recover(owner,ref)\n    assert isinstance(receipt,ArtifactReceipt) and receipt.path.parent==config.artifact_root\n    assert receipt.path!=candidate.path and receipt.sha256==candidate.sha256==media.sha256(receipt.path)\n    assert receipt.snapshot_sha256==spec.snapshot_sha256 and receipt.stage_profile==\"stage-engineering-v0\"\n    assert c.jobs.get_job(owner,job.id).state==JobState.SUCCEEDED\n    assert c.jobs.get_task(owner,job.id).state==JobState.SUCCEEDED\n    assert c.publication.publish(owner,ref,candidate)==receipt\nwith phase(\"reopen_separate_process\"):\n    code=\"\"\"import sys\nfrom pathlib import Path\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.domain.jobs import JobState\nc=build_core(load_config(Path(sys.argv[1])))\nr=c.publication.get_artifact(sys.argv[2],sys.argv[3])\nassert c.jobs.get_job(sys.argv[2],sys.argv[3]).state==JobState.SUCCEEDED\nassert r.sha256==sys.argv[4]\nprint('PUBLISHED_REOPEN=PASS')\n\"\"\"\n    assert \"PUBLISHED_REOPEN=PASS\" in subprocess.check_output([sys.executable,\"-I\",\"-c\",code,str(cfg),owner,job.id,receipt.sha256],cwd=cfg.parent,text=True,timeout=30)\n    media.decode(receipt.path)\n    assert media.sha256(receipt.path)==candidate.sha256\n    ids.write_text(json.dumps({\"owner\":owner,\"job\":job.id,\"sha256\":receipt.sha256,\"artifact\":receipt.path.stem}))\nreport={k:v for k,v in vars(candidate).items() if k!=\"path\"}\nreport.update(status=\"PASS\",duration_sec=float(info[\"format\"][\"duration\"]),frames=frames,tones=tones,resolution=\"1920x1080\",fps=30,video_codec=\"h264\",audio_codec=\"aac\",full_decode=\"PASS\",originals_unchanged=True,direct_staged_inputs=True,canonical_execution_spec=True,core_job_succeeded=True,publication=\"PASS\",published_reopen=\"PASS\",published_digest_verified=True,stage_semantics=att[\"stage_semantics\"])\np=out/\"quality.json\";p.write_text(json.dumps(report,indent=2)+\"\\n\");pass\nprint(\"CANONICAL_AV=PASS\",flush=True)\n")
ids=home/"ids.json"
with phase("canonical_av",state):
    run(exe,"-I",gate,renderer,eng,cfg,ids,out,cwd=outside)
assert version()==4
inode=db.stat().st_ino
before=logical()
stage="""import sys,json
from pathlib import Path
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
from montara_core.application.synthetic_worker import run_once
c=build_core(load_config(Path(sys.argv[1])))
d=json.loads(Path(sys.argv[2]).read_text())
r=c.publication.get_artifact(d['owner'],d['job'])
assert r.sha256==d['sha256'] and c.jobs.get_job(d['owner'],d['job']).state=='succeeded'
"""
def stage_run(code):
    return run(active/"venv/bin/python","-I","-c",stage+code,cfg,ids,cwd=outside,capture=True)
with phase("reinstall_and_recovery",state):
    b=install_slot("schema4-b",wb,mb)
    assert tree(a)==tree(b)
    assert logical()==before
    activate(b)
    stage_run("print('receipt preserved')")
    late=json.loads(stage_run("""p=c.projects.create_project(d['owner'],'Post reinstall write')
j=c.jobs.create_job(d['owner'],p.id,'later')
done=run_once(c.jobs)
assert done.id==j.id and done.state=='succeeded'
print(json.dumps({'project':p.id,'job':j.id,'result':done.result}))
"""))
    d=json.loads(ids.read_text());d["late"]=late;ids.write_text(json.dumps(d))
    verify="""l=d['late']
assert c.projects.get_project(d['owner'],l['project']).name=='Post reinstall write'
assert c.jobs.get_job(d['owner'],l['job']).result==l['result']
print('RECEIPT_AND_LATE_WRITE=PASS')
"""
    current=logical();activate(a);assert logical()==current
    assert "RECEIPT_AND_LATE_WRITE=PASS" in stage_run(verify)
    activate(b);assert logical()==current
    assert "RECEIPT_AND_LATE_WRITE=PASS" in stage_run(verify)
    assert db.stat().st_ino==inode and sha(cfg)==cfghash
    print("SAME_SCHEMA_SLOT_ROLLBACK=PASS",flush=True)
    try:install_slot("reject-v3",wv3,mv3)
    except ValueError:pass
    else:raise AssertionError("v3 installation accepted")
    assert not (releases/"reject-v3").exists()
    v3=install_slot("probe-v3",wv3,mv3,guard=False)
    try:activate(v3)
    except ValueError:pass
    else:raise AssertionError("v3 activation accepted")
    run(v3/"venv/bin/python","-I","-c","from pathlib import Path\nimport sys\nfrom montara_core.bootstrap.config import Config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.application.ports import StorageError\ntry: build_core(Config(sqlite_path=Path(sys.argv[1])))\nexcept StorageError as e:\n assert 'unsupported database schema version' in str(e),str(e)\n print('V3_REJECTED')\nelse: raise AssertionError('v3 accepted v4')",db,cwd=outside)
    assert active.resolve()==b and logical()==current and db.stat().st_ino==inode
    assert "RECEIPT_AND_LATE_WRITE=PASS" in stage_run(verify)
    print("INCOMPATIBLE_V3_ROLLBACK=REJECTED",flush=True)
quality=json.loads((out/"quality.json").read_text())
assert quality["status"]=="PASS"
published=artifacts/(d["artifact"]+".mp4")
assert sha(published)==d["sha256"]
shutil.copyfile(published,out/"published-fixture.mp4")
shutil.copyfile(wa,out/wa.name)
save("manifest.json",{"format":1,"core":ma,"second_build_sha256":mb["sha256"],"normalized_equal":ma["files"]==mb["files"],"byte_equal":ma["sha256"]==mb["sha256"],"previous_schema3":mv3,"sources":{"renderer":jm,"engine":engine},"python":"3.12.12","uv":"0.9.18","sqlite":run(py,"-c","import sqlite3; print(sqlite3.sqlite_version)",capture=True).strip(),"pip":run(build,"-m","pip","--version",capture=True).split()[1],"platform":"Ubuntu 24.04.5 LTS Linux x86_64","locks":{"python":sha(out/"python-lock.json"),"ffmpeg":sha(out/"ffmpeg-lock.json")},"config_external":True,"storage_schema":4,"stage_profile":"stage-engineering-v0","redistribution":"upstream and binaries obtained separately; no redistribution authorized","validation":{"installed_suite":82,"publication":True,"reopen":True,"reinstallation":True,"same_schema_slot_rollback":True,"later_write_preserved":True,"schema3_rejected":True,"v3_to_v4_release_migration":"NOT RUN","distinct_schema4_release_rollback":"NOT RUN"},"cgroup_peak_bytes":cnum("memory.peak"),"limits":{"memory_max_bytes":cnum("memory.max"),"cpu_affinity":len(os.sched_getaffinity(0)),"pids_max":cnum("pids.max")},"state_sizes":{"db":db.stat().st_size,"originals":size(media),"workspaces":size(work),"published":size(artifacts)},"installed_slot_bytes":size(b),"ffmpeg_extracted_bytes":size(ffroot),"wheelhouse_bytes":size(wheelhouse),"free_disk_bytes":shutil.disk_usage(home).free})
print("H2_25_RESULT=PASS",flush=True)
PY
python3 "$HOME/release.py"
