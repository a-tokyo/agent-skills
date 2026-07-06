#!/usr/bin/env bash
# fixtures/make-bare.sh <stack> [out-dir]
#
# Generates a "bare" fixture: plain official scaffolder output (no guardrail canon applied) + one git
# commit. This is the honest-negative baseline the golden fixture is compared against -- evaluate.sh
# should land a LOW guardrail_score on it (most of the 7 gates won't even exist under the canonical
# names, so Cat1/Cat2 mostly zero and the all_gates_pass cap applies).
#
# `next` and `springboot` are implemented. `springboot` is trivial (one curl + tar + git init, no
# separate CLI/toolchain install needed beyond JAVA_HOME) so ticket 17 added it alongside the adapter.
# `nest`/`django`/`go`/`rust` remain TODO stubs -- ticket 14 (matrix-fill) or a future pass fills them
# in; they exit 2 so callers never mistake "not implemented" for "scored 0".
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK="${1:?usage: make-bare.sh <next|nest|django|go|rust|springboot> [out-dir]}"
OUT="${2:-$HERE/.gen/${STACK}-bare}"

GIT_AUTHOR=(-c user.name="guardrails-benchmark" -c user.email="guardrails-benchmark@localhost")

commit_all() {
  local dir="$1" msg="$2"
  ( cd "$dir" && git "${GIT_AUTHOR[@]}" add -A && git "${GIT_AUTHOR[@]}" commit -q -m "$msg" )
}

make_next_bare() {
  rm -rf "$OUT"
  mkdir -p "$OUT"
  echo "# scaffolding bare Next.js into $OUT" >&2
  npx --yes create-next-app@latest "$OUT" \
    --ts --app --tailwind --eslint --src-dir --import-alias "@/*" \
    --use-npm --disable-git --yes
  ( cd "$OUT" && git init -q -b main )
  commit_all "$OUT" "bare: create-next-app scaffold, no guardrails"
  echo "# bare Next.js fixture ready: $OUT" >&2
}

make_springboot_bare() {
  rm -rf "$OUT"
  mkdir -p "$OUT"
  echo "# scaffolding bare Spring Boot into $OUT" >&2
  curl -sS https://start.spring.io/starter.tgz \
    -d type=gradle-project -d language=java -d packaging=jar -d javaVersion=21 \
    -d groupId=com.example -d artifactId=app -d name=app -d packageName=com.example.app \
    -d dependencies=web,validation \
    -o "$OUT/app.tgz"
  tar -xzf "$OUT/app.tgz" -C "$OUT"
  rm -f "$OUT/app.tgz" "$OUT/HELP.md"
  chmod +x "$OUT/gradlew"
  ( cd "$OUT" && git init -q -b main )
  commit_all "$OUT" "bare: Spring Initializr scaffold, no guardrails"
  echo "# bare Spring Boot fixture ready: $OUT" >&2
}

case "$STACK" in
  next) make_next_bare ;;
  springboot) make_springboot_bare ;;
  nest|django|go|rust)
    echo "TODO: make-bare.sh has no generator for stack '$STACK' yet (ticket 10 time-box: only next/springboot are implemented)." >&2
    exit 2
    ;;
  *)
    echo "unknown stack: $STACK" >&2
    exit 2
    ;;
esac
