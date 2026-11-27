#!/bin/bash
# Continue only the specific PR17 failure before seal/credentials/ACL/units.
# Root operator only; no automatic activation. Keep prior partial slot and logs.
set +x
set -Eeuo pipefail
umask 077
export LC_ALL=C
test "$(id -u)" = 0
test "$#" = 1
S=$(realpath "$1")
test "$S" = "$1" && test "$(stat -c '%u:%a' "$S")" = 0:700
test -t 0 && test -t 1 && test -r /dev/tty
for x in git python3 nft ss curl systemctl systemd-analyze runuser sha256sum install; do command -v "$x" >/dev/null; done
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
test "$(git hash-object --no-filters "$S/resume-runtime.sh")" = 8b301272c8df27cd7a39773241e79d0a6c454fc9
test "$(git hash-object --no-filters "$S/ffmpeg-original-lock.json")" = f425eed4a28ef2c5bb60a042c0ffbfe9d11b59e6
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
test ! -e "$base/input/ffmpeg-cache" && test ! -L "$base/input/ffmpeg-cache"
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
    cmp -s "$control/$n" "$S/$n"
done
cmp -s "$cfg/gitconfig" "$S/gitconfig"
test "$(ls -A "$base/input")" = montara_core-0.1.0-py3-none-any.whl
test "$(ls -A "$base/ingress")" = cloudflared
test ! -L "$base/input/montara_core-0.1.0-py3-none-any.whl" && test ! -L "$base/ingress/cloudflared"
echo "c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f  $base/input/montara_core-0.1.0-py3-none-any.whl" | sha256sum -c --status
echo "56f13335143ec0e22772963290683a84a04114a35ec64cbc794a4d39ff9bb8cf  $base/ingress/cloudflared" | sha256sum -c --status
# No running processes from the three dedicated service UIDs at this stage.
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do
    if pgrep -u "$n" >/dev/null; then echo PARTIAL_UID_BUSY; exit 1; fi
done
echo PARTIAL_ROOT_PREFLIGHT=PASS
confirm() {
    printf '%s\nEscribe %s para continuar: ' "$1" "$2" >/dev/tty
    read -r answer </dev/tty
    test "$answer" = "$2"
}
confirm "FER autoriza continuar el estado parcial exacto, Alfred confirma edge inerte/tunel V2 sin conexion y preflight Google/Access. No reinstalar ni abrir origen." CONTINUAR
T=$(mktemp -d /root/montara-pilot-resume.XXXXXXXX)
done_ok=0
created=1
cleanup() {
    rc=$?
    trap - EXIT INT TERM HUP
    set +e
    if [ "$created" = 1 ] && [ "$done_ok" != 1 ]; then
        stop_ok=1
        for unit in montara-pilot-ingress montara-pilot-enrol montara-pilot-worker montara-pilot-http; do
            if [ "$(systemctl show "$unit.service" -p LoadState --value 2>/dev/null)" = not-found ]; then continue; fi
            systemctl stop "$unit.service" >/dev/null 2>&1 || stop_ok=0
            test "$(systemctl show "$unit.service" -p MainPID --value 2>/dev/null)" = 0 || stop_ok=0
            case "$(systemctl show "$unit.service" -p ActiveState --value 2>/dev/null)" in inactive|failed) ;; *) stop_ok=0 ;; esac
        done
        # Keep ACL and state; manual reviewed recovery if preparation partially completed.
        rm -f -- "$ecfg/client-secret" "$ecfg/arm.sha256"
        if [ "$stop_ok" = 1 ]; then echo PILOT_STOPPED_REVIEW_REQUIRED; else echo PILOT_STOP_UNVERIFIED_FAIL_CLOSE_EDGE; rc=1; fi
    fi
    rm -rf -- "$T"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
# Copy only the 148 named files as the authorized cache reader, verify before publishing.
install -d -o root -g montara-pilot -m 0750 "$base/input/ffmpeg-cache"
python3 -I - "$S/ffmpeg-original-lock.json" "$base/input/ffmpeg-cache" <<'PY'
import hashlib,json,os,re,subprocess,sys,grp
from pathlib import Path
lock,dest=map(Path,sys.argv[1:])
entries=json.loads(lock.read_text())["debs"]
assert len(entries)==148 and len({x["filename"] for x in entries})==148
cache=Path("/srv/montara-core/workspaces/artifacts/h2-10-ffmpeg-closure-original")
for x in entries:
    n=x["filename"]; assert re.fullmatch(r"[A-Za-z0-9.+_%~-]+\.deb",n)
    size=x["size"]; assert type(size) is int and 0<size<100_000_000
    raw=subprocess.check_output(["runuser","-u","sentinelx","--","head","-c",str(size+1),str(cache/n)])
    assert len(raw)==size and hashlib.sha256(raw).hexdigest()==x["sha256"]
    p=dest/n
    with p.open("xb") as f: f.write(raw)
    os.chown(p,0,grp.getgrnam("montara-pilot").gr_gid)
    p.chmod(0o440)
print("ROOT_CACHE_148_HASHES=PASS")
PY
# Install only the reviewed continuation script; original preparation remains intact.
install -m 0440 -o root -g montara-pilot "$S/resume-runtime.sh" "$control/resume-runtime.sh"
runuser -u montara-pilot -- env -i HOME="$slot" PATH=/usr/bin:/bin /bin/bash "$control/resume-runtime.sh"
echo PARTIAL_RUNTIME_CONTINUED=PASS
chown -hR root:montara-pilot "$slot"
chmod -R g+rX,g-w,o-rwx "$slot"
# External symlinks must be reviewed; allow only immutable system runtime locations.
python3 -I - "$slot" <<'PY'
import os,sys
from pathlib import Path
slot=Path(sys.argv[1])
for p in slot.rglob("*"):
    if not p.is_symlink(): continue
    target=p.resolve(strict=False)
    if target.is_relative_to(slot): continue
    if not any(target.is_relative_to(Path(x)) for x in ("/usr/lib","/usr/bin","/lib","/lib64")) or not target.exists():
        raise SystemExit("EXTERNAL_SYMLINK_REVIEW_REQUIRED")
    for q in (target,*target.parents):
        s=q.stat()
        if s.st_uid!=0 or s.st_mode&0o022: raise SystemExit("EXTERNAL_SYMLINK_REVIEW_REQUIRED")
print("EXTERNAL_SYMLINK_CHECK=PASS")
PY
runuser -u montara-pilot -- env -i HOME="$state/runtime-home" PATH="$base/current/media-tools:/usr/bin:/bin" \
 GIT_CONFIG_GLOBAL="$cfg/gitconfig" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=0 GIT_OPTIONAL_LOCKS=0 \
 /bin/bash "$control/check-sealed-git.sh" 1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d 9af275b18535005e36bd289a68ceb4a1d2f6c506
ln -s "releases/c7e11118d2d1" "$base/current"
# Input values never passed via argv/env or printed; entered locally by FER.
private_read() {
    printf '%s (entrada oculta): ' "$1" >/dev/tty
    IFS= read -rs value </dev/tty
    printf '\n' >/dev/tty
    test -n "$value"
    printf '%s\n' "$value" > "$2"
    unset value
}
private_read "Origen HTTPS exacto asignado por Alfred" "$T/origin"
private_read "Audience Access exacta" "$cfg/access-aud"
private_read "Client ID Google V2" "$cfg/client-id"
private_read "Client secret Google V2" "$cfg/client-secret"
private_read "Token del tunel exclusivo V2 (no comando)" "$icfg/token"
chown montara-pilot:montara-pilot "$cfg/client-secret"; chmod 0600 "$cfg/client-secret"
chown montara-pilot-ingress:montara-pilot-ingress "$icfg/token"; chmod 0600 "$icfg/token"
# Derive enrolment settings from the single private operator template; no subjects yet.
python3 -I - "$control/pilot.example.toml" "$T/origin" "$cfg" "$ecfg" <<'PY'
import json,re,sys,tomllib
from pathlib import Path
template,originfile,cfg,ecfg=map(Path,sys.argv[1:])
origin=originfile.read_text().strip()
if not re.fullmatch(r"https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev",origin) or origin=="https://montara.example.workers.dev": raise SystemExit("ORIGIN_INVALID")
text=template.read_text().replace('public_origin = "https://montara.example.workers.dev"','public_origin = '+json.dumps(origin))
d=tomllib.loads(text)
if d["public_origin"]!=origin: raise SystemExit("ORIGIN_INVALID")
aud=(cfg/"access-aud").read_text().strip()
client=(cfg/"client-id").read_text().strip()
if not re.fullmatch(r"[0-9a-f]{64}",aud) or not re.fullmatch(r"[A-Za-z0-9._-]+\.apps\.googleusercontent\.com",client): raise SystemExit("IDENTIFIER_INVALID")
(cfg/"operator.toml").write_text(text)
ident=d["identity"]
settings={k:ident[k] for k in ("issuer","authorization_endpoint","token_endpoint","jwks_uri")}
settings.update(client_id=client,public_origin=origin)
(ecfg/"oidc.json").write_text(json.dumps(settings))
PY
install -o montara-pilot-enrol -g montara-pilot-enrol -m 0600 "$cfg/client-secret" "$ecfg/client-secret"
chown root:montara-pilot-enrol "$ecfg/oidc.json"; chmod 0640 "$ecfg/oidc.json"
# ACL only this exclusive port and dedicated UID; no flush, no host/global firewall changes.
ingress_uid=$(id -u montara-pilot-ingress)
cat > "$cfg/acl.nft" <<EOF
table inet montara_pilot_v2 {
 chain local_output {
  type filter hook output priority -5; policy accept;
  oifname "lo" tcp dport 8080 meta skuid != { 0, $ingress_uid } reject
  oifname "lo" tcp dport 20246 meta skuid != 0 reject
 }
}
EOF
nft -c -f "$cfg/acl.nft"
for n in "${units[@]}"; do
    install -m 0644 -o root -g root "$S/$n.service" "/etc/systemd/system/$n.service"
done
install -d -m 0755 /etc/systemd/system/montara-pilot-http.service.d
cat > /etc/systemd/system/montara-pilot-http.service.d/acl.conf <<'EOF'
[Unit]
Requires=montara-pilot-acl.service
After=montara-pilot-acl.service
BindsTo=montara-pilot-acl.service
Conflicts=montara-pilot-enrol.service
EOF
systemctl daemon-reload
systemd-analyze verify "${units[@]/%/.service}"
systemctl start montara-pilot-acl.service
nft list table inet montara_pilot_v2 >/dev/null
origin=$(<"$T/origin")
host=${origin#https://}
# Initially enrol only the two available accounts.
enrol_count=2
source "$S/enrol-batch.sh"
enrol_batch
# Close origin before switching. No overlap of listeners or enrolment after this point.
systemctl stop montara-pilot-ingress.service montara-pilot-enrol.service
test "$(systemctl show montara-pilot-enrol.service -p MainPID --value)" = 0
test "$(systemctl show montara-pilot-ingress.service -p MainPID --value)" = 0
listeners=$(ss -H -ltn)
if printf '%s\n' "$listeners" | awk '$4 ~ /:8080$/ {found=1} END {exit !found}'; then echo LISTENER_NOT_STOPPED; exit 1; fi
# Temporary service and copied secret removed; no Core DB was available to it.
rm -f /etc/systemd/system/montara-pilot-enrol.service "$ecfg/oidc.json" "$ecfg/client-secret" "$ecfg/arm.sha256" "$control/enrol.py"
rmdir "$work" "$ecfg"
systemctl daemon-reload
test "$(systemctl show montara-pilot-enrol.service -p LoadState --value)" = not-found
test "$(wc -l < "$T/subjects")" = 2
install -m 0600 -o root -g root "$T/subjects" "$cfg/allowed-subjects"
python3 -I "$control/render-config.py" "$cfg/operator.toml" "$T/generated"
install -m 0640 -o root -g montara-pilot "$T/generated/core.toml" "$cfg/core.toml"
runuser -u montara-pilot -- env -i HOME="$state/runtime-home" PATH="$base/current/media-tools:/usr/bin:/bin" \
 "$base/current/pilot-runtime/venv/bin/python" -I -m montara_core --config "$cfg/core.toml" --check-config
# Core and worker stay stopped until explicit final authorization.
test ! -e "$state/db/core.sqlite"
echo INITIAL_TWO_SUBJECTS_AND_ENROLMENT_REMOVED=PASS
confirm "Alfred confirma cierre del enrolamiento y edge protegido sin origen accesible. FER autoriza arrancar Core V2 y el conector, sin lanzar worker ni crear proyectos." ACTIVAR_CORE
nft list table inet montara_pilot_v2 >/dev/null
systemctl start montara-pilot-http.service
for i in $(seq 1 30); do
    if curl --noproxy '*' -fsS --max-time 2 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then break; fi
    sleep .2
done
curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null
# Confirm exactly one loopback listener, no public bind.
test "$(ss -H -ltn | awk '$4 ~ /:8080$/ {print $4}')" = 127.0.0.1:8080
runuser -u montara-pilot-ingress -- curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null
if runuser -u nobody -- curl --noproxy '*' -sS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then echo ORIGIN_ACL=FAIL; exit 1; fi
systemctl start montara-pilot-ingress.service
confirm "Alfred confirma de nuevo QUIC, binding exclusivo, negativas Access y health protegida. Acepta dejar piloto disponible para QA humana, sin worker automatico." PILOTO_LISTO
systemctl is-active --quiet montara-pilot-http.service
systemctl is-active --quiet montara-pilot-ingress.service
if systemctl is-active --quiet montara-pilot-worker.service; then echo WORKER_UNEXPECTEDLY_ACTIVE; exit 1; fi
done_ok=1
echo PILOT_INSTALLED_PENDING_HUMAN_QA=PASS
