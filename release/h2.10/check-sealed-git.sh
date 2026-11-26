#!/bin/bash
# Run as the worker UID with its effective Git environment, after sealing.
# Arguments are the approved renderer and Engine commit SHA-1 pins.
set -Eeuo pipefail
export LC_ALL=C
slot=/opt/montara-pilot/releases/c7e11118d2d1
cfg=/etc/montara-pilot/gitconfig
renderer="$slot/renderer-src"
engine="$slot/engine-src"
test "$(id -u)" -ne 0
test "$#" -eq 2
[[ "$1" =~ ^[0-9a-f]{40}$ && "$2" =~ ^[0-9a-f]{40}$ ]]
test "$GIT_CONFIG_GLOBAL" = "$cfg"
test "$GIT_CONFIG_NOSYSTEM" = 1
test "$GIT_CONFIG_COUNT" = 0
test "$GIT_OPTIONAL_LOCKS" = 0
test -z "${GIT_CONFIG_PARAMETERS+x}"
for p in /opt/montara-pilot /opt/montara-pilot/releases "$slot" /etc/montara-pilot "$cfg"; do
    test "$(realpath "$p")" = "$p"
    test "$(stat -c %u "$p")" = 0
    test ! -w "$p"
done
test "$(git config --global --get-all safe.directory)" = "$(printf '%s\n%s' "$renderer" "$engine")"
echo GIT_TRUST_EXACT=PASS
check() {
    local p="$1" pin="$2" tmp dirty
    test "$(realpath "$p")" = "$p"
    test "$(stat -c %u "$p")" = 0
    test "$(stat -c %u "$p/.git")" = 0
    test "$(git -C "$p" rev-parse HEAD)" = "$pin"
    dirty=$(git -C "$p" status --porcelain --untracked-files=no)
    test -z "$dirty"
    for d in "$p" "$p/.git"; do
        if tmp=$(mktemp "$d/.montara-write-check.XXXXXXXX" 2>/dev/null); then
            rm -f -- "$tmp"
            echo SLOT_WRITE=FAIL >&2
            exit 1
        fi
    done
}
check "$renderer" "$1"
echo RENDERER_PIN_CLEAN=PASS
check "$engine" "$2"
echo ENGINE_PIN_CLEAN=PASS
# Also ensure no writable regular file or directory anywhere in the sealed slot.
writable=$(find "$slot" \( -type f -o -type d \) -writable -print -quit)
test -z "$writable"
echo SLOT_READ_ONLY=PASS
# core-src is another root-owned checkout from preparation, not trusted for execution.
test -d "$slot/core-src/.git"
test "$(stat -c %u "$slot/core-src")" = 0
if git -C "$slot/core-src" rev-parse HEAD >/dev/null 2>&1; then
    echo UNAPPROVED_CHECKOUT=FAIL >&2
    exit 1
fi
echo UNAPPROVED_CHECKOUT_REJECTED=PASS
echo SEALED_GIT_GATE=PASS
