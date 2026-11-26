#!/bin/bash
# Add one or two verified subjects only; never run the initial installer again.
set +x
set -Eeuo pipefail
umask 077
export LC_ALL=C
test "$(id -u)" = 0
test "$#" = 2
S=$(realpath "$1")
test "$S" = "$1" && test "$(stat -c '%u:%a' "$S")" = 0:700
enrol_count=$2
[[ "$enrol_count" = 1 || "$enrol_count" = 2 ]]
test -t 0 && test -t 1
cd /
while read -r hash name; do
    test -f "$S/$name" && test ! -L "$S/$name"
    test "$(stat -c %u "$S/$name")" = 0
    test "$(git hash-object --no-filters "$S/$name")" = "$hash"
done <<'HASHES'
35819a26552e9b24a53fc13e402499ea5d3b2ec8 render-config.py
ab5d5fdad3f518088c8c6f94a30481b26d8a16b8 extend-admission.py
6714a88dbbf507e34f6e35e785231d3c832d8518 enrol-batch.sh
a6f137778ed181aaf7ea08238c4b94e64696ccd4 enrol.py
bb528fc454104970f88ac801a63dbab55495b3d5 montara-pilot-enrol.service
HASHES
base=/opt/montara-pilot
cfg=/etc/montara-pilot
ecfg=/etc/montara-pilot-enrol
work=/run/montara-pilot-enrol
state=/var/lib/montara-pilot
slot="$base/releases/c7e11118d2d1"
control="$base/control"
# Root-owned transaction directory survives failure. No start or retry while it exists.
T="$cfg/.admission-change"
candidate="$cfg/.core.toml.new"
for p in "$T" "$candidate" "$ecfg" "$work" "$control/enrol.py" /etc/systemd/system/montara-pilot-enrol.service; do
    test ! -e "$p" && test ! -L "$p"
done
for p in "$cfg" "$control" "$slot"; do
    test "$(realpath "$p")" = "$p" && test "$(stat -c %u "$p")" = 0
done
test "$(realpath "$base/current")" = "$slot"
for f in operator.toml allowed-subjects core.toml; do
    test -f "$cfg/$f" && test ! -L "$cfg/$f" && test "$(stat -c %u "$cfg/$f")" = 0
done
test "$(stat -c %a "$cfg/allowed-subjects")" = 600
test "$(stat -c %a "$cfg/operator.toml")" = 600
test "$(stat -c %a "$cfg/core.toml")" = 640
test "$(git hash-object --no-filters "$control/render-config.py")" = 35819a26552e9b24a53fc13e402499ea5d3b2ec8
test "$(systemctl show montara-pilot-enrol.service -p LoadState --value)" = not-found
test ! -e /etc/systemd/system/montara-pilot-enrol.service.d
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do id "$n" >/dev/null; done
# Validate existing admission and capacity before any shutdown. Do not print subjects.
python3 -I - "$S" "$cfg" "$enrol_count" <<'PY'
import importlib.util,sys,tomllib
from pathlib import Path
s,cfg=map(Path,sys.argv[1:3])
spec=importlib.util.spec_from_file_location("render",s/"render-config.py")
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
d=tomllib.loads(r.private_text(cfg/"operator.toml"))
assert d["identity"]["allowed_subjects_file"]==str(cfg/"allowed-subjects")
old,_,_=r.generate(d)
a=tomllib.loads(old)
assert a==tomllib.loads((cfg/"core.toml").read_text())
assert len(a["pilot"]["allowed_subjects"])+int(sys.argv[3])<=4
print("EXISTING_ADMISSION_AND_CAPACITY=PASS")
PY
confirm() {
    printf '%s\nEscribe %s para continuar: ' "$1" "$2" >/dev/tty
    read -r answer </dev/tty
    test "$answer" = "$2"
}
confirm "FER autoriza alta incremental. Alfred confirma cierre temporal del ingreso (sin cambiar Access). No iniciar ciclos worker ni comandos de recuperacion durante mantenimiento. Deja terminar cualquier trabajo activo; reconciliar intentos pendientes segun runbook, no reintentar." AMPLIAR_ADMISION
test "$(systemctl show montara-pilot-worker.service -p ActiveState --value)" = inactive
# Ensure the existing ACL is still active; never add/replace firewall rules here.
systemctl is-active --quiet montara-pilot-acl.service
nft list table inet montara_pilot_v2 >/dev/null
mkdir -m 0700 "$T"
done_ok=0
cleanup() {
    rc=$?
    trap - EXIT INT TERM HUP
    set +e
    if [ "$done_ok" != 1 ]; then
        # Preserve both original config files and any staged candidate. Never roll back data.
        stop_ok=1
        for unit in montara-pilot-ingress montara-pilot-enrol montara-pilot-http; do
            if [ "$(systemctl show "$unit.service" -p LoadState --value 2>/dev/null)" = not-found ]; then continue; fi
            systemctl stop "$unit.service" >/dev/null 2>&1 || stop_ok=0
            test "$(systemctl show "$unit.service" -p MainPID --value 2>/dev/null)" = 0 || stop_ok=0
            case "$(systemctl show "$unit.service" -p ActiveState --value 2>/dev/null)" in inactive|failed) ;; *) stop_ok=0 ;; esac
        done
        rm -f -- "$ecfg/client-secret" "$ecfg/arm.sha256"
        if [ "$stop_ok" = 1 ]; then echo ADMISSION_STOPPED_REVIEW_REQUIRED; else echo ADMISSION_STOP_UNVERIFIED_FAIL_CLOSE_EDGE; rc=1; fi
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
cp -p "$cfg/allowed-subjects" "$T/old-subjects"
cp -p "$cfg/core.toml" "$T/old-core.toml"
chmod 0600 "$T/old-subjects" "$T/old-core.toml"
cp "$T/old-subjects" "$T/subjects"
stop_quiet() {
    local unit="$1" cg active
    cg=$(systemctl show "$unit.service" -p ControlGroup --value)
    systemctl stop "$unit.service"
    test "$(systemctl show "$unit.service" -p MainPID --value)" = 0
    active=$(systemctl show "$unit.service" -p ActiveState --value)
    [[ "$active" = inactive || "$active" = failed ]]
    if [ -n "$cg" ]; then
        [[ "$cg" = /* ]]
        if test -e "/sys/fs/cgroup$cg/cgroup.events"; then
            grep -qx 'populated 0' "/sys/fs/cgroup$cg/cgroup.events"
        fi
    fi
}
# Edge first, then worker (already idle) and HTTP. No live configuration replacement.
stop_quiet montara-pilot-ingress
stop_quiet montara-pilot-worker
stop_quiet montara-pilot-http
listeners=$(ss -H -ltn)
if printf '%s\n' "$listeners" | awk '$4 ~ /:8080$/ {found=1} END {exit !found}'; then echo LISTENER_NOT_STOPPED; exit 1; fi
# Non-root read-only fingerprint of durable DB and published artifact inventory.
# Do not create a lock, open SQLite for writing or hash long original media.
snapshot() {
    runuser -u montara-pilot -- /usr/bin/python3 -I - "$state" <<'PY'
import fcntl,hashlib,os,stat,sys
from pathlib import Path
state=Path(sys.argv[1])
db=state/"db/core.sqlite"
lock=db.with_name(db.name+".worker.lock")
if lock.exists() or lock.is_symlink():
    fd=os.open(lock,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,"rb") as f:
        s=os.fstat(f.fileno())
        assert stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert os.stat(lock,follow_symlinks=False).st_ino==s.st_ino
assert db.is_file() and not db.is_symlink()
h=hashlib.sha256()
for p in (db,db.with_name(db.name+"-wal"),db.with_name(db.name+"-shm"),lock):
    if p.exists() or p.is_symlink():
        fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,"rb") as f:
            s=os.fstat(f.fileno());assert stat.S_ISREG(s.st_mode)
            digest=hashlib.file_digest(f,"sha256").hexdigest()
            h.update(repr((p.name,s.st_dev,s.st_ino,s.st_size,digest)).encode())
for root in (state/"db",state/"originals",state/"attempts",state/"artifacts"):
    assert root.is_dir() and root.resolve()==root
    s=root.stat();h.update(repr((root.name,s.st_dev,s.st_ino)).encode())
# File metadata inventory detects replacement/removal; not content hashing of all media.
for p in sorted((state/"artifacts").rglob("*")):
    s=p.lstat()
    assert stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)
    h.update(repr((str(p.relative_to(state)),s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)).encode())
print(h.hexdigest())
PY
}
snapshot > "$T/state-before"
echo WRITERS_QUIESCENT=PASS
# Recreate only the exact approved temporary enrolment service; no runtime or UID reinstall.
install -d -o root -g montara-pilot-enrol -m 0750 "$ecfg"
install -d -o montara-pilot-enrol -g montara-pilot-enrol -m 0700 "$work"
install -m 0440 -o root -g montara-pilot "$S/enrol.py" "$control/enrol.py"
install -m 0644 -o root -g root "$S/montara-pilot-enrol.service" /etc/systemd/system/montara-pilot-enrol.service
python3 -I - "$cfg" "$ecfg" "$T/origin" <<'PY'
import json,sys,tomllib
from pathlib import Path
cfg,ecfg,out=map(Path,sys.argv[1:])
d=tomllib.loads((cfg/"operator.toml").read_text())
ident=d["identity"]
settings={k:ident[k] for k in ("issuer","authorization_endpoint","token_endpoint","jwks_uri")}
settings.update(client_id=(cfg/"client-id").read_text().strip(),public_origin=d["public_origin"])
(ecfg/"oidc.json").write_text(json.dumps(settings))
out.write_text(d["public_origin"])
PY
install -o montara-pilot-enrol -g montara-pilot-enrol -m 0600 "$cfg/client-secret" "$ecfg/client-secret"
chown root:montara-pilot-enrol "$ecfg/oidc.json"; chmod 0640 "$ecfg/oidc.json"
systemctl daemon-reload
systemd-analyze verify montara-pilot-enrol.service
origin=$(<"$T/origin")
host=${origin#https://}
source "$S/enrol-batch.sh"
enrol_batch
stop_quiet montara-pilot-ingress
stop_quiet montara-pilot-enrol
listeners=$(ss -H -ltn)
if printf '%s\n' "$listeners" | awk '$4 ~ /:8080$/ {found=1} END {exit !found}'; then echo LISTENER_NOT_STOPPED; exit 1; fi
rm -f /etc/systemd/system/montara-pilot-enrol.service "$ecfg/oidc.json" "$ecfg/client-secret" "$ecfg/arm.sha256" "$control/enrol.py"
rmdir "$work" "$ecfg"
systemctl daemon-reload
test "$(systemctl show montara-pilot-enrol.service -p LoadState --value)" = not-found
python3 -I "$S/extend-admission.py" "$cfg/operator.toml" "$cfg/core.toml" "$T/subjects" "$T/new-core.toml"
test "$(wc -l < "$T/subjects")" -eq "$(( $(wc -l < "$T/old-subjects") + enrol_count ))"
# Candidate validation as the real service UID before publishing either file.
install -m 0640 -o root -g montara-pilot "$T/new-core.toml" "$candidate"
runuser -u montara-pilot -- env -i HOME="$state/runtime-home" PATH="$base/current/media-tools:/usr/bin:/bin" \
 "$base/current/pilot-runtime/venv/bin/python" -I -m montara_core --config "$candidate" --check-config
snapshot > "$T/state-after"
cmp -s "$T/state-before" "$T/state-after"
cmp -s "$cfg/allowed-subjects" "$T/old-subjects"
cmp -s "$cfg/core.toml" "$T/old-core.toml"
echo STATE_AND_EXISTING_ADMISSION_UNCHANGED=PASS
confirm "FER autoriza publicar exclusivamente la ampliacion verificada. Alfred confirma enrolador retirado, ingreso cerrado. No arranques servicios hasta completar ambos archivos." PUBLICAR_ADMISION
# Persist originals before either rename. Both are same-filesystem; no readers until both complete.
sync -f "$cfg"
# If interrupted between them, T remains and operator must reconcile from its originals.
mv -fT "$T/subjects" "$cfg/allowed-subjects"
mv -fT "$candidate" "$cfg/core.toml"
sync -f "$cfg"
cmp -s "$cfg/core.toml" "$T/new-core.toml"
runuser -u montara-pilot -- env -i HOME="$state/runtime-home" PATH="$base/current/media-tools:/usr/bin:/bin" \
 "$base/current/pilot-runtime/venv/bin/python" -I -m montara_core --config "$cfg/core.toml" --check-config
snapshot > "$T/state-after-publish"
cmp -s "$T/state-before" "$T/state-after-publish"
echo ADMISSION_CONFIG_AND_STATE=PASS
confirm "Alfred confirma Access intacto y binding exclusivo. FER autoriza reabrir Core; sesiones en memoria se invalidan y requieren login. Worker sigue parado." REABRIR_CORE
systemctl start montara-pilot-http.service
for i in $(seq 1 30); do
    if curl --noproxy '*' -fsS --max-time 2 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then break; fi
    sleep .2
done
test "$(ss -H -ltn | awk '$4 ~ /:8080$/ {print $4}')" = 127.0.0.1:8080
runuser -u montara-pilot-ingress -- curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null
if runuser -u nobody -- curl --noproxy '*' -sS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then echo ORIGIN_ACL=FAIL; exit 1; fi
systemctl start montara-pilot-ingress.service
confirm "Alfred confirma QUIC, health protegida y negativas. Verifica relogin de un admitido anterior con mismo UUID, proyectos/resultado existentes y acceso nuevo separado; no render adicional obligatorio." AMPLIACION_LISTA
systemctl is-active --quiet montara-pilot-http.service
systemctl is-active --quiet montara-pilot-ingress.service
test "$(systemctl show montara-pilot-worker.service -p ActiveState --value)" = inactive
# Only after successful reopen; remove private staging, never state.
rm -rf -- "$T"
done_ok=1
echo INCREMENTAL_ADMISSION=PASS
