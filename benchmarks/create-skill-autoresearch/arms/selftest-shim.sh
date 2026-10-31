#!/usr/bin/env bash
# selftest-shim.sh: offline check of the build's `claude` shim (claude-shim.sh) against a stub CLI that prints
# its argv and CSA_EXECUTOR. Prints SHIM SELFTEST PASS or exits 1.
#
#   bash arms/selftest-shim.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=claude-shim.sh
. "$HERE/claude-shim.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cat >"$TMP/real" <<'STUB'
#!/bin/sh
printf 'env=%s\n' "${CSA_EXECUTOR:-unset}"
for arg do printf 'arg=%s\n' "$arg"; done
STUB
chmod +x "$TMP/real"
write_claude_shim "$TMP/guarded" "$TMP/real" "$TMP/calls.log" "/plugins/create-skill-autoresearch"
write_claude_shim "$TMP/plain" "$TMP/real" "$TMP/calls.log"

FAILED=0
check() {  # check <name> <expected output> <shim> <args...>
  local name="$1" want="$2" shim="$3"
  shift 3
  local got
  got="$(env -u CSA_EXECUTOR "$TMP/$shim" "$@")"
  if [ "$got" = "$want" ]; then echo "ok: $name"; else
    echo "FAIL: $name"; printf '  want:\n%s\n  got:\n%s\n' "$want" "$got"; FAILED=1
  fi
}
check "an executor -p call gets the factory plugin first, and the marker" \
  "$(printf 'env=1\narg=--plugin-dir\narg=/plugins/create-skill-autoresearch\narg=-p\narg=--model\narg=m\narg=do it')" \
  guarded -p --model m "do it"
check "--no-session-persistence is dropped, --print counts as -p" \
  "$(printf 'env=1\narg=--plugin-dir\narg=/plugins/create-skill-autoresearch\narg=--print\narg=x')" \
  guarded --print --no-session-persistence x
check "a call that loads a plugin itself (the builder) is unchanged" \
  "$(printf 'env=unset\narg=-p\narg=x\narg=--plugin-dir\narg=/elsewhere')" \
  guarded -p x --plugin-dir /elsewhere
check "a subcommand without -p (plugin eval) is unchanged" \
  "$(printf 'env=unset\narg=plugin\narg=eval\narg=.')" \
  guarded plugin eval .
check "an arm without a hooked factory adds nothing" \
  "$(printf 'env=unset\narg=-p\narg=x')" \
  plain -p --no-session-persistence x
[ "$(wc -l <"$TMP/calls.log" | tr -d ' ')" = 5 ] && echo "ok: every call is logged" || { echo "FAIL: call log"; FAILED=1; }
[ "$FAILED" = 0 ] && echo "SHIM SELFTEST PASS" || { echo "SHIM SELFTEST FAIL"; exit 1; }
