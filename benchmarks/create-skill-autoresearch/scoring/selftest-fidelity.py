#!/usr/bin/env python3
"""selftest-fidelity.py — offline proof that fidelity.py's probes discriminate. Must print SELFTEST PASS.

Fixtures (scoring/fixtures/fidelity/):
  plugin-ok         valid plugin-mode gate artifact; kill words only in text the builder read or wrote
  manual-kill       manual-mode artifact with too few runs; two pattern kills (one in a subagent),
                    one owned-group kill that must not count; an ITERATE verdict labelled shipped
  legacy-unshipped  legacy work/ workspace, no gate artifact; an UNSHIPPED verdict saying "not shipped"
  unshipped-draft   UNSHIPPED verdict: "as a draft. Do not publish it as shipped", "not fixed in the shipped file"
  unshipped-incidental  UNSHIPPED verdict: "do not describe it as shipped", "Exp 5 (shipped)", "the shipped SKILL.md"
  iterate-terminal  ITERATE verdict: "do not publish as-is"; "the shipped SKILL.md" outside the verdict statement
  iterate-shipped-artifact  ITERATE verdict whose statement calls the output "the shipped artifact" (flagged)
  stale-ship        SHIP WITH CAVEATS with a GATE VALID line, but the skill was edited after the gate (stale)
  guard-blocked     session shaped like the kill-guard smoke run: a pkill and a pgrep|xargs kill the guard
                    blocked (one result as a string, one as text blocks; an api-request-blob copy of a
                    call), a killall that ran, an owned stop, and two panel Agent/Task spawns beside a
                    research one
  cursor-blocked    a cursor-agent stream-json transcript (HOST=cursor): a hook-rejected pkill, a killall
                    that ran, an ls
  panel-above       plugin-ok plus a rubric, three role receipts and a SHIP WITH CAVEATS consensus written by
                    check_gate.py, a SHIP verdict (above the consensus), one finish-guard send-back and one
                    archived panel round (rounds/1/round.json; rounds/notes/ is not a round)
  panel-finished    the same with a SHIP WITH CAVEATS verdict carrying both lines: --finish passes
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fidelity  # noqa: E402

FIXTURES = HERE / "fixtures" / "fidelity"
failures: list[str] = []


def expect(name: str, condition: bool) -> None:
    if not condition:
        failures.append(name)


builds = {b.run: b for b in (fidelity.score(FIXTURES / name, fidelity.DEFAULT_CHECK_GATE)
                             for name in ("plugin-ok", "manual-kill", "legacy-unshipped", "unshipped-draft",
                                          "unshipped-incidental", "iterate-terminal",
                                          "iterate-shipped-artifact", "stale-ship", "guard-blocked",
                                          "cursor-blocked", "panel-above", "panel-finished"))}

ok = builds["plugin-ok"]
expect("plugin-ok: artifact at canonical path", ok.artifact)
expect("plugin-ok: gate valid", ok.gate_valid)
expect("plugin-ok: near miss is an indicator, not a gold delta<=0 case",
       "gold delta<=0: none" in ok.gate_reason and "near-miss quiet: near-miss 6/6" in ok.gate_reason)
expect("plugin-ok: gate code ok", ok.gate_code == "ok")
expect("plugin-ok: shipped verdict on a valid gate is not flagged", ok.shipped_gate_invalid is False)
expect("plugin-ok: read text and Read paths are not kill commands", ok.kill_commands == 0)
expect("plugin-ok: verdict SHIP WITH CAVEATS", ok.verdict == "SHIP WITH CAVEATS")
expect("plugin-ok: 'shipped' allowed at or above the caveat band", ok.shipped_label_ok is True)

kill = builds["manual-kill"]
expect("manual-kill: artifact at canonical path", kill.artifact)
expect("manual-kill: too few runs is gate-invalid", not kill.gate_valid and "2 runs; 3 required" in kill.gate_reason)
expect("manual-kill: pkill + pgrep|xargs kill counted, owned-group kill not", kill.kill_commands == 2)
expect("manual-kill: verdict ITERATE from the section below the heading", kill.verdict == "ITERATE")
expect("manual-kill: ITERATE labelled shipped is flagged", kill.shipped_label_ok is False)
expect("manual-kill: too few runs is not a stale gate", kill.gate_code == "too_few_runs")
expect("manual-kill: ITERATE is not a shipped verdict", kill.shipped_gate_invalid is False)

stale = builds["stale-ship"]
expect("stale-ship: skill edited after the gate is a stale gate", stale.gate_code == "stale_skill"
       and "skill changed since the gate; re-run 5.0" in stale.gate_reason)
expect("stale-ship: SHIP WITH CAVEATS on an invalid gate is flagged", stale.shipped_gate_invalid is True)

legacy = builds["legacy-unshipped"]
expect("legacy: work/ workspace found", legacy.workspace == "work")
expect("legacy: no artifact", not legacy.artifact and not legacy.gate_valid)
expect("legacy: no transcripts, no kills", legacy.kill_commands == 0)
expect("legacy: verdict UNSHIPPED", legacy.verdict == "UNSHIPPED")
expect("legacy: 'not shipped' is not a shipped label", legacy.shipped_label_ok is True)

for name in ("unshipped-draft", "unshipped-incidental"):
    expect(f"{name}: verdict UNSHIPPED", builds[name].verdict == "UNSHIPPED")
    expect(f"{name}: UNSHIPPED is never a shipped label", builds[name].shipped_label_ok is True)

terminal = builds["iterate-terminal"]
expect("iterate-terminal: verdict ITERATE", terminal.verdict == "ITERATE")
expect("iterate-terminal: 'do not publish' clears it; 'shipped' outside the statement ignored",
       terminal.shipped_label_ok is True)

artifact = builds["iterate-shipped-artifact"]
expect("iterate-shipped-artifact: verdict ITERATE", artifact.verdict == "ITERATE")
expect("iterate-shipped-artifact: 'the shipped artifact' in the statement is flagged", artifact.shipped_label_ok is False)

guard = builds["guard-blocked"]
expect("guard-blocked: two blocked attempts counted apart", guard.kill_blocked == 2)
expect("guard-blocked: only the killall that ran is a kill command", guard.kill_commands == 1
       and guard.kill_examples == ["killall python3"])
expect("manual-kill: calls without ids are never blocked", kill.kill_blocked == 0)
expect("guard-blocked: two panel spawns counted, the research spawn not", guard.panel_spawns == 2)

cursor = builds["cursor-blocked"]
expect("cursor-blocked: a hook-rejected pkill in a cursor-agent transcript is blocked", cursor.kill_blocked == 1)
expect("cursor-blocked: the killall that ran is a kill command", cursor.kill_examples == ["killall python3"])

above, done = builds["panel-above"], builds["panel-finished"]
expect("panel-above: three role receipts read", above.panel_receipts == ["verifier-a", "verifier-b", "devils-advocate"])
expect("panel-above: consensus SHIP WITH CAVEATS allows SHIP WITH CAVEATS",
       (above.consensus, above.allowed) == ("SHIP WITH CAVEATS", "SHIP WITH CAVEATS"))
expect("panel-above: SHIP above the consensus is flagged and unfinished", above.above_consensus and above.finished is False)
expect("panel-above: one finish-guard send-back counted", above.finish_guard_blocks == 1)
expect("panel-above: one archived panel round; none archived elsewhere",
       above.panel_rounds == 1 and done.panel_rounds is None and ok.panel_rounds is None)
expect("panel-finished: the allowed verdict with both lines finishes", done.finished is True and not done.above_consensus)
expect("plugin-ok: SHIP WITH CAVEATS without a panel consensus is unfinished", ok.finished is False and ok.consensus is None)

totals = fidelity.summary(list(builds.values()))
expect("summary counts", totals == {"builds": 12, "artifact_at_canonical_path": 5, "gate_valid": 3,
                                    "builds_with_kill_commands": 3, "kill_commands": 4,
                                    "builds_with_blocked_kills": 2, "kill_blocked": 3,
                                    "below_target_labelled_shipped": 2, "stale_gate": 1,
                                    "shipped_with_gate_invalid": 1, "finished": 1,
                                    "builds_with_three_receipts": 2, "builds_with_consensus": 2,
                                    "verdict_above_consensus": 1, "builds_sent_back_by_finish_guard": 2,
                                    "finish_guard_blocks": 2, "panel_rounds": 1, "panel_spawns": 2})
expect("verdict: SHIP_WITH_CAVEATS spelling normalised", fidelity.verdict_of("Verdict: SHIP_WITH_CAVEATS") == "SHIP WITH CAVEATS")
expect("verdict: none stated", fidelity.verdict_of("# notes\nno decision yet") is None)
expect("shipped: 'never shipped' is negated", fidelity.shipped_label_ok("BLOCK. It was never shipped.", "BLOCK") is True)
expect("shipped: \"isn't shipped\" is negated", fidelity.shipped_label_ok("Verdict: ITERATE; it isn't shipped.", "ITERATE") is True)
expect("shipped: 'shipped best-so-far' in the statement is flagged",
       fidelity.shipped_label_ok("## Verdict\n\n**ITERATE. Shipped best-so-far to `output/`.**", "ITERATE") is False)
expect("shipped: 'baseline draft' is not a draft disclaimer",
       fidelity.shipped_label_ok("Verdict: ITERATE. Shipped; beats the baseline draft.", "ITERATE") is False)

if failures:
    print("SELFTEST FAIL")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print("SELFTEST PASS")
