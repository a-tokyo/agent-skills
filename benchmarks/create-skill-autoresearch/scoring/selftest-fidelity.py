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
                                          "iterate-shipped-artifact"))}

ok = builds["plugin-ok"]
expect("plugin-ok: artifact at canonical path", ok.artifact)
expect("plugin-ok: gate valid", ok.gate_valid)
expect("plugin-ok: delta<=0 case reported", "delta<=0: near-miss" in ok.gate_reason)
expect("plugin-ok: read text and Read paths are not kill commands", ok.kill_commands == 0)
expect("plugin-ok: verdict SHIP WITH CAVEATS", ok.verdict == "SHIP WITH CAVEATS")
expect("plugin-ok: 'shipped' allowed at or above the caveat band", ok.shipped_label_ok is True)

kill = builds["manual-kill"]
expect("manual-kill: artifact at canonical path", kill.artifact)
expect("manual-kill: too few runs is gate-invalid", not kill.gate_valid and "2 runs; 3 required" in kill.gate_reason)
expect("manual-kill: pkill + pgrep|xargs kill counted, owned-group kill not", kill.kill_commands == 2)
expect("manual-kill: verdict ITERATE from the section below the heading", kill.verdict == "ITERATE")
expect("manual-kill: ITERATE labelled shipped is flagged", kill.shipped_label_ok is False)

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

totals = fidelity.summary(list(builds.values()))
expect("summary counts", totals == {"builds": 7, "artifact_at_canonical_path": 2, "gate_valid": 1,
                                    "builds_with_kill_commands": 1, "kill_commands": 2,
                                    "below_target_labelled_shipped": 2})
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
