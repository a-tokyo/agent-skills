# Anti-Patterns — symptoms and fixes

Nineteen ways tribunal runs go wrong. Each entry: the observable SYMPTOM, then the FIX.

## Recursive tribunals
- SYMPTOM: a verifier (or an agent handed a panel report) spins up a nested tribunal on the artifact it is judging; agent layers multiply with no depth guard.
- FIX: panel members never invoke the protocol. Panel outputs (verdicts, ledgers, plans) are verified, if at all, by one ordinary fresh panel — never nested inside a running slice cycle.

## Counter conflation
- SYMPTOM: doer re-dispatches logged as panel rounds (or vice versa); a slice escalates with panel budget unspent, or burns unbounded doer retries because only rounds are capped.
- FIX: two counters, both capped — doer attempts (default 5, complexity-weighted) and panel rounds (max 3). Whichever exhausts first escalates; the ledger records panel rounds.

## Generic skepticism
- SYMPTOM: the Devil's Advocate writes "this could be more robust" or "consider edge cases" with no named scenario; its catch rate equals its false-positive rate.
- FIX: enforce the contract — every concern names an exact scenario (input -> wrong behavior) via the seven attack vectors. Discard unanchored concerns.

## Consensus pressure
- SYMPTOM: round-1 scores cluster suspiciously; a verifier references "the other reviews."
- FIX: re-erect the wall — verifiers never see peer scores or identities before synthesis; respawn any verifier that has.

## Score anchoring
- SYMPTOM: scores hover around a number that appeared in the dispatch ("last round was 0.78").
- FIX: never tell the panel expected scores, prior-round scores, or the target's history.

## Halo inflation
- SYMPTOM: one excellent dimension and every other dimension scores within a point of it; a single holistic paragraph justifies all scores.
- FIX: atomic per-dimension passes, each with its own evidence; reject scorecards with one shared justification.

## Unbounded debate
- SYMPTOM: a second or third synthesis round "to fully converge"; positions converge to the most verbose member.
- FIX: exactly one synthesis round, then resolution math or escalation. No exceptions.

## Devil's Advocate drift to agreement
- SYMPTOM: the DA opens with concessions, mirrors the majority by round 2, or scores a perfect 10 without exhausting attack vectors.
- FIX: the DA prompt prohibits agreement language; "no flaw found" must be earned, stated, and rare. Replace a drifting DA with a fresh spawn.

## Context-wall leaks
- SYMPTOM: a verifier mentions the doer's intent, effort, or reasoning ("the implementer chose X because...").
- FIX: verifiers receive only criteria, artifact, references, command permission, and premortem risks. A leaked verifier's scorecard is void; respawn.

## Rubber-stamp panels
- SYMPTOM: three SHIPs inside minutes, no quoted evidence, no findings, no caveats — on a non-trivial slice.
- FIX: evidence thresholds make rubber-stamping detectable: an 8+ without a verbatim quote is an invalid score. Reject the scorecard and re-dispatch.

## Evidence-free findings
- SYMPTOM: findings phrased as opinions ("feels fragile") with no file:line, quote, or output.
- FIX: discard them before consensus — a finding without an anchor does not exist.

## Verifying without pre-declared criteria
- SYMPTOM: the panel is asked "is this good?"; each verifier invents its own bar; scores are incomparable.
- FIX: acceptance criteria are written before implementation and frozen; they are the rubric. No criteria, no panel — write them first.

## Improve-while-verifying scope creep
- SYMPTOM: a verifier (or the orchestrator) starts editing the artifact to "fix it while we're here"; the review becomes a second implementation.
- FIX: verifiers measure; only a fresh doer changes the artifact, via the ITERATE fix list. The orchestrator never edits the deliverable itself.

## Ledger drift
- SYMPTOM: the gate ledger lags reality by slices; verdicts reconstructed from memory at handover.
- FIX: the ledger entry is part of the slice cycle — written in the same step as the verdict, before the next slice starts.

## Premature parallel dispatch
- SYMPTOM: multiple doers running concurrently on overlapping files, or doers dispatched before the slice strategy and criteria are fixed; merge conflicts and rework.
- FIX: serialize doers by default (parallel only in isolated workspaces on disjoint slices); fix criteria and doer rules before dispatching anyone. Panel parallelism is safe — verifiers only read.

## Trusting agent success reports
- SYMPTOM: "the doer said tests pass" goes straight to the panel; the diff was never opened; later the change turns out partial or absent.
- FIX: the orchestrator's first act on DONE is inspecting the actual diff and demanding fresh command output. Agent-reported success is a claim, not a fact.

## Wrong review order (quality before spec compliance)
- SYMPTOM: detailed craft feedback on code that builds the wrong thing; "beautifully tested, doesn't match the request."
- FIX: Verifier-A checks missing / extra / misunderstood against the criteria first; a spec failure caps the verdict at ITERATE regardless of craft scores.

## Trigger-evaluation laziness
- SYMPTOM: the orchestrator checks only the trigger that obviously fired (a big spread, say) and never evaluates the remaining three, silently narrowing or missing the disputed set.
- FIX: evaluate all four triggers every round; the disputed set is the union of all flagged dimensions.

## Averaging away disagreement
- SYMPTOM: a 3-vs-8 split becomes "5.5, close enough to ship" — or a confused outlier's score sinks a passing artifact — without anyone checking whose evidence is true.
- FIX: adjudicate before aggregating. Spot-verify the outlier's evidence against the artifact: refuted evidence -> score excluded, dissent logged as refuted; surviving evidence -> the concern drives the verdict. The mean is only for scores that earned their place in it.
