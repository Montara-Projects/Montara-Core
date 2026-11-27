#!/bin/bash
# Shared audited ceremony; sourced only from hash-verified private operator staging.
# Caller provides confirm, T, cfg, ecfg, work, host and enrol_count (1 or 2).
enrol_batch() {
    [[ "$enrol_count" = 1 || "$enrol_count" = 2 ]]
# For each ceremony: create a fresh code, start a bounded listener, stop before reading result.
for ((n=1;n<=enrol_count;n++)); do
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
    printf 'Ceremonia %s de %s. Entrega este codigo solo al operador de la cuenta confirmada, por canal privado. Codigo: %s\n' "$n" "$enrol_count" "$code" >/dev/tty
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
if len(prior)>=4 or raw.rstrip(b"\n") in prior: raise SystemExit("SUBJECT_CAPACITY_OR_DUPLICATE")
with d.open("ab") as f: f.write(raw)
p.unlink()
print("VERIFIED_SUBJECT_ACCEPTED=PASS")
PY
done
}
