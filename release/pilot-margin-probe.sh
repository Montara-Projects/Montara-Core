#!/bin/bash
# One bounded operator-run probe; no permanent unit, no Google, no external listener.
set -Eeuo pipefail
test "$(id -u)" = 0
id montara-test >/dev/null
getent group montara-jobs >/dev/null
D=$(mktemp -d /var/tmp/montara-margin.XXXXXXXX)
chmod 0755 "$D"
tag=$(basename "$D" | cut -d. -f2)
P="montara-margin-prep-$tag.service"
W="montara-margin-worker-$tag.service"
cleanup() {
    systemctl stop "$W" "$P" >/dev/null 2>&1 || true
    systemctl reset-failed "$W" "$P" >/dev/null 2>&1 || true
    rm -rf -- "$D"
}
trap cleanup EXIT
install -d -o montara-test -g montara-jobs -m 0700 "$D/home"
install -d -m 0755 "$D/input"
# Copy the previously archived, verified wheel, not an untrusted executable as root.
install -m 0444 /srv/montara-core/workspaces/artifacts/h2-30-pilot-reference/montara_core-0.1.0-py3-none-any.whl "$D/input/montara_core-0.1.0-py3-none-any.whl"
echo "c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f  $D/input/montara_core-0.1.0-py3-none-any.whl" | sha256sum -c --status
echo ARCHIVED_WHEEL=PASS
cat > "$D/prep.sh" <<'MONTARA_PREP_EOF'
#!/bin/bash
set -euo pipefail
umask 077
export LC_ALL=C.UTF-8 PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export UV_NO_CONFIG=1 UV_CACHE_DIR="$HOME/uv-cache" UV_PYTHON_INSTALL_DIR="$HOME/python" UV_PYTHON_BIN_DIR="$HOME/bin"
unset PYTHONPATH PYTHONHOME PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST
test "$(id -un)" = montara-test
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
export CORE=4a7fb469d5a750e1384685695cabb24523ed78a9 PREV=36b010c172370a9c59762cf355e782adc00c3de0 JM=1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d ENGINE=9af275b18535005e36bd289a68ceb4a1d2f6c506
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
    buildenv=home/"build-env";run(py,"-m","venv",buildenv)
    build=buildenv/"bin/python"
    pip(build,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",build_lock)
    pip(build,"check")
# Use the exact wheel already built twice and archived in H2.9.
wheel=Path(os.environ["PILOT_ARCHIVED_WHEEL"])
assert sha(wheel)=="c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f"
slot=home/"pilot-runtime";slot.mkdir()
run(py,"-m","venv",slot/"venv");exe=slot/"venv/bin/python"
pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",runtime_lock)
pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,str(wheel)+"[oidc]")
pip(exe,"check")
state=home/"state";state.mkdir()
for name in ("originals","attempts","artifacts"):(state/name).mkdir()
cfg=home/"pilot.toml"
cfg.write_text("[http]\nport=0\n[storage]\n"+"\n".join(f"{k}={json.dumps(str(v))}" for k,v in {"sqlite_path":state/"core.db","media_root":state/"originals","workspace_root":state/"attempts","artifact_root":state/"artifacts"}.items())+"\n[oidc]\n"+ "\n".join(f"{k}={json.dumps(v)}" for k,v in {"issuer":"https://fixture.invalid","authorization_endpoint":"https://fixture.invalid/authorize","token_endpoint":"https://fixture.invalid/token","jwks_uri":"https://fixture.invalid/jwks","client_id":"fixture","client_secret_file":str(home/"absent-secret"),"public_origin":"https://pilot.invalid"}.items())+"\n[pilot]\nallowed_subjects=[\"margin-fixture\"]\n"+f"renderer_root={json.dumps(str(renderer))}\nengine_root={json.dumps(str(eng))}\nrender_timeout_sec=180\n")
seed="""import sys,json,shutil,importlib.util
from pathlib import Path
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import Shot
cfg,jm,ids=map(Path,sys.argv[1:])
config=load_config(cfg);c=build_core(config)
owner=c.identity.resolve_identity(config.oidc.issuer,"margin-fixture")
loader=importlib.util.spec_from_file_location("jm_fixture",jm/"tools/core_v2_media_fixture.py")
media=importlib.util.module_from_spec(loader);loader.loader.exec_module(media)
bank=cfg.parent/"bank";media.generate(bank);media.verify_inputs(bank)
p=c.projects.create_project(owner,"Margin fixture")
folder=config.media_root/p.id;folder.mkdir()
for n in ("camera-a.mp4","camera-b.mp4","master.wav"):shutil.copyfile(bank/n,folder/n)
_,a,_=c.media.register_original(owner,p.id,"video","camera-a.mp4")
_,b,_=c.media.register_original(owner,p.id,"video","camera-b.mp4")
_,m,_=c.media.register_original(owner,p.id,"audio","master.wav")
recipe=c.media.save_recipe(owner,p.id,None,m.id,(Shot(a.id,0,2000),Shot(b.id,2000,4000),Shot(a.id,4000,6000)))
job=c.jobs.create_job(owner,p.id,"margin-fixture",recipe.id)
ids.write_text(json.dumps({"owner":owner,"job":job.id}))
"""
run(exe,"-I","-c",seed,cfg,renderer,home/"ids.json")
run(exe,"-I","-m","montara_core","--config",cfg,"--check-config")
assert not (home/"absent-secret").exists()
print("PREPARED_EXACT_RUNTIME_AND_QUEUED_FIXTURE=PASS",flush=True)
PY
python3 "$HOME/release.py"
MONTARA_PREP_EOF
cat > "$D/measure.py" <<'MONTARA_MEASURE_EOF'
import os,sys,time,json,subprocess,hashlib,sqlite3
from pathlib import Path
home=Path.home()
cg=Path("/sys/fs/cgroup")/next(s.split(":",2)[2].lstrip("/") for s in Path("/proc/self/cgroup").read_text().splitlines() if s.startswith("0:"))
def val(k):return (cg/k).read_text().strip()
def fields(k):return {a:int(b) for a,b in (x.split() for x in val(k).splitlines())}
assert val("memory.max")=="4294967296" and val("pids.max")=="256"
quota,period=map(int,val("cpu.max").split());assert quota==2*period
initial=fields("memory.events");cpu0=fields("cpu.stat")
mem0=int(val("memory.current"));t=time.monotonic()
exe=home/"pilot-runtime/venv/bin/python";cfg=home/"pilot.toml"
cmd=[str(exe),"-I","-m","montara_core","--config",str(cfg),"--worker-once"]
env=dict(os.environ,PATH=str(home/"media-tools")+":/usr/bin:/bin")
p=subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
assert Path(f"/proc/{p.pid}/cgroup").read_text()==Path("/proc/self/cgroup").read_text()
stdout,stderr=p.communicate(timeout=500)
elapsed=time.monotonic()-t
peak=int(val("memory.peak"));events=fields("memory.events");cpu1=fields("cpu.stat")
delta={k:events[k]-initial.get(k,0) for k in events}
assert p.returncode==0,"worker exit nonzero"
assert json.loads(stdout)["worker"]=="published"
# Only after capturing peak/events, verify receipt via installed Core, outside worker timing.
check="""import json,sys,hashlib,sqlite3
from pathlib import Path
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
cfg,ids=map(Path,sys.argv[1:])
d=json.loads(ids.read_text());config=load_config(cfg);c=build_core(config)
r=c.publication.get_artifact(d["owner"],d["job"])
assert c.jobs.get_job(d["owner"],d["job"]).state=="succeeded"
assert r.sha256==hashlib.sha256(r.path.read_bytes()).hexdigest()
assert r.sha256=="e54dad8764762ef56c0e362dc27ac5f280fb29713a3f87bcf7647c5979e4830f"
with sqlite3.connect(config.sqlite_path) as db:
 assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
 assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
"""
subprocess.run([str(exe),"-I","-c",check,str(cfg),str(home/"ids.json")],env=env,check=True,timeout=30)
# Proposed conservative threshold only, not production capacity certification.
margin=4294967296-peak
ok=all(delta.get(k,0)==0 for k in ("max","oom","oom_kill","oom_group_kill")) and margin>=858993460
result={"worker_wall_s":round(elapsed,3),"cpu_usage_s":round((cpu1["usage_usec"]-cpu0["usage_usec"])/1e6,3),"cpu_user_s":round((cpu1["user_usec"]-cpu0["user_usec"])/1e6,3),"cpu_system_s":round((cpu1["system_usec"]-cpu0["system_usec"])/1e6,3),"cpu_throttled_s":round((cpu1["throttled_usec"]-cpu0["throttled_usec"])/1e6,3),"memory_start_bytes":mem0,"memory_peak_bytes":peak,"memory_limit_bytes":4294967296,"memory_margin_bytes":margin,"memory_events_delta":delta,"cpu_quota_cores":2,"tasks_max":256,"receipt_digest":"PASS","margin_20_percent":"PASS" if ok else "FAIL","qa_http_build_in_worker_cgroup":False}
print("WORKER_METRICS="+json.dumps(result,sort_keys=True),flush=True)
print("WORKER_LIMITS=PASS\nWORKER_PUBLICATION=PASS\nWORKER_MARGIN="+("PASS" if ok else "FAIL"),flush=True)
sys.exit(0 if ok else 4)
MONTARA_MEASURE_EOF
chmod 0444 "$D/prep.sh" "$D/measure.py"
# No overlap: wait for preparation unit to terminate before worker starts.
# Python, wheelhouse, FFmpeg and fixture are built under nonprivileged test UID.
echo "PREP_BEGIN"
if ! systemd-run --quiet --wait --pipe --collect --unit="$P" --service-type=exec \
    -p User=montara-test -p Group=montara-jobs -p NoNewPrivileges=yes \
    -p KillMode=control-group -p SendSIGKILL=yes -p TimeoutStopSec=10s \
    -p RuntimeMaxSec=900s -p Restart=no -p MemoryMax=4G -p TasksMax=256 \
    -E "HOME=$D/home" -E "PILOT_ARCHIVED_WHEEL=$D/input/montara_core-0.1.0-py3-none-any.whl" \
    /bin/bash "$D/prep.sh" > "$D/prep.log" 2>&1; then
    echo "PREP=FAIL"
    grep -E 'PHASE=|Traceback|AssertionError|RuntimeError|Error:' "$D/prep.log" | tail -8 || true
    exit 1
fi
grep -qx 'PREPARED_EXACT_RUNTIME_AND_QUEUED_FIXTURE=PASS' "$D/prep.log"
echo "PREP=PASS"
test "$(systemctl is-active "$P" || true)" != active
awk '/MemAvailable:/ {print "HOST_AVAILABLE_BEFORE_KIB=" $2}' /proc/meminfo
# A fresh dedicated cgroup for just the actual installed one-shot worker.
if systemd-run --quiet --wait --pipe --collect --unit="$W" --service-type=exec \
    -p User=montara-test -p Group=montara-jobs -p NoNewPrivileges=yes \
    -p KillMode=control-group -p SendSIGKILL=yes -p TimeoutStopSec=10s \
    -p RuntimeMaxSec=600s -p Restart=no -p MemoryMax=4G -p CPUQuota=200% -p TasksMax=256 \
    -E "HOME=$D/home" /usr/bin/python3 -I "$D/measure.py" > "$D/worker.log" 2>&1; then
    rc=0
else
    rc=$?
fi
grep -E '^WORKER_(METRICS|LIMITS|PUBLICATION|MARGIN)=' "$D/worker.log" || true
awk '/MemAvailable:/ {print "HOST_AVAILABLE_AFTER_KIB=" $2}' /proc/meminfo
if test "$rc" = 0 && grep -qx 'WORKER_MARGIN=PASS' "$D/worker.log"; then
    echo "DEDICATED_MARGIN_PROBE=PASS"
else
    echo "DEDICATED_MARGIN_PROBE=FAIL"
    exit 1
fi
