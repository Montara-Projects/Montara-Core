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
test -f "$HOME/release.py" && test ! -L "$HOME/release.py"
test -d "$HOME/resume-evidence" && test ! -L "$HOME/resume-evidence"
test ! -e "$HOME/resume-evidence-2" && test ! -L "$HOME/resume-evidence-2"
test ! -e "$HOME/release-continue.py" && test ! -L "$HOME/release-continue.py"
cd "$HOME"
OUT="$HOME/resume-evidence-2"
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
cat > "$HOME/release-continue.py" <<'PY'
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
# The root read-only preflight already checked partial-state inventory and hashes.
# Recheck immutable pins and explicit host baseline under the actual service UID.
for name,pin in (("core-src",core),("renderer-src",jm),("engine-src",engine)):
    p=home/name
    assert p.resolve()==p
    assert run("git","rev-parse","HEAD",cwd=p,capture=True).strip()==pin
    assert not run("git","status","--porcelain",cwd=p,capture=True).strip()
repo=home/"core-src";renderer=home/"renderer-src";eng=home/"engine-src"
prior=repo/"release/manifests"
assert run("git","hash-object",prior/"h2.7-python-lock.json",capture=True).strip()=="81731b789adba706f2971e3260e56345f2b0381c"
oldpy=json.loads((prior/"h2.7-python-lock.json").read_text())
oldff=json.loads((prior/"h2.6-ffmpeg-lock.json").read_text())
assert [(x["phase"],x["ok"]) for x in json.loads((home/"resume-evidence/metrics-install.json").read_text())]==[("ffmpeg_local_closure",False)]
uv=home/"uv-bootstrap/bin/uv"
assert lock_wheels(home/"uv-wheels")==oldpy["uv"]
assert run(uv,"--version",capture=True).split()[1]=="0.9.18"
py=Path(run(uv,"python","find","--managed-python","3.12.12",capture=True).strip())
assert py.resolve().is_relative_to(home/"python")
assert run(py,"-I","-B","-c","import sys,ssl,sqlite3,ctypes; print(sys.version.split()[0])",capture=True).strip()=="3.12.12"
control=Path("/opt/montara-pilot/control")
with phase("host_baseline_and_local_relocation"):
    run(sys.executable,"-I","-B",control/"check-host-baseline.py",home,control/"host-baseline-noble-20261127.json","--normalize-x11")
    shutil.copyfile(control/"host-baseline-noble-20261127.json",out/"host-baseline.json")
    print("PREVIOUS_FFMPEG_EXTRACTION_REUSED=PASS",flush=True)
os.environ["PATH"]=str(home/"media-tools")+":"+os.environ["PATH"]
with phase("python_wheel_closure"):
    wheelhouse=home/"wheelhouse";wheelhouse.mkdir()
    oidc_req=["PyJWT==2.15.1","cryptography==50.0.2","cffi==2.1.1","pycparser==3.12"]
    req=oldpy["runtime"]
    buildreq=oldpy["build"]
    cache=Path("/opt/montara-pilot/input/python-cache")
    expected={x["filename"]:x for x in oldpy["wheel_files"]}
    assert len(expected)==22 and {p.name for p in cache.iterdir()}==set(expected)
    for p in cache.iterdir():
        x=expected[p.name]
        assert p.is_file() and not p.is_symlink() and p.stat().st_size==x["size"] and sha(p)==x["sha256"]
    for p in sorted(cache.iterdir()):
        shutil.copyfile(p,wheelhouse/p.name)
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
run(exe,"-I","-B","-c","import ssl,sqlite3,ctypes,numpy,scipy,cv2,PIL,cryptography,jwt,montara_core; print('PYTHON_NATIVE_IMPORTS=PASS')")
run(exe,"-I","-c","import pathlib,sys,montara_core,jwt,cryptography; from importlib.resources import files; import montara_core.api; assert pathlib.Path(montara_core.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); assert all(files(montara_core.api).joinpath('pilot.'+e).is_file() for e in ('html','css','js'))")
run(sys.executable,"-I","-B",control/"check-host-baseline.py",home,control/"host-baseline-noble-20261127.json","--normalize-x11")
print("PRESEAL_FULL_SYMLINK_AND_BASELINE=PASS",flush=True)
save("prepared.json",{"code_commit":prev,"release_source_commit":core,"wheel_sha256":sha(wheel),"python":"3.12.12","renderer":jm,"engine":engine,"schema":5,"historical_artifact_locks_verified":True,"host_baseline":"noble-20261127 USN-9202-1","scope":"runtime continuation only, no render or suite or activation"})
print("PILOT_RUNTIME_PREPARED=PASS",flush=True)
PY
python3 "$HOME/release-continue.py"
