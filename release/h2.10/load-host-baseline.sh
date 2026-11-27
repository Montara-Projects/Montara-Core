#!/bin/bash
# inspect is read-only for the pilot; continue requires separate QA/FER authorization.
set -Eeuo pipefail
umask 077
test "$(id -u)" = 0
test "$#" = 1
mode=$1
[[ "$mode" = inspect || "$mode" = continue ]]
D=$(mktemp -d /root/montara-baseline-reviewed.XXXXXXXX)
trap 'rm -rf -- "$D"' EXIT
W=/srv/montara-core/workspaces/efrain
for n in h2-10-initial-two-bundle.json h2-10-host-baseline-bundle.json; do
    runuser -u sentinelx -- head -c 65001 "$W/$n" > "$D/$n"
    test "$(wc -c < "$D/$n")" -le 65000
done
python3 -I - "$D" <<'PY'
import hashlib,json,sys
from pathlib import Path
d=Path(sys.argv[1])
sets={
"h2-10-initial-two-bundle.json":{"prepare-runtime.sh","render-config.py","pilot.example.toml","gitconfig","check-sealed-git.sh","montara-pilot-http.service","montara-pilot-worker.service","enrol.py","montara-pilot-enrol.service","montara-pilot-ingress.service","montara-pilot-acl.service","install-pilot.sh","enrol-batch.sh"},
"h2-10-host-baseline-bundle.json":{"check-host-baseline.py","host-baseline-noble-20261127.json","inspect-second-failure.sh","continue-runtime-after-host-drift.sh","continue-pilot-after-host-drift.sh"}}
for name,names in sets.items():
    o=json.loads((d/name).read_text())
    assert o["format"]==1 and len(o["files"])==len(names)
    assert {x["name"] for x in o["files"]}==names
    for x in o["files"]:
        b=x["content"].encode()
        assert hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()==x["sha"]
        with (d/x["name"]).open("xb") as f:f.write(b)
PY
test "$(git hash-object --no-filters "$D/install-pilot.sh")" = 91e64d5cb8aa58712efefec3a9346b5d267d0a52
test "$(git hash-object --no-filters "$D/check-host-baseline.py")" = 0638387e6ae965bbeebcc5ae48ed5de149036f51
test "$(git hash-object --no-filters "$D/host-baseline-noble-20261127.json")" = ba9b0e317cc7b609bd4a4eb2eb52eb61fd646b5a
test "$(git hash-object --no-filters "$D/inspect-second-failure.sh")" = a320917d1e4b08453337c4c556117c22481f3963
test "$(git hash-object --no-filters "$D/continue-runtime-after-host-drift.sh")" = 326f49c8d9c4a82f9fc419edc1bb8220c2ecf88a
test "$(git hash-object --no-filters "$D/continue-pilot-after-host-drift.sh")" = 3fca52b793283cc7802effe8ea755ea78c29b24a
echo REVIEWED_HOST_BASELINE_BUNDLE=PASS
if [ "$mode" = inspect ]; then
    /bin/bash "$D/inspect-second-failure.sh" "$D"
else
    /bin/bash "$D/continue-pilot-after-host-drift.sh" "$D" </dev/tty >/dev/tty 2>&1
fi
