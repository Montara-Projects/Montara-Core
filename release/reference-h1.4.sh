#!/bin/bash
set -euo pipefail
umask 007
export LC_ALL=C.UTF-8
export PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export UV_NO_CONFIG=1 UV_CACHE_DIR="$HOME/uv-cache" UV_PYTHON_INSTALL_DIR="$HOME/python" UV_PYTHON_BIN_DIR="$HOME/bin"
unset PYTHONPATH PYTHONHOME PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST
test "$(id -un)" = montara-test
cd "$HOME"
OUT="$MONTARA_OUTPUT/h2-19-2d5f2fc"
test ! -e "$OUT"
mkdir "$OUT"
export OUT
echo STAGE=TARGET
. /etc/os-release
test "$ID" = ubuntu
test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
echo "TARGET=$PRETTY_NAME x86_64"
python3 - <<'PY'
import os, shutil
from pathlib import Path
print("CPU_AFFINITY="+str(len(os.sched_getaffinity(0))))
for key in ("MemTotal:", "MemAvailable:"):
    line=next(x for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith(key))
    print(line)
print("DISK_FREE_GIB="+str(round(shutil.disk_usage(os.environ["HOME"]).free/2**30,2)))
cg=next(x.split(":",2)[-1] for x in Path("/proc/self/cgroup").read_text().splitlines() if x.startswith("0:"))
for name in ("memory.max","cpu.max","pids.max"):
    try: print("CGROUP_"+name.upper().replace(".","_")+"="+(Path("/sys/fs/cgroup")/cg.lstrip("/")/name).read_text().strip())
    except OSError: print("CGROUP_"+name.upper().replace(".","_")+"=NOT_READABLE")
for tool in ("ffmpeg","ffprobe"):
    print(tool.upper()+"="+("AVAILABLE" if shutil.which(tool) else "NOT_FOUND"))
assert shutil.disk_usage(os.environ["HOME"]).free >= 15*2**30
PY
echo STAGE=SOURCE_AND_TOOLCHAIN
git -c credential.helper= clone --quiet --no-checkout https://github.com/Montara-Projects/Montara-Core.git repository
python3 -m venv "$HOME/uv-bootstrap"
"$HOME/uv-bootstrap/bin/python" -m pip install --quiet --only-binary=:all: uv==0.9.18
UV="$HOME/uv-bootstrap/bin/uv"
"$UV" python install 3.11.14
PY="$("$UV" python find --managed-python 3.11.14)"
export PY
"$PY" -c 'import sys; assert sys.version_info[:3] == (3,11,14); print("PYTHON="+sys.version.split()[0])'
"$PY" -m venv "$HOME/build-env"
BUILD="$HOME/build-env/bin/python"
export BUILD
"$BUILD" -m pip install --quiet --only-binary=:all: setuptools==75.8.2 wheel==0.45.1
"$BUILD" - <<'PY'
from importlib.metadata import version
for n,v in (("setuptools","75.8.2"),("wheel","0.45.1")):
    assert version(n)==v
    print(n.upper()+"="+v)
PY
"$PY" - <<'PY'
import hashlib, json, os, pathlib, shutil, sqlite3, subprocess, sys, tarfile, zipfile
from pathlib import Path
from datetime import datetime, timezone

root=Path.home()
out=Path(os.environ["OUT"])
py=os.environ["PY"]
build=os.environ["BUILD"]
repo=root/"repository"
current="2d5f2fce87838317d717f235ff3675c553755304"
previous="329b34d1c4ace6cdf43624cd2d1f6ac1937f333d"
incompatible="c9746579acb2a623e340733f3b9b49ab3be8e247"
def run(*args,cwd=None, capture=False):
    return subprocess.run([str(x) for x in args],cwd=cwd,check=True,text=True,stdout=subprocess.PIPE if capture else None,stderr=subprocess.STDOUT if capture else None).stdout
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write_json(path,obj): path.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n")
def normalize(wheel):
    with zipfile.ZipFile(wheel) as z:
        names=z.namelist()
        assert len(names)==len(set(names))
        assert all(not n.startswith("/") and ".." not in Path(n).parts for n in names)
        return {n:hashlib.sha256(z.read(n)).hexdigest() for n in sorted(names) if not n.endswith("/")}
def make(label,commit,schema):
    src=root/("source-"+label)
    src.mkdir()
    assert run("git","rev-parse",commit+"^{commit}",cwd=repo,capture=True).strip()==commit
    tree=run("git","rev-parse",commit+"^{tree}",cwd=repo,capture=True).strip()
    epoch=run("git","show","-s","--format=%ct",commit,cwd=repo,capture=True).strip()
    archive=root/(label+".tar")
    subprocess.run(["git","archive","--format=tar","-o",str(archive),commit],cwd=repo,check=True)
    with tarfile.open(archive) as t: t.extractall(src,filter="data")
    archive.unlink()
    wheel_dir=root/("wheel-"+label)
    wheel_dir.mkdir()
    env=dict(os.environ,SOURCE_DATE_EPOCH=epoch,PYTHONHASHSEED="0")
    subprocess.run([build,"-m","pip","wheel","--quiet","--no-build-isolation","--no-index","--no-deps","--wheel-dir",str(wheel_dir),str(src)],cwd=root,env=env,check=True)
    files=list(wheel_dir.glob("*.whl"))
    assert len(files)==1
    wheel=files[0]
    norm=normalize(wheel)
    manifest={
        "format":1,"source":{"repository":"https://github.com/Montara-Projects/Montara-Core","commit":commit,"tree":tree,"source_date_epoch":int(epoch)},
        "package":{"name":"montara-core","version":"0.1.0","filename":wheel.name,"sha256":sha(wheel),"normalized_tree_sha256":hashlib.sha256(json.dumps(norm,sort_keys=True,separators=(",",":")).encode()).hexdigest(),"files":norm},
        "build":{"python":"3.11.14","uv":"0.9.18","setuptools":"75.8.2","wheel":"0.45.1","pip":run(build,"-m","pip","--version",capture=True).split()[1],"mode":"no-build-isolation/no-index/no-deps"},
        "runtime":{"python":"3.11.14","platform":"Linux x86_64","reference_os":"Ubuntu 24.04.5 LTS","sqlite":sqlite3.sqlite_version,"schema":schema,"runtime_dependencies":[]},
        "config":{"external":True,"storage":"absolute path outside release","http_bind":"127.0.0.1"},
        "engine":{"status":"not_included","candidate_revision":"9af275b18535005e36bd289a68ceb4a1d2f6c506","runtime_closure":"pending #18","redistribution":"not authorized/verified"}
    }
    print("BUILD_"+label.upper()+"=PASS "+commit+" "+manifest["package"]["sha256"],flush=True)
    return src,wheel,manifest

src_a,wa,ma=make("a",current,2)
src_b,wb,mb=make("b",current,2)
_,wp,mp=make("previous",previous,2)
_,wi,mi=make("incompatible",incompatible,1)
assert ma["package"]["files"]==mb["package"]["files"]
print("NORMALIZED_WHEEL_TREE=PASS files="+str(len(ma["package"]["files"])),flush=True)
print("WHEEL_BYTES_EQUAL="+str(sha(wa)==sha(wb)),flush=True)
# Reference layout: immutable release slots, external config/state, atomic current link.
install=root/"reference-install"
releases=install/"releases"
state=install/"state"
config_dir=install/"config"
outside=root/"outside"
for d in (releases,state,config_dir,outside): d.mkdir(parents=True)
db=state/"core.sqlite"
cfg=config_dir/"core.toml"
cfg.write_text("[http]\nport = 0\n[storage]\nsqlite_path = "+json.dumps(str(db))+"\n")
config_hash=sha(cfg)
active=install/"current"
def schema_version():
    if not db.exists(): return 0
    with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]
def logical():
    with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()==("ok",)
        assert c.execute("PRAGMA foreign_key_check").fetchall()==[]
        return hashlib.sha256("\n".join(c.iterdump()).encode()).hexdigest()
def installed_tree(slot):
    site=Path(run(slot/"venv/bin/python","-I","-c","import site; print(site.getsitepackages()[0])",capture=True).strip())
    return {str(p.relative_to(site)):sha(p) for d in (site/"montara_core",site/"montara_core-0.1.0.dist-info") for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name not in {"RECORD","direct_url.json"}}
def install_slot(label,wheel,manifest):
    if sha(wheel)!=manifest["package"]["sha256"]: raise ValueError("wheel checksum mismatch")
    schema=schema_version()
    if schema not in (0,manifest["runtime"]["schema"]): raise ValueError("incompatible live database schema")
    slot=releases/label
    assert not slot.exists()
    slot.mkdir()
    copy=slot/wheel.name
    shutil.copy2(wheel,copy)
    write_json(slot/"manifest.json",manifest)
    run(py,"-m","venv",slot/"venv")
    exe=slot/"venv/bin/python"
    run(exe,"-m","pip","install","--quiet","--no-index","--no-deps",copy)
    run(exe,"-m","pip","check")
    run(exe,"-I","-c","import pathlib,sys,montara_core; p=pathlib.Path(montara_core.__file__).resolve(); assert p.is_relative_to(pathlib.Path(sys.prefix).resolve()) and 'site-packages' in p.parts",cwd=outside)
    run(exe,"-I","-m","montara_core","--check-config","--config",cfg,cwd=outside)
    return slot
def activate(slot):
    manifest=json.loads((slot/"manifest.json").read_text())
    wheel=slot/manifest["package"]["filename"]
    if sha(wheel)!=manifest["package"]["sha256"]: raise ValueError("wheel checksum mismatch")
    if schema_version() not in (0,manifest["runtime"]["schema"]): raise ValueError("incompatible live database schema")
    # Maintenance window: no resident processes in this reference.
    tmp=install/"current.next"
    tmp.symlink_to(slot.relative_to(install),target_is_directory=True)
    tmp.replace(active)
def stage(code,data=None,slot=None):
    prefix="""import json,sys
from pathlib import Path
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.projects import build_project_service
from montara_core.bootstrap.jobs import build_job_service
from montara_core.application.synthetic_worker import run_once
cfg=load_config(Path(sys.argv[1]))
projects=build_project_service(cfg)
jobs=build_job_service(cfg)
data=json.loads(sys.argv[2])
"""
    exe=(slot or active)/"venv/bin/python"
    result=run(exe,"-I","-c",prefix+code,cfg,json.dumps(data or {}),cwd=outside,capture=True)
    return json.loads(result)
def write_record(name,owner=None):
    return stage("""owner=data.get('owner') or projects.provision_account().id
p=projects.create_project(owner,data['name'])
j=jobs.create_job(owner,p.id,data['name'])
done=run_once(jobs)
assert done.id==j.id and done.state=='succeeded'
print(json.dumps({'owner':owner,'project':p.id,'name':p.name,'job':j.id,'result':done.result}))
""",{"name":name,"owner":owner})
def check_records(records):
    got=stage("""for r in data['records']:
    assert projects.get_project(r['owner'],r['project']).name==r['name']
    j=jobs.get_job(r['owner'],r['job'])
    assert j.state=='succeeded' and j.result==r['result']
    assert jobs.create_job(r['owner'],r['project'],r['name'])==j
assert run_once(jobs) is None
print(json.dumps({'count':len(data['records'])}))
""",{"records":records})
    assert got["count"]==len(records)
    return got["count"]

old=install_slot("h13-"+previous[:7],wp,mp)
activate(old)
a=write_record("Before upgrade")
assert schema_version()==2
db_inode=db.stat().st_ino
snapshot=root/"pre-upgrade.sqlite"
with sqlite3.connect(db) as s,sqlite3.connect(snapshot) as d: s.backup(d)
snapshot_hash=sha(snapshot)
before=logical()
new=install_slot("h14-"+current[:7]+"-a",wa,ma)
activate(new)
assert logical()==before and check_records([a])==1
b=write_record("After upgrade",a["owner"])
print("UPGRADE_WRITE=PASS",flush=True)
before=logical()
reinstall=install_slot("h14-"+current[:7]+"-b",wb,mb)
assert installed_tree(new)==installed_tree(reinstall)
activate(reinstall)
assert logical()==before and check_records([a,b])==2
assert sha(cfg)==config_hash and db.stat().st_ino==db_inode
c=write_record("After reinstall",a["owner"])
print("CLEAN_REINSTALL_NO_DRIFT=PASS",flush=True)
before=logical()
activate(old)
assert logical()==before and check_records([a,b,c])==3
d=write_record("After rollback",a["owner"])
assert sha(snapshot)==snapshot_hash and db.stat().st_ino==db_inode
print("COMPATIBLE_ROLLBACK_LATER_WRITES=PASS records=3 then write=4",flush=True)
before=logical()
activate(reinstall)
assert logical()==before and check_records([a,b,c,d])==4
assert sha(cfg)==config_hash and db.stat().st_ino==db_inode
print("ROLL_FORWARD_LATER_WRITES=PASS records=4",flush=True)
before=logical()
link=active.readlink()
try: install_slot("reject-v1",wi,mi)
except ValueError as e: assert str(e)=="incompatible live database schema"
else: raise AssertionError("incompatible release accepted")
assert not (releases/"reject-v1").exists()
# Verify the real old package also rejects canonical v2, using a separate venv.
probe=root/"probe-v1"
run(py,"-m","venv",probe)
run(probe/"bin/python","-m","pip","install","--quiet","--no-index","--no-deps",wi)
run(probe/"bin/python","-I","-c", """from pathlib import Path
import sys
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.projects import build_project_service
from montara_core.application.ports import StorageError
try: build_project_service(load_config(Path(sys.argv[1])))
except StorageError as e: assert 'unsupported database schema version' in str(e)
else: raise AssertionError('v1 accepted v2 database')
""",cfg,cwd=outside)
assert active.readlink()==link and logical()==before and check_records([a,b,c,d])==4
print("INCOMPATIBLE_ROLLBACK_REJECTED=PASS",flush=True)
bad=json.loads(json.dumps(ma))
bad["package"]["sha256"]="0"*64
try: install_slot("reject-hash",wa,bad)
except ValueError as e: assert str(e)=="wheel checksum mismatch"
else: raise AssertionError("bad manifest accepted")
assert not (releases/"reject-hash").exists()
assert active.readlink()==link and logical()==before
print("BAD_WHEEL_HASH_REJECTED=PASS",flush=True)
# Original workflow from clean installed package, external working directory.
run(active/"venv/bin/python","-I","-m","unittest","discover","-s",src_a/"tests","-v",cwd=outside)
assert check_records([a,b,c,d])==4 and sha(cfg)==config_hash and db.stat().st_ino==db_inode
print("ORIGINAL_SUITE=PASS",flush=True)
# Preserve only portable release artifacts, never fixture database or absolute config.
for label,wheel,manifest in (("current",wa,ma),("rollback",wp,mp)):
    dst=out/label
    dst.mkdir(mode=0o770)
    shutil.copy2(wheel,dst/wheel.name)
    write_json(dst/"manifest.json",manifest)
write_json(out/"validation.json",{
    "source_commit":current,"previous_commit":previous,"incompatible_commit":incompatible,
    "normalized_tree_equal":True,"wheel_bytes_equal":sha(wa)==sha(wb),"second_wheel_sha256":sha(wb),
    "checks":{"clean_install":True,"external_config_unchanged":True,"reinstall_no_drift":True,"compatible_rollback":True,"roll_forward":True,"incompatible_rollback_rejected":True,"bad_hash_rejected":True,"original_suite":True},
    "later_records_preserved":4,"sqlite_integrity":"ok","foreign_key_check":"ok","schema":2,
    "limits":"reference in temporary workspace; no production deployment, no Engine included"
})
print("ARTIFACTS_SAVED=PASS",flush=True)
print("H2_19_RESULT=PASS",flush=True)
PY
