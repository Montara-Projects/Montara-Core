#!/bin/bash
# Final activation only: no installation, enrolment, or worker execution.
set +x
set -Eeuo pipefail
umask 077
export LC_ALL=C
test "$(id -u)" = 0
test "$#" = 1
mode=$1
[[ "$mode" = inspect || "$mode" = activate ]]
cd /
base=/opt/montara-pilot
cfg=/etc/montara-pilot
icfg=/etc/montara-pilot-ingress
state=/var/lib/montara-pilot
slot="$base/releases/c7e11118d2d1"
http=montara-pilot-http.service
ingress=montara-pilot-ingress.service
worker=montara-pilot-worker.service
acl=montara-pilot-acl.service
enrol=montara-pilot-enrol.service
phase=initial
armed=0
completed=0
show_state() {
    local u
    for u in "$http" "$ingress" "$worker" "$acl" "$enrol"; do
        printf 'UNIT=%s\n' "$u"
        systemctl show "$u" -p LoadState -p ActiveState -p SubState -p Result -p MainPID \
          -p ExecMainCode -p ExecMainStatus -p ExecMainStartTimestampMonotonic -p NeedDaemonReload || :
    done
}
finish() {
    rc=$?
    trap - EXIT INT TERM HUP
    set +e
    if [ "$rc" != 0 ] || { [ "$armed" = 1 ] && [ "$completed" != 1 ]; }; then
        printf 'ACTIVATION_FAILED_PHASE=%s RC=%s\n' "$phase" "$rc"
        if [ "$armed" = 1 ]; then
            stop_ok=1
            for u in "$ingress" "$http"; do
                cg=$(systemctl show "$u" -p ControlGroup --value 2>/dev/null) || stop_ok=0
                systemctl stop "$u" >/dev/null 2>&1 || stop_ok=0
                test "$(systemctl show "$u" -p MainPID --value)" = 0 || stop_ok=0
                case "$(systemctl show "$u" -p ActiveState --value)" in inactive|failed) ;; *) stop_ok=0 ;; esac
                if [ -n "$cg" ] && [ -e "/sys/fs/cgroup$cg/cgroup.events" ]; then
                    grep -qx "populated 0" "/sys/fs/cgroup$cg/cgroup.events" || stop_ok=0
                fi
            done
            listeners=$(ss -H -ltn) || stop_ok=0
            if printf '%s\n' "$listeners" | awk '$4 ~ /:(8080|20246)$/ {found=1} END {exit !found}'; then stop_ok=0; fi
            if [ "$stop_ok" = 1 ]; then echo FINAL_ACTIVATION_STOPPED_REVIEW_REQUIRED
            else echo FINAL_STOP_UNVERIFIED_FAIL_CLOSE_EDGE; rc=1; fi
        fi
        show_state
        [ "$rc" != 0 ] || rc=1
    fi
    exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
mark() { phase=$1; printf 'PHASE=%s\n' "$phase"; }
hash_file() {
    test -f "$1" && test ! -L "$1" && test "$(stat -c %u "$1")" = 0
    test "$(git hash-object --no-filters "$1")" = "$2"
    local mode
    mode=$(stat -c %a "$1")
    (( (8#$mode & 022) == 0 ))
}
stopped() {
    test "$(systemctl show "$1" -p MainPID --value)" = 0
    case "$(systemctl show "$1" -p ActiveState --value)" in inactive|failed) ;; *) return 1 ;; esac
}
no_enrolment() {
    test "$(systemctl show "$enrol" -p LoadState --value)" = not-found
    for p in /etc/montara-pilot-enrol /run/montara-pilot-enrol "$base/control/enrol.py" /etc/systemd/system/"$enrol"; do
        test ! -e "$p" && test ! -L "$p"
    done
}
confirm() {
    printf '%s\nEscribe %s para continuar: ' "$1" "$2" >/dev/tty
    if ! read -r answer </dev/tty; then echo CONFIRM_INPUT_FAILED; return 1; fi
    if [ "$answer" != "$2" ]; then echo CONFIRM_LITERAL_MISMATCH; return 1; fi
    unset answer
    printf 'CONFIRM_%s=PASS\n' "$2"
}
mark observation
show_state
# Count only the journal records, never echo messages or credential-bearing logs.
for u in "$http" "$acl"; do
    if count=$(journalctl -b -u "$u" --no-pager -o json -q | wc -l); then
        printf 'CURRENT_BOOT_UNIT_JOURNAL_RECORDS %s %s\n' "$u" "$count"
    else printf 'JOURNAL_RECORD_COUNT_UNAVAILABLE %s\n' "$u"; fi
done
mark preserved_units
# Missing Conflicts target is intentional: no Requires=enrolment and no recreation.
for u in "$http" "$ingress" "$worker"; do stopped "$u"; done
no_enrolment
for u in "$http" "$ingress" "$worker" "$acl"; do
    test "$(systemctl show "$u" -p LoadState --value)" = loaded
    test "$(systemctl show "$u" -p FragmentPath --value)" = "/etc/systemd/system/$u"
    test "$(systemctl show "$u" -p NeedDaemonReload --value)" = no
    test "$(systemctl show "$u" -p UnitFileState --value)" = static
    if [ "$u" = "$http" ]; then
        test "$(systemctl show "$u" -p DropInPaths --value)" = /etc/systemd/system/montara-pilot-http.service.d/acl.conf
    else
        test -z "$(systemctl show "$u" -p DropInPaths --value)"
    fi
done
hash_file "/etc/systemd/system/$http" d39310dd76febab0dfa371ee536c56832c9d53c1
hash_file "/etc/systemd/system/$ingress" ebad4fc19cc26a7243541b395c2366d0b46e1278
hash_file "/etc/systemd/system/$worker" 6d9df7806b9567aa5fef4467f6984ef69f26af30
hash_file "/etc/systemd/system/$acl" 1675a3a57a303ef78c514714e3081c628150c20d
test ! -L /etc/systemd/system/montara-pilot-http.service.d/acl.conf
test "$(stat -c %u /etc/systemd/system/montara-pilot-http.service.d/acl.conf)" = 0
cmp -s /etc/systemd/system/montara-pilot-http.service.d/acl.conf <(printf '[Unit]\nRequires=montara-pilot-acl.service\nAfter=montara-pilot-acl.service\nBindsTo=montara-pilot-acl.service\nConflicts=montara-pilot-enrol.service\n')
jobs=$(systemctl list-jobs --no-legend --no-pager)
if printf '%s\n' "$jobs" | grep -q 'montara-pilot-'; then echo PENDING_UNIT_JOB; exit 1; fi
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do
    if pgrep -u "$n" >/dev/null; then echo DEDICATED_UID_BUSY; exit 1; else test "$?" = 1; fi
done
systemd-analyze verify "$http" "$ingress" "$worker" "$acl" >/dev/null
echo PRESERVED_UNITS=PASS
mark acl_preflight
systemctl is-active --quiet "$acl"
# Inspect the loaded rules, not just the existence of the table. Do not reapply it.
check_acl() {
python3 -I - "$cfg/acl.nft" "$(id -u montara-pilot-ingress)" <<'PY'
import json,subprocess,sys
from pathlib import Path
uid=int(sys.argv[2])
expected='''table inet montara_pilot_v2 {
 chain local_output {
  type filter hook output priority -5; policy accept;
  oifname "lo" tcp dport 8080 meta skuid != { 0, %d } reject
  oifname "lo" tcp dport 20246 meta skuid != 0 reject
 }
}
''' % uid
assert Path(sys.argv[1]).read_text()==expected
data=json.loads(subprocess.check_output(["nft","-j","-n","list","table","inet","montara_pilot_v2"]))["nftables"]
assert all(set(x) in ({"metainfo"},{"table"},{"chain"},{"rule"}) for x in data)
tables=[x["table"] for x in data if "table" in x]
chains=[x["chain"] for x in data if "chain" in x]
rules=[x["rule"] for x in data if "rule" in x]
assert len(tables)==len(chains)==1 and len(rules)==2
assert tables[0]["family"]=="inet" and tables[0]["name"]=="montara_pilot_v2"
c=chains[0]
assert (c["family"],c["table"],c["name"],c["type"],c["hook"],c["prio"],c["policy"])==("inet","montara_pilot_v2","local_output","filter","output",-5,"accept")
for rule,port in zip(rules,(8080,20246)):
    assert (rule["family"],rule["table"],rule["chain"])==("inet","montara_pilot_v2","local_output")
    expr=rule["expr"]
    assert len(expr)==4 and set(expr[-1])=={"reject"}
    matches=[x["match"] for x in expr[:3]]
    assert matches[0]=={"op":"==","left":{"meta":{"key":"oifname"}},"right":"lo"}
    assert matches[1]=={"op":"==","left":{"payload":{"protocol":"tcp","field":"dport"}},"right":port}
    m=matches[2]
    assert m["op"]=="!=" and m["left"]=={"meta":{"key":"skuid"}}
    if port==8080: assert set(m["right"]["set"])=={0,uid}
    else: assert m["right"]==0
print("LIVE_ACL_RULES=PASS")
PY
}
check_acl
listeners=$(ss -H -ltn)
if printf '%s\n' "$listeners" | awk '$4 ~ /:(8080|20246)$/ {found=1} END {exit !found}'; then echo PORT_COLLISION; exit 1; fi
echo ACL_AND_QUIESCENCE=PASS
mark preserved_config
test "$(realpath "$base/current")" = "$slot"
test -L "$base/current" && test "$(stat -c %u "$base/current")" = 0
test "$(stat -c '%U:%G:%a' "$slot")" = root:montara-pilot:750
test "$(stat -c '%U:%G:%a' "$cfg")" = root:montara-pilot:750
test "$(stat -c '%U:%G:%a' "$icfg")" = root:montara-pilot-ingress:750
for f in operator.toml client-id access-aud allowed-subjects; do
    test -f "$cfg/$f" && test ! -L "$cfg/$f"
    test "$(stat -c '%U:%a' "$cfg/$f")" = root:600
done
test "$(stat -c '%U:%G:%a' "$cfg/core.toml")" = root:montara-pilot:640
test "$(stat -c '%U:%G:%a' "$cfg/client-secret")" = montara-pilot:montara-pilot:600
test "$(stat -c '%U:%G:%a' "$icfg/token")" = montara-pilot-ingress:montara-pilot-ingress:600
for f in "$cfg/core.toml" "$cfg/client-secret" "$icfg/token" "$cfg/acl.nft"; do
    test -f "$f" && test ! -L "$f"
done
test -s "$icfg/token"
# Do not inspect or print token content. The actual tunnel readiness is Alfred’s gate.
hash_file "$base/control/render-config.py" f2603013903f4a05140489cb8d463577ab29eceb
hash_file "$base/control/pilot.example.toml" eb08b8987f6621e1bdf8028c75219c074323ce6b
echo "c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f  $base/input/montara_core-0.1.0-py3-none-any.whl" | sha256sum -c --status
echo "56f13335143ec0e22772963290683a84a04114a35ec64cbc794a4d39ff9bb8cf  $base/ingress/cloudflared" | sha256sum -c --status
# All values remain in local Python memory. Print only the approved origin's hostname
# into a shell variable (never the credentials/subjects or config).
host=$(python3 -I -B - "$cfg" "$base/control/render-config.py" <<'PY'
import importlib.util,sys,tomllib
from pathlib import Path
cfg,helper=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location("render_config",helper)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
try:
    d=tomllib.loads(m.private_text(cfg/"operator.toml"))
    template=tomllib.loads(helper.with_name("pilot.example.toml").read_text())
    template["public_origin"]=d["public_origin"]
    assert d==template
    assert d["identity"]["allowed_subjects_file"]==str(cfg/"allowed-subjects")
    core,_,checklist=m.generate(d)
    assert tomllib.loads(core)==tomllib.loads((cfg/"core.toml").read_text())
    assert len(tomllib.loads(core)["pilot"]["allowed_subjects"])==2
    print(checklist["host"])
except Exception:
    raise SystemExit("PRIVATE_CONFIG_OR_SUBJECTS_MISMATCH") from None
PY
)
echo TWO_SUBJECTS_AND_DECLARATIVE_CONFIG=PASS
fingerprint() {
    # Captured privately in shell; never printed or persisted.
    sha256sum "$cfg/allowed-subjects" "$cfg/operator.toml" "$cfg/core.toml" "$cfg/client-secret" "$cfg/client-id" "$cfg/access-aud" "$icfg/token"
}
before=$(fingerprint)
mark runtime_and_storage
exe="$base/current/pilot-runtime/venv/bin/python"
as_core() {
    runuser -u montara-pilot -- env -i HOME="$state/runtime-home" PATH="$base/current/media-tools:/usr/bin:/bin" "$exe" -I -B "$@"
}
as_core - "$cfg/core.toml" "$slot" <<'PY'
import sys,os
from pathlib import Path
import montara_core
from montara_core.bootstrap.config import load_config
from montara_core.adapters.oidc import OIDCProvider,read_client_secret
try:
    config=load_config(Path(sys.argv[1]))
    assert Path(montara_core.__file__).resolve().is_relative_to(Path(sys.argv[2])/"pilot-runtime")
    assert config.pilot and len(config.pilot.allowed_subjects)==2
    OIDCProvider(config.oidc,read_client_secret(config.oidc.client_secret_file))
    for p in (config.sqlite_path.parent,config.media_root,config.workspace_root,config.artifact_root,Path.home()):
        assert p.resolve()==p and p.is_dir() and p.stat().st_uid==os.getuid() and os.access(p,os.W_OK|os.X_OK)
except Exception:
    raise SystemExit("SERVICE_RUNTIME_SECRET_OR_STORAGE_INVALID") from None
print("SERVICE_RUNTIME_SECRET_AND_STORAGE=PASS")
PY
runuser -u montara-pilot-ingress -- test -r "$icfg/token"
runuser -u montara-pilot-ingress -- test ! -r "$cfg/core.toml"
runuser -u montara-pilot-ingress -- test ! -r "$cfg/client-secret"
runuser -u montara-pilot -- test ! -r "$icfg/token"
# This is a first boot, not a DB repair or migration procedure. Never delete data.
db="$state/db/core.sqlite"
for p in "$state/originals" "$state/attempts" "$state/artifacts"; do
    test -z "$(ls -A "$p")"
done
if [ -e "$db" ] || [ -L "$db" ]; then
    test -f "$db" && test ! -L "$db"
    test "$(stat -c '%U:%a' "$db")" = montara-pilot:600
    for p in "$db-wal" "$db-shm" "$db-journal"; do test ! -e "$p" && test ! -L "$p"; done
    as_core - "$db" <<'PY'
import sqlite3,sys
from pathlib import Path
p=Path(sys.argv[1])
with sqlite3.connect(p.as_uri()+"?mode=ro&immutable=1",uri=True) as db:
    assert db.execute("PRAGMA user_version").fetchone()==(5,)
    assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
    assert db.execute("PRAGMA foreign_key_check").fetchone() is None
    for n in ("accounts","oidc_identities","projects","jobs","attempts","artifact_publications"):
        assert db.execute("SELECT count(*) FROM "+n).fetchone()==(0,)
print("EXISTING_EMPTY_SCHEMA5_PRESERVED=PASS")
PY
    test "$(ls -A "$state/db")" = core.sqlite
else
    test -z "$(ls -A "$state/db")"
    echo DB_NOT_INITIALIZED=PASS
fi
test "$before" = "$(fingerprint)"
echo FINAL_ACTIVATION_PREFLIGHT=PASS
if [ "$mode" = inspect ]; then
    echo READONLY_ACTIVATION_INSPECT=PASS
    exit 0
fi
mark authorize
confirm "Alfred confirma Access y binding exactos, edge protegido, tunel cerrado. FER autoriza exclusivamente activar Core y conector, sin worker." ACTIVAR_CORE
armed=1
mark start_http
systemctl start "$http"
echo HTTP_START_REQUEST=PASS
mark wait_http
healthy=0
for i in $(seq 1 30); do
    if curl --noproxy '*' -fsS --max-time 2 -H "Host: $host" http://127.0.0.1:8080/health 2>/dev/null | python3 -I -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ok"}' 2>/dev/null; then healthy=1; break; fi
    sleep .2
done
test "$healthy" = 1
systemctl is-active --quiet "$http"
test "$(ss -H -ltn | awk '$4 ~ /:8080$/ {print $4}')" = 127.0.0.1:8080
echo CORE_HEALTH_LOOPBACK=PASS
mark origin_acl
runuser -u montara-pilot-ingress -- curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null
for uid in nobody montara-pilot montara-pilot-enrol; do
    if runuser -u "$uid" -- curl --noproxy '*' -sS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then echo ORIGIN_ACL=FAIL; exit 1; fi
done
test -f "$db" && test ! -L "$db"
echo CORE_ORIGIN_ACL=PASS
mark start_ingress
systemctl start "$ingress"
echo CONNECTOR_START_REQUEST=PASS
mark edge_confirmation
confirm "Alfred verifica QUIC, binding exclusivo, negativas Access y health protegida. No iniciar sesiones Core, crear proyectos ni ejecutar worker hasta cerrar este gate." PILOTO_LISTO
mark final_gate
systemctl is-active --quiet "$http"
systemctl is-active --quiet "$ingress"
systemctl is-active --quiet "$acl"
check_acl
test "$(ss -H -ltn | awk '$4 ~ /:8080$/ {print $4}')" = 127.0.0.1:8080
stopped "$worker"
no_enrolment
test "$before" = "$(fingerprint)"
# DB first startup is allowed; no enrolment/provisioning by this script.
completed=1
echo PILOT_INSTALLED_PENDING_HUMAN_QA=PASS
