# toolchain-env.sh — ONE toolchain environment for both the agent runs (run-arm.sh) and the scorer
# (evaluate.sh). Found 2026-07-06: rust/springboot with-skill runs scored a uniform capped 50 because
# the AGENTS had cargo-llvm-cov/cargo-deny (installed into the shared containment CARGO_HOME) and
# JAVA_HOME, but the SCORER ran gates in the maintainer's bare shell where none of that exists —
# the referee couldn't run the gates it was judging. Source this from both sides so the toolchain
# the gates see is identical by construction.
#
# Usage: BENCH_HERE must be set to THIS benchmark's directory (the one containing cache/,
# e.g. .../benchmarks/app-ai-guardrails) before sourcing.
: "${BENCH_HERE:?source with BENCH_HERE=<this benchmark dir (containing cache/)>}"

_CACHE="$BENCH_HERE/cache"
mkdir -p "$_CACHE/npm" "$_CACHE/pnpm-store" "$_CACHE/pnpm-home" "$_CACHE/bun" "$_CACHE/uv" \
         "$_CACHE/gomod" "$_CACHE/gopath" "$_CACHE/cargo" "$_CACHE/playwright" "$_CACHE/gradle"

export npm_config_cache="$_CACHE/npm"
export npm_config_prefer_offline=true
export PNPM_STORE_DIR="$_CACHE/pnpm-store"
export PNPM_HOME="$_CACHE/pnpm-home"
export BUN_INSTALL_CACHE_DIR="$_CACHE/bun"
export UV_CACHE_DIR="$_CACHE/uv"
export GOMODCACHE="$_CACHE/gomod"
export GOPATH="$_CACHE/gopath"
# CARGO_HOME carries installed subcommand binaries (cargo-llvm-cov, cargo-deny) as well as the
# registry cache — shared deliberately so tools install once and BOTH arms and the scorer see them.
export CARGO_HOME="$_CACHE/cargo"
export PATH="$CARGO_HOME/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$_CACHE/playwright"
export GRADLE_USER_HOME="$_CACHE/gradle"
# keg-only JDK (ticket 17): not on default PATH
if [ -d /opt/homebrew/opt/openjdk@21 ]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@21
  export PATH="$JAVA_HOME/bin:$PATH"
fi
