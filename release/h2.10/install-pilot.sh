#!/bin/bash
# One interactive, explicitly authorized operator session. NOT auto-deployed.
# Staging must be reviewed commit files in a root-owned private directory.
set +x
set -Eeuo pipefail
umask 077
export LC_ALL=C
test "$(id -u)" = 0
test "$#" = 1
S=$(realpath "$1")
test "$S" = "$1" && test -d "$S" && test ! -L "$S"
test "$(stat -c '%u:%a' "$S")" = 0:700
test -t 0 && test -t 1 && test -r /dev/tty
for x in git python3 nft ss curl systemctl systemd-analyze runuser useradd groupadd sha256sum install; do command -v "$x" >/dev/null; done
test -x /usr/sbin/nft
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04 && test "$(uname -m)" = x86_64
cd /
while read -r hash name; do
    test -f "$S/$name" && test ! -L "$S/$name"
    test "$(stat -c %u "$S/$name")" = 0
    test "$(git hash-object --no-filters "$S/$name")" = "$hash"
done <<'HASHES'
fc7c01419c28a0c3ca15fa3eddf97435a18cca46 prepare-runtime.sh
ded2bbd95e9d8fa9dc8491dcd5c1d539aeb539f6 render-config.py
eb08b8987f6621e1bdf8028c75219c074323ce6b pilot.example.toml
d487d168af13dd310a77838f960eb2d4a149ca8e gitconfig
9625015cc8875c20bf21aa4a19ff69cbb083293c check-sealed-git.sh
d39310dd76febab0dfa371ee536c56832c9d53c1 montara-pilot-http.service
6d9df7806b9567aa5fef4467f6984ef69f26af30 montara-pilot-worker.service
a6f137778ed181aaf7ea08238c4b94e64696ccd4 enrol.py
bb528fc454104970f88ac801a63dbab55495b3d5 montara-pilot-enrol.service
c9e2ae14d615d1b6a83651c5be0d12bd93ad2756 montara-pilot-ingress.service
1675a3a57a303ef78c514714e3081c628150c20d montara-pilot-acl.service
HASHES
# Abort on any previous installation; never overwrite data, accounts or units.
base=/opt/montara-pilot
cfg=/etc/montara-pilot
ecfg=/etc/montara-pilot-enrol
icfg=/etc/montara-pilot-ingress
state=/var/lib/montara-pilot
work=/run/montara-pilot-enrol
slot="$base/releases/c7e11118d2d1"
control="$base/control"
units=(montara-pilot-http montara-pilot-worker montara-pilot-ingress montara-pilot-enrol montara-pilot-acl)
for p in "$base" "$cfg" "$ecfg" "$icfg" "$state" "$work"; do test ! -e "$p" && test ! -L "$p"; done
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do
    ! getent passwd "$n" >/dev/null
    ! getent group "$n" >/dev/null
done
for n in "${units[@]}"; do
    test "$(systemctl show "$n.service" -p LoadState --value)" = not-found
    test ! -e "/etc/systemd/system/$n.service.d" && test ! -L "/etc/systemd/system/$n.service.d"
done
! ss -H -ltn | awk '$4 ~ /:(8080|20246)$/ {found=1} END {exit !found}'
! nft list table inet montara_pilot_v2 >/dev/null 2>&1
test "$(df -PB1 /opt | awk 'NR==2 {print $4}')" -ge 16106127360
echo PREFLIGHT_LOCAL=PASS
confirm() {
    printf '%s\nEscribe %s para continuar: ' "$1" "$2" >/dev/tty
    read -r answer </dev/tty
    test "$answer" = "$2"
}
confirm "Autorizacion FER: Access real positivo/negativo, callback Google aceptado, logs de solicitudes/callback desactivados, token exclusivo V2, UDP saliente 7844, sin recursos V1. No inicies si Alfred no lo ha confirmado. Este bloque creara cuentas, ACL y servicios V2." PREPARAR
T=$(mktemp -d /root/montara-pilot-stage.XXXXXXXX)
# No destructive automatic rollback. Stop exclusively newly installed V2 services on any exit before explicit success.
done_ok=0
created=0
cleanup() {
    rc=$?
    trap - EXIT INT TERM
    set +e
    if [ "$created" = 1 ] && [ "$done_ok" != 1 ]; then
        systemctl stop montara-pilot-ingress.service montara-pilot-enrol.service montara-pilot-worker.service montara-pilot-http.service >/dev/null 2>&1
        # Keep ACL and state; manual reviewed recovery if preparation partially completed.
        echo "PILOT_STOPPED_REVIEW_REQUIRED"
    fi
    rm -rf -- "$T"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Copy archived wheel as the authorized existing non-root reader, then hash before publishing.
runuser -u sentinelx -- cat /srv/montara-core/workspaces/artifacts/h2-30-pilot-reference/montara_core-0.1.0-py3-none-any.whl > "$T/wheel"
echo "c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f  $T/wheel" | sha256sum -c --status
curl --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --silent --show-error --location --max-time 180 \
 https://github.com/cloudflare/cloudflared/releases/download/2026.11.2/cloudflared-linux-amd64 -o "$T/cloudflared"
echo "56f13335143ec0e22772963290683a84a04114a35ec64cbc794a4d39ff9bb8cf  $T/cloudflared" | sha256sum -c --status
echo ARCHIVED_WHEEL_AND_CONNECTOR_HASH=PASS
for n in montara-pilot montara-pilot-enrol montara-pilot-ingress; do
    groupadd --system "$n"
    useradd --system --gid "$n" --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$n"
done
created=1
install -d -o root -g montara-pilot -m 0750 "$base" "$base/input" "$base/releases" "$control" "$cfg"
# Ingress has no Core group; install its binary under a separate root-owned searchable directory.
install -d -o root -g montara-pilot-ingress -m 0750 /opt/montara-pilot/ingress "$icfg"
# Base path searchable by ingress, not readable or writable by others.
chmod 0711 "$base"
install -d -o root -g montara-pilot-enrol -m 0750 "$ecfg"
install -d -o montara-pilot -g montara-pilot -m 0700 "$state" "$state/db" "$state/originals" "$state/attempts" "$state/artifacts" "$state/runtime-home"
install -d -o montara-pilot-enrol -g montara-pilot-enrol -m 0700 "$work"
install -d -o montara-pilot -g montara-pilot -m 0700 "$slot"
install -m 0440 -o root -g montara-pilot "$T/wheel" "$base/input/montara_core-0.1.0-py3-none-any.whl"
install -m 0550 -o root -g montara-pilot-ingress "$T/cloudflared" "$base/ingress/cloudflared"
for n in prepare-runtime.sh render-config.py pilot.example.toml check-sealed-git.sh enrol.py; do
    install -m 0440 -o root -g montara-pilot "$S/$n" "$control/$n"
done
install -m 0640 -o root -g montara-pilot "$S/gitconfig" "$cfg/gitconfig"
runuser -u montara-pilot-ingress -- "$base/ingress/cloudflared" --version | grep -Fq '2026.11.2'
runuser -u montara-pilot-ingress -- "$base/ingress/cloudflared" tunnel run --help > "$T/cf-help"
grep -q -- '--token-file' "$T/cf-help"
echo CONNECTOR_VERSION_AND_TOKEN_FILE=PASS
# No runtime build as root, no package manager installation or render.
runuser -u montara-pilot -- env -i HOME="$slot" PATH=/usr/bin:/bin /bin/bash "$control/prepare-runtime.sh"
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
# Isolate connector binary path from the Core group. Unit is rendered by the operator.
sed -i 's@/opt/montara-pilot/control/cloudflared@/opt/montara-pilot/ingress/cloudflared@' /etc/systemd/system/montara-pilot-ingress.service
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
# For each ceremony: create a fresh code, start a bounded listener, stop before reading result.
for n in 1 2 3 4; do
    systemctl stop montara-pilot-enrol.service
    test ! -e "$work/result" && test ! -L "$work/result"
    code=$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(32))')
    printf '%s' "$code" | sha256sum | awk '{print $1}' > "$ecfg/arm.sha256"
    chown root:montara-pilot-enrol "$ecfg/arm.sha256"; chmod 0640 "$ecfg/arm.sha256"
    systemctl start montara-pilot-enrol.service
    for i in $(seq 1 30); do
        if curl --noproxy '*' -fsS --max-time 2 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then break; fi
        sleep .2
    done
    test "$(curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health)" = ENROLMENT_READY
    test "$(runuser -u montara-pilot-ingress -- curl --noproxy '*' -fsS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health)" = ENROLMENT_READY
    for uid in nobody montara-pilot montara-pilot-enrol; do
        if runuser -u "$uid" -- curl --noproxy '*' -sS --max-time 3 -H "Host: $host" http://127.0.0.1:8080/health >/dev/null 2>&1; then echo ORIGIN_ACL=FAIL; exit 1; fi
    done
    echo ORIGIN_ACL=PASS
    if [ "$n" = 1 ]; then
        confirm "ACL local acreditada. Alfred confirma politica Access y VPC exactos, diagnostico sin logs sensibles; autoriza conectar exclusivamente el enrolador." CONECTAR_ENROL
        systemctl start montara-pilot-ingress.service
        confirm "Alfred verifica QUIC/estado del tunel y enlaza el edge aprobado al VPC fijo. Verifica anonimo/no autorizado sin acceso, Host/callback/cookies/no logs antes de introducir codigo." EDGE_ENROL_OK
    fi
    printf 'Ceremonia %s de 4. Entrega este codigo solo al operador de la cuenta confirmada, por canal privado. Codigo: %s\n' "$n" "$code" >/dev/tty
    unset code
    confirm "La persona autorizada ha iniciado Access y seleccionado la cuenta Google acordada; se ha confirmado privadamente el resultado de la ceremonia. No es una vinculacion por email." CUENTA_CONFIRMADA
    systemctl stop montara-pilot-enrol.service
    # Read bounded regular result without following symlinks or trusting filesystem names.
    python3 -I - "$work/result" "$T/subjects" <<'PY'
import os,pwd,re,stat,sys
from pathlib import Path
p,d=map(Path,sys.argv[1:])
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
with os.fdopen(fd,"rb") as f:
 s=os.fstat(f.fileno())
 if not stat.S_ISREG(s.st_mode) or s.st_uid!=pwd.getpwnam("montara-pilot-enrol").pw_uid or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1: raise SystemExit("RESULT_INVALID")
 raw=f.read(257)
if not re.fullmatch(rb"[\x21-\x7e]{1,255}\n",raw): raise SystemExit("RESULT_INVALID")
prior=d.read_bytes().splitlines() if d.exists() else []
if raw.rstrip(b"\n") in prior: raise SystemExit("DUPLICATE_SUBJECT")
with d.open("ab") as f: f.write(raw)
p.unlink()
print("VERIFIED_SUBJECT_ACCEPTED=PASS")
PY
done
# Close origin before switching. No overlap of listeners or enrolment after this point.
systemctl stop montara-pilot-ingress.service montara-pilot-enrol.service
test "$(systemctl show montara-pilot-enrol.service -p MainPID --value)" = 0
test "$(systemctl show montara-pilot-ingress.service -p MainPID --value)" = 0
! ss -H -ltn | awk '$4 ~ /:8080$/ {found=1} END {exit !found}'
# Temporary service and copied secret removed; no Core DB was available to it.
rm -f /etc/systemd/system/montara-pilot-enrol.service "$ecfg/oidc.json" "$ecfg/client-secret" "$ecfg/arm.sha256" "$control/enrol.py"
rmdir "$work" "$ecfg"
systemctl daemon-reload
test "$(systemctl show montara-pilot-enrol.service -p LoadState --value)" = not-found
install -m 0600 -o root -g root "$T/subjects" "$cfg/allowed-subjects"
python3 -I "$control/render-config.py" "$cfg/operator.toml" "$T/generated"
install -m 0640 -o root -g montara-pilot "$T/generated/core.toml" "$cfg/core.toml"
runuser -u montara-pilot -- env -i HOME="$state/runtime-home" PATH="$base/current/media-tools:/usr/bin:/bin" \
 "$base/current/pilot-runtime/venv/bin/python" -I -m montara_core --config "$cfg/core.toml" --check-config
# Core and worker stay stopped until explicit final authorization.
test ! -e "$state/db/core.sqlite"
echo FOUR_SUBJECTS_AND_ENROLMENT_REMOVED=PASS
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
! systemctl is-active --quiet montara-pilot-worker.service
done_ok=1
echo PILOT_INSTALLED_PENDING_HUMAN_QA=PASS
