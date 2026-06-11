# Worked Example — one slice, two rounds, both adjudication directions

A complete tribunal cycle on a fictional deliverable: a `rate-limiter` module for a generic
HTTP service. Round 1 shows a Devil's Advocate claim that SURVIVES verification (genuine bug,
ITERATE). Round 2 shows a different claim that is REFUTED (quotes code that does not exist)
and excluded from consensus. Every name, score, and finding here is illustrative.

## Contents

- [Slice spec](#slice-spec)
- [Doer report](#doer-report)
- [Round 1: independent scorecards](#round-1-independent-scorecards)
- [Round 1: adjudication and synthesis](#round-1-adjudication-and-synthesis)
- [Round 1: ledger entry](#round-1-ledger-entry)
- [Round 2: fix, re-panel, refuted dissent](#round-2-fix-re-panel-refuted-dissent)
- [Round 2: ledger entry](#round-2-ledger-entry)
- [Non-code variant (same machinery)](#non-code-variant-same-machinery)

## Slice spec

```
Slice 3 — rate-limiter module (src/limiter.ts)

Doer spec: implement a fixed-window rate limiter keyed by client id.
Public API: allow(clientId): boolean. Window and limit from config.

Acceptance criteria (frozen before implementation):
  AC1  At most `limit` requests per client per window are allowed.
  AC2  Requests beyond the limit return false until the window resets.
  AC3  Unknown client ids are tracked independently (no cross-talk).
  AC4  Unit tests cover the limit boundary and the window reset.

Verification commands: `npm test -- limiter` and `npm run lint` (both green).

Dimensions (equal weight): correctness, robustness, clarity. Target 0.80.
DA attack surface: window-boundary behavior, clock handling, memory growth.
```

## Doer report

```
Status: DONE
Implemented: fixed-window limiter in src/limiter.ts (87 lines);
  tests in test/limiter.test.ts (6 cases).
Diff summary: 2 new files, no existing files touched.
Verification (fresh runs):
  $ npm test -- limiter   ->  6 passing (412ms), exit 0
  $ npm run lint          ->  0 errors, 0 warnings, exit 0
Self-review: none outstanding.
Concerns: none.
```

The orchestrator inspects the diff (both files exist, contents match the summary) and spawns
the three verifiers in parallel, context-walled.

## Round 1: independent scorecards

| Dimension | Verifier-A (Quality) | Verifier-B (Utility) | Devil's Advocate |
|---|---|---|---|
| correctness | 8 (conf 0.9) | 8 (conf 0.8) | 7 (conf 0.7) |
| robustness | 8 (conf 0.8) | 7 (conf 0.7) | 3 (conf 0.9) |
| clarity | 8 (conf 0.9) | 8 (conf 0.8) | 7 (conf 0.8) |
| Recommendation | SHIP_WITH_CAVEATS | SHIP_WITH_CAVEATS | ITERATE |

- A's robustness evidence (8 requires a verbatim quote): "tracked per client in
  `buckets.get(clientId)` with `windowStart` reset on expiry" — boundary test present.
- DA's robustness evidence (3 requires file:line + scenario): "src/limiter.ts:41 —
  `if (now - state.windowStart >= windowMs) state.count = 0` resets the count but not
  `windowStart`. Scenario: client sends `limit` requests at t=999ms of a 1000ms window,
  then `limit` more at t=1001ms; both bursts pass — 2x the configured limit crosses the
  boundary. AC1 is violated at the window edge; no test covers a straddling burst."

## Round 1: adjudication and synthesis

Triggers fired: trigger 1, spread >= 2 on robustness (8 vs 3). Trigger 2 also fires:
Verifier-A's weighted overall is 24/30 = 0.80, meeting the 0.80 target, while the DA's is
17/30 ≈ 0.567 — more than 0.15 below the target; its disputed set resolves to robustness
only, the sole dimension where the split pair differs by 2 or more (correctness and clarity
differ by 1), so it adds nothing beyond trigger 1. The DA veto does not fire (no score of 1) and
confidence divergence does not fire (no <0.5 vs >0.8 pair). Disputed set: robustness only.
Before any math, the orchestrator spot-verifies the outlier:

- Open src/limiter.ts:41 — the quoted line exists exactly as cited, and `windowStart` is
  indeed never advanced. The straddling-burst scenario reproduces with a 3-line test.
- **The DA's claim SURVIVES verification.** The score stands.

Synthesis round (disputed dimension: robustness only; rationales anonymized as Rationale-1/2/3
and shuffled):

| Member | robustness | Action | Justification |
|---|---|---|---|
| Verifier-A | 8 -> 5 | REVISED | "Rationale-2's boundary scenario is real; my quote showed per-client tracking, not boundary safety." |
| Verifier-B | 7 -> 5 | REVISED | "Confirmed by re-running the straddle case; limit is breachable at the edge." |
| Devil's Advocate | 3 | MAINTAINED | Rebuttal: "No opposing rationale addresses the straddling burst." |

Resolution: robustness scores 5, 5, 3 — majority pair (5, 5) within 1; consensus = 5.0.
DA dissent logged (3 vs consensus 5, status: logged). Overall = (8+8+7)/3/10 x 1/3 +
5.0/10 x 1/3 + (8+8+7)/3/10 x 1/3 = 0.68 — below target − 0.10. **Verdict: ITERATE.**

Feedback packet to a fresh doer: the straddling-burst scenario verbatim, the fix list
("advance `windowStart` on reset; add a boundary-straddle test"), per-dimension scores.

## Round 1: ledger entry

```
## Slice 3 - rate-limiter module
- Round 1: A=SHIP_WITH_CAVEATS B=SHIP_WITH_CAVEATS DA=ITERATE -> ITERATE
- Dissents: DA: robustness 3 vs consensus 5 - straddling burst passes 2x
  limit at window edge, src/limiter.ts:41 (status: logged; verified genuine)
- Caveats: window-boundary fix required; boundary-straddle test required
- Panel rounds: 1 of 3
```

## Round 2: fix, re-panel, refuted dissent

A fresh doer fixes the reset (`state.windowStart = now` alongside `state.count = 0`), adds
the straddle test, reports DONE with fresh green output (7 passing). The orchestrator
re-panels with FRESH verifiers in one parallel dispatch, each re-scoring every dimension
with the round-1 disputed finding attached as a flagged risk.

| Dimension | Verifier-A (Quality) | Verifier-B (Utility) | Devil's Advocate |
|---|---|---|---|
| correctness | 9 (conf 0.9) | 8 (conf 0.9) | 8 (conf 0.7) |
| robustness | 8 (conf 0.9) | 8 (conf 0.8) | 4 (conf 0.6) |
| clarity | 8 (conf 0.9) | 8 (conf 0.8) | 8 (conf 0.8) |
| Recommendation | SHIP | SHIP | ITERATE |

- DA robustness evidence (anchoring the finding): "src/limiter.ts:58 — the
  `resetWindow()` helper discards in-flight requests when the window rolls over, so
  concurrent callers lose permitted slots."

Adjudication — all four triggers evaluated again: spread >= 2 on robustness (8 vs 4) fires;
trigger 2 does NOT fire this round (the DA's overall, 20/30 ≈ 0.667, sits only 0.133 below
the 0.80 target — inside the 0.15 band); no DA score of 1; no confidence divergence. The
orchestrator greps the artifact —
**there is no `resetWindow()` function anywhere in the diff**; the reset is inline at
line 41-42, and the module has no concurrency (single-threaded event loop, no await points
between read and write). The quoted code does not exist and the claim contradicts the
artifact. **The DA's robustness score is REFUTED and excluded from consensus math.** The
dissent is preserved verbatim in the ledger with status: refuted.

Resolution on remaining scores: robustness (8, 8) -> 8.0; correctness (9, 8, 8)
converged -> 8.33; clarity (8, 8, 8) converged -> 8.0.
Overall = sum(consensus / 10 x weight); with equal weights that is the plain mean of the
normalized consensuses: (0.833 + 0.80 + 0.80) / 3 = 0.81 >= target, no dimension blocked,
no surviving caveats.
**Verdict: SHIP** — the overall meets the target on the surviving scores; "majority" here
is the (8, 8) score pair on robustness, never a tally of recommendations. Refuted dissent
logged. No `ESCALATE:` signal was written, and the refuted scenario gives the human nothing
unrebutted to review.

## Round 2: ledger entry

Round 2's lines are appended to the same slice section (one section per slice):

```
## Slice 3 - rate-limiter module
- Round 1: A=SHIP_WITH_CAVEATS B=SHIP_WITH_CAVEATS DA=ITERATE -> ITERATE
- Round 2: A=SHIP B=SHIP DA=ITERATE -> SHIP (overall 0.81; DA robustness
  excluded as refuted)
- Dissents: DA: robustness 4 vs consensus 8 - cited `resetWindow()` at
  src/limiter.ts:58; no such function exists in the artifact and no
  concurrency window exists (status: refuted)
- Caveats: none open; round-1 caveats fixed and re-verified (straddle test green)
- Panel rounds: 2 of 3
```

Both adjudication directions in one slice: a divergent score backed by real evidence forced
an iteration a friendly panel would have shipped; a divergent score backed by fabricated
evidence was excluded so it could not sink a passing artifact. Same rule both times: check
the evidence, never average the disagreement.

## Non-code variant (same machinery)

The cycle is identical for a document deliverable. Slice: "migration runbook, section 4 —
rollback procedure." Acceptance criteria: every step names its executor and expected
observation; rollback is possible from any step; no step depends on state a prior failure
could have destroyed. Verification "commands" become checks: walk the runbook against the
system inventory; confirm each named resource exists. The doer reports DONE with the
walk-through results pasted; verifiers quote section/paragraph instead of file:line ("step
4.3 assumes the snapshot from step 2.1 survives a failed step 3 — nothing guarantees it");
the Devil's Advocate's attack vectors apply unchanged (hidden assumptions, failure
scenarios, spec gaps). Scores, triggers, synthesis, adjudication, verdicts, and the ledger
entry are exactly as above — only the evidence type changes.
