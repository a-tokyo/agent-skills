#!/usr/bin/env bash
# claude-shim.sh: sourced by run-arm.sh (and selftest-shim.sh). write_claude_shim <out> <real-claude> <calls-log>
# [guard-plugin] writes the `claude` a build finds first on its PATH:
#
#   - it logs one timestamp per call to <calls-log>;
#   - it drops --no-session-persistence: cost metering (scoring/cost.py) reads usage from session logs under
#     $HOME/.claude/projects, and a builder's executor started with that flag leaves none, so its spend went
#     uncounted;
#   - with <guard-plugin> (a hooked factory arm), a `-p` call without --plugin-dir is an executor or judge the
#     build started: the shim puts `--plugin-dir <guard-plugin>` first and exports CSA_EXECUTOR=1, so the
#     factory's kill guard covers it even when the builder forgot (phase-3.md 3.4) and the plugin's finish and
#     subagent guards stay inert there. Calls with --plugin-dir (the builder itself) and subcommands without -p
#     (`claude plugin eval`) pass through unchanged.
write_claude_shim() {
  local out="$1" real="$2" calls="$3" plugin="${4:-}"
  cat >"$out" <<SHIM
#!/bin/sh
printf '%s\n' "\$(date +%s)" >>"$calls"
print=0 loaded=0
for arg do
  shift
  case "\$arg" in
    -p|--print) print=1 ;;
    --plugin-dir|--plugin-dir=*) loaded=1 ;;
  esac
  [ "\$arg" = "--no-session-persistence" ] || set -- "\$@" "\$arg"
done
if [ -n "$plugin" ] && [ \$print = 1 ] && [ \$loaded = 0 ]; then
  set -- --plugin-dir "$plugin" "\$@"
  CSA_EXECUTOR=1; export CSA_EXECUTOR
fi
exec "$real" "\$@"
SHIM
  chmod +x "$out"
}
