#!/bin/bash
set -euo pipefail
umask 077
export LC_ALL=C.UTF-8 PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export UV_NO_CONFIG=1 UV_CACHE_DIR="$HOME/uv-cache" UV_PYTHON_INSTALL_DIR="$HOME/python" UV_PYTHON_BIN_DIR="$HOME/bin"
unset PYTHONPATH PYTHONHOME PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST
test "$(id -un)" = montara-pilot
# Fixed final path: never relocate venv, Python or FFmpeg wrappers.
test "$HOME" = /opt/montara-pilot/releases/c7e11118d2d1
test -d "$HOME" && test ! -L "$HOME"
test "$(realpath "$HOME")" = "$HOME"
test -z "$(ls -A "$HOME")"
cd "$HOME"
OUT="$HOME/prep-evidence"
if [ -d "$OUT" ] && [ ! -L "$OUT" ] && [ -O "$OUT" ]; then rmdir "$OUT"; fi
test ! -e "$OUT"
mkdir "$OUT"
# Evidence files shared with the authorized connector; scratch stays private.
export OUT
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
echo "TARGET=$PRETTY_NAME x86_64"
export CORE=c75ef8f11c884c1e2d60d989ad694fcb15797f5a PREV=4a7fb469d5a750e1384685695cabb24523ed78a9 JM=1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d ENGINE=9af275b18535005e36bd289a68ceb4a1d2f6c506
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
    p.mkdir()
    run("git","init","--quiet",p)
    run("git","remote","add","origin",url,cwd=p)
    run("git","-c","credential.helper=","fetch","--quiet","--depth=1","origin",rev,cwd=p)
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
oldpy=json.loads((prior/"h2.7-python-lock.json").read_text())
oldff=json.loads((prior/"h2.6-ffmpeg-lock.json").read_text())
oldmanifest=json.loads((prior/"h2.7.json").read_text())
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
    req=oldpy["runtime"]
    buildreq=oldpy["build"]
    downloadenv=home/"download-env";run(py,"-m","venv",downloadenv)
    pip(downloadenv/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",wheelhouse,*req,*buildreq)
    wlocks=lock_wheels(wheelhouse)
    assert len(wlocks)==len(req)+len(buildreq)
    assert wlocks==oldpy["wheel_files"],"historical Python wheel drift"
    print("PYTHON_V5_LOCK_REPLAY=PASS",flush=True)
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
    assert runtime_lock.read_text()==oldpy["runtime_lock"] and build_lock.read_text()==oldpy["build_lock"]
    oidclock=home/"oidc.lock";locked(oidc_req,oidclock)
    assert oidclock.read_text()==oldpy["oidc_lock"]
    shutil.copyfile(prior/"h2.7-python-lock.json",out/"python-lock.json")
# Use the exact wheel already built twice and archived in H2.9.
wheel=Path("/opt/montara-pilot/input/montara_core-0.1.0-py3-none-any.whl")
assert sha(wheel)=="c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f"
slot=home/"pilot-runtime";slot.mkdir()
run(py,"-m","venv",slot/"venv");exe=slot/"venv/bin/python"
pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",runtime_lock)
pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,str(wheel)+"[oidc]")
pip(exe,"check")
run(exe,"-I","-c","import pathlib,sys,montara_core,jwt,cryptography; from importlib.resources import files; import montara_core.api; assert pathlib.Path(montara_core.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); assert all(files(montara_core.api).joinpath('pilot.'+e).is_file() for e in ('html','css','js'))")
save("prepared.json",{"code_commit":prev,"release_source_commit":core,"wheel_sha256":sha(wheel),"python":"3.12.12","renderer":jm,"engine":engine,"schema":5,"historical_locks_verified":True,"scope":"runtime preparation only, no render or suite or activation"})
print("PILOT_RUNTIME_PREPARED=PASS",flush=True)
PY
python3 "$HOME/release.py"
