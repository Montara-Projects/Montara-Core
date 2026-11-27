#!/bin/bash
# Read-only diagnostic of the exact second failure. No services, credentials or writes to pilot.
set -Eeuo pipefail
umask 077
export LC_ALL=C
test "$(id -u)" = 0
test "$#" = 1
S=$(realpath "$1")
test "$S" = "$1" && test "$(stat -c '%u:%a' "$S")" = 0:700
for x in git python3 nft ss systemctl runuser sha256sum pgrep; do command -v "$x" >/dev/null; done
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04 && test "$(uname -m)" = x86_64
cd /
while read -r hash name; do
    test -f "$S/$name" && test ! -L "$S/$name"
    test "$(stat -c %u "$S/$name")" = 0
    test "$(git hash-object --no-filters "$S/$name")" = "$hash"
done <<'HASHES'
fc7c01419c28a0c3ca15fa3eddf97435a18cca46 prepare-runtime.sh
f2603013903f4a05140489cb8d463577ab29eceb render-config.py
eb08b8987f6621e1bdf8028c75219c074323ce6b pilot.example.toml
d487d168af13dd310a77838f960eb2d4a149ca8e gitconfig
9625015cc8875c20bf21aa4a19ff69cbb083293c check-sealed-git.sh
d39310dd76febab0dfa371ee536c56832c9d53c1 montara-pilot-http.service
6d9df7806b9567aa5fef4467f6984ef69f26af30 montara-pilot-worker.service
a6f137778ed181aaf7ea08238c4b94e64696ccd4 enrol.py
bb528fc454104970f88ac801a63dbab55495b3d5 montara-pilot-enrol.service
ebad4fc19cc26a7243541b395c2366d0b46e1278 montara-pilot-ingress.service
1675a3a57a303ef78c514714e3081c628150c20d montara-pilot-acl.service
6714a88dbbf507e34f6e35e785231d3c832d8518 enrol-batch.sh
HASHES
base=/opt/montara-pilot
cfg=/etc/montara-pilot
ecfg=/etc/montara-pilot-enrol
icfg=/etc/montara-pilot-ingress
state=/var/lib/montara-pilot
work=/run/montara-pilot-enrol
slot="$base/releases/c7e11118d2d1"
control="$base/control"
units=(montara-pilot-http montara-pilot-worker montara-pilot-ingress montara-pilot-enrol montara-pilot-acl)
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do
    test "$(id -gn "$n")" = "$n"
    test "$(id -nG "$n")" = "$n"
    test "$(getent passwd "$n" | cut -d: -f7)" = /usr/sbin/nologin
done
for p in "$base" "$base/input" "$base/releases" "$control" "$base/ingress" "$cfg" "$ecfg" "$icfg" "$slot" "$state" "$work"; do
    test -d "$p" && test "$(realpath "$p")" = "$p"
done
for p in "$base" "$base/input" "$base/releases" "$control" "$base/ingress" "$cfg" "$ecfg" "$icfg"; do
    test "$(stat -c %u "$p")" = 0
    mode=$(stat -c %a "$p")
    (( (8#$mode & 022) == 0 ))
done
test "$(stat -c '%U:%G:%a' "$slot")" = montara-pilot:montara-pilot:700
test "$(stat -c '%U:%G:%a' "$work")" = montara-pilot-enrol:montara-pilot-enrol:700
test "$(stat -c '%U:%G:%a' "$state")" = montara-pilot:montara-pilot:700
test "$(find "$state" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort)" = "$(printf '%s\n' artifacts attempts db originals runtime-home)"
for p in "$state/db" "$state/originals" "$state/attempts" "$state/artifacts" "$state/runtime-home" "$work"; do
    test "$(realpath "$p")" = "$p"
    test -z "$(ls -A "$p")"
done
test "$(ls -A "$cfg")" = gitconfig
test -z "$(ls -A "$ecfg")" && test -z "$(ls -A "$icfg")"
test ! -e "$base/current" && test ! -L "$base/current"
test -d "$base/input/ffmpeg-cache" && test "$(realpath "$base/input/ffmpeg-cache")" = "$base/input/ffmpeg-cache"
test "$(stat -c '%U:%G:%a' "$base/input/ffmpeg-cache")" = root:montara-pilot:750
test ! -e "$base/input/python-cache" && test ! -L "$base/input/python-cache"
for n in "${units[@]}"; do
    test "$(systemctl show "$n.service" -p LoadState --value)" = not-found
    test ! -e "/etc/systemd/system/$n.service.d" && test ! -L "/etc/systemd/system/$n.service.d"
done
listeners=$(ss -H -ltn)
if printf '%s\n' "$listeners" | awk '$4 ~ /:(8080|20246)$/ {found=1} END {exit !found}'; then echo PORT_COLLISION; exit 1; fi
# nft query must succeed; an absent table is not confused with a failed query.
tables=$(nft list tables)
if printf '%s\n' "$tables" | grep -qx 'table inet montara_pilot_v2'; then echo ACL_COLLISION; exit 1; fi
for n in prepare-runtime.sh render-config.py pilot.example.toml check-sealed-git.sh enrol.py; do
    test -f "$control/$n" && test ! -L "$control/$n"
    test -f "$S/$n" && cmp -s "$control/$n" "$S/$n"
done
cmp -s "$cfg/gitconfig" "$S/gitconfig"
test "$(git hash-object --no-filters "$control/resume-runtime.sh")" = 8b301272c8df27cd7a39773241e79d0a6c454fc9
test "$(ls -A "$control" | sort)" = "$(printf '%s\n' check-sealed-git.sh enrol.py pilot.example.toml prepare-runtime.sh render-config.py resume-runtime.sh)"
test "$(ls -A "$base/input" | sort)" = "$(printf '%s\n' ffmpeg-cache montara_core-0.1.0-py3-none-any.whl)"
test "$(ls -A "$base/ingress")" = cloudflared
test ! -L "$base/input/montara_core-0.1.0-py3-none-any.whl" && test ! -L "$base/ingress/cloudflared"
echo "c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f  $base/input/montara_core-0.1.0-py3-none-any.whl" | sha256sum -c --status
echo "56f13335143ec0e22772963290683a84a04114a35ec64cbc794a4d39ff9bb8cf  $base/ingress/cloudflared" | sha256sum -c --status
# No running processes from the three dedicated service UIDs at this stage.
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do
    if pgrep -u "$n" >/dev/null; then echo PARTIAL_UID_BUSY; exit 1; else test "$?" = 1; fi
done
echo SECOND_PARTIAL_ROOT_PREFLIGHT=PASS
# Public reviewed helper copied to temporary root-owned read-only files,
# accessible to service UID; no changes to installation.
D=$(mktemp -d /tmp/montara-pilot-inspect.XXXXXXXX)
trap 'rm -rf -- "$D"' EXIT
chmod 0755 "$D"
test "$(git hash-object --no-filters "$S/check-host-baseline.py")" = 0638387e6ae965bbeebcc5ae48ed5de149036f51
test "$(git hash-object --no-filters "$S/host-baseline-noble-20261127.json")" = ba9b0e317cc7b609bd4a4eb2eb52eb61fd646b5a
install -m 0444 "$S/check-host-baseline.py" "$D/check-host-baseline.py"
install -m 0444 "$S/host-baseline-noble-20261127.json" "$D/host-baseline.json"
runuser -u montara-pilot -- env -i HOME="$slot" PATH=/usr/bin:/bin \
 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_OPTIONAL_LOCKS=0 PYTHONDONTWRITEBYTECODE=1 \
 /usr/bin/python3 -I -B - "$slot" "$base/input/ffmpeg-cache" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
slot,cache=map(Path,sys.argv[1:])
def sha(p):
    with p.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def run(*a):return subprocess.check_output([str(x) for x in a],text=True).strip()
expected={"core-src":"c75ef8f11c884c1e2d60d989ad694fcb15797f5a","renderer-src":"1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d","engine-src":"9af275b18535005e36bd289a68ceb4a1d2f6c506"}
for n,pin in expected.items():
    p=slot/n
    assert p.resolve()==p and run("git","-C",p,"rev-parse","HEAD")==pin
    assert not run("git","-C",p,"status","--porcelain")
allowed={"core-src","renderer-src","engine-src","uv-bootstrap","uv-wheels","python","bin","uv-cache","debs","debs.partial","ffmpeg-root","media-tools","prep-evidence","release.py","release-resume.py","resume-evidence"}
actual={p.name for p in slot.iterdir()}
print("SLOT_TOP_LEVEL="+json.dumps(sorted(actual)))
assert actual<=allowed
for n in ("debs","debs.partial","ffmpeg-root","media-tools","prep-evidence","resume-evidence"):
    p=slot/n
    assert p.is_dir() and p.resolve()==p
for n,expected_phases in (("prep-evidence",[("sources",True),("python_bootstrap",True),("ffmpeg_local_closure",False)]),("resume-evidence",[("ffmpeg_local_closure",False)])):
    metrics=json.loads((slot/n/"metrics-install.json").read_text())
    assert [(x["phase"],x["ok"]) for x in metrics]==expected_phases
fflock=slot/"core-src/release/manifests/h2.6-ffmpeg-lock.json"
pylock=slot/"core-src/release/manifests/h2.7-python-lock.json"
assert run("git","hash-object",fflock)=="f425eed4a28ef2c5bb60a042c0ffbfe9d11b59e6"
assert run("git","hash-object",pylock)=="81731b789adba706f2971e3260e56345f2b0381c"
oldff=json.loads(fflock.read_text())
oldpy=json.loads(pylock.read_text())
assert (slot/"ffmpeg-root/usr/share/X11/rgb.txt").is_symlink()
assert __import__("os").readlink(slot/"ffmpeg-root/usr/share/X11/rgb.txt")=="/etc/X11/rgb.txt"
for p in (slot/"debs",cache):
    assert p.resolve()==p
    assert {x.name for x in p.iterdir()}=={x["filename"] for x in oldff["debs"]}
    for x in oldff["debs"]:
        f=p/x["filename"]
        assert f.is_file() and not f.is_symlink() and f.stat().st_size==x["size"] and sha(f)==x["sha256"]
print("EXTRACTED_AND_ROOT_DEB_CACHE_148=PASS")
uvfiles=sorted((slot/"uv-wheels").glob("*.whl"))
assert [{"filename":p.name,"sha256":sha(p),"size":p.stat().st_size} for p in uvfiles]==oldpy["uv"]
assert run(slot/"uv-bootstrap/bin/uv","--version").split()[1]=="0.9.18"
py=[p for p in (slot/"python").glob("*/bin/python3.12") if p.is_file()]
assert len(py)==1 and py[0].resolve().is_relative_to(slot/"python")
assert run(py[0],"-I","-B","-c","import sys,ssl,sqlite3,ctypes; print(sys.version.split()[0])")=="3.12.12"
print("SOURCES_PYTHON_AND_PRIOR_PHASES=PASS")
PY
runuser -u montara-pilot -- env -i HOME="$slot" PATH=/usr/bin:/bin \
 /usr/bin/python3 -I -B "$D/check-host-baseline.py" "$slot" "$D/host-baseline.json"
echo SECOND_PARTIAL_READONLY=PASS
