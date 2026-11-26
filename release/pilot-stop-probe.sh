#!/bin/bash
# Authorized operator only: isolated transient synthetic probe, not deployment.
set -Eeuo pipefail
test "$(id -u)" -eq 0
id montara-test >/dev/null
D=$(mktemp -d /run/montara-stop-probe.XXXXXXXX)
chmod 0755 "$D"
install -d -o montara-test -g montara-jobs -m 0700 "$D/state"
U="montara-stop-probe-$(basename "$D" | cut -d. -f2).service"
cleanup() {
    systemctl stop "$U" >/dev/null 2>&1 || true
    systemctl reset-failed "$U" >/dev/null 2>&1 || true
    rm -rf -- "$D"
}
trap cleanup EXIT
cat > "$D/probe.py" <<'PY'
import os,sys,signal,time,json,fcntl
from pathlib import Path
p=Path(sys.argv[1])
fd=os.open(p/"lock",os.O_CREAT|os.O_RDWR|os.O_EXCL,0o600)
fcntl.flock(fd,fcntl.LOCK_EX)
def identity():
    stat=Path("/proc/self/stat").read_text().rsplit(")",1)[1].split()
    return {"pid":os.getpid(),"start":stat[19],"session":os.getsid(0),"cg":Path("/proc/self/cgroup").read_text()}
child=os.fork()
if child==0:
    os.setsid()
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    (p/"child.json").write_text(json.dumps(identity()))
    while True:time.sleep(1)
(p/"parent.json").write_text(json.dumps(identity()))
while True:time.sleep(1)
PY
chmod 0644 "$D/probe.py"
systemd-run --quiet --unit="$U" --service-type=exec \
  -p User=montara-test -p Group=montara-jobs \
  -p KillMode=control-group -p SendSIGKILL=yes -p TimeoutStopSec=3s \
  -p RuntimeMaxSec=60s -p Restart=no -p MemoryMax=4G \
  -p CPUQuota=200% -p TasksMax=256 -p NoNewPrivileges=yes \
  /usr/bin/python3 "$D/probe.py" "$D/state"
for i in $(seq 1 50); do
  if test -s "$D/state/parent.json" && test -s "$D/state/child.json"; then break; fi
  sleep .1
done
test -s "$D/state/parent.json" && test -s "$D/state/child.json"
export D U
python3 - <<'PY'
import os,json,subprocess
from pathlib import Path
d=Path(os.environ["D"]);u=os.environ["U"]
p,c=[json.loads((d/"state"/(n+".json")).read_text()) for n in ("parent","child")]
def prop(k):return subprocess.check_output(["systemctl","show",u,"-p",k,"--value"],text=True).strip()
assert prop("KillMode")=="control-group" and prop("SendSIGKILL")=="yes"
assert p["cg"]==c["cg"] and p["session"]!=c["session"]
cg=Path("/sys/fs/cgroup")/prop("ControlGroup").lstrip("/")
assert cg.is_dir()
members={int(x) for x in (cg/"cgroup.procs").read_text().split()}
assert {p["pid"],c["pid"]}<=members
assert (cg/"memory.max").read_text().strip()=="4294967296"
assert (cg/"pids.max").read_text().strip()=="256"
quota,period=map(int,(cg/"cpu.max").read_text().split())
assert quota==2*period
(d/"cgroup").write_text(str(cg))
print("SEPARATE_SESSION_AND_CGROUP=PASS")
print("LIMITS=PASS")
PY
systemctl stop "$U"
python3 - <<'PY'
import os,json,fcntl,subprocess,time
from pathlib import Path
d=Path(os.environ["D"]);u=os.environ["U"]
cg=Path((d/"cgroup").read_text())
identities=[json.loads((d/"state"/(n+".json")).read_text()) for n in ("parent","child")]
def alive(i):
    try:s=Path("/proc/"+str(i["pid"])+"/stat").read_text().rsplit(")",1)[1].split()
    except FileNotFoundError:return False
    return s[19]==i["start"] and s[0]!="Z"
for _ in range(50):
    empty=not cg.exists() or (cg/"cgroup.events").read_text().splitlines().count("populated 0")==1
    if empty and not any(alive(i) for i in identities):break
    time.sleep(.1)
else:raise AssertionError("descendant remains or cgroup populated")
assert subprocess.run(["systemctl","is-active","--quiet",u]).returncode!=0
lock=d/"state"/"lock";ino=lock.stat().st_ino
with lock.open("rb") as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert lock.stat().st_ino==ino
print("CGROUP_EMPTY_AND_IDENTITIES_GONE=PASS")
print("FLOCK_FREE_SAME_INODE=PASS")
print("STOP_PROBE=PASS")
PY
