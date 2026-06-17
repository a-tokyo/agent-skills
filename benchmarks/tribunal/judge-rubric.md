# Judge Rubric — Tribunal A/B Benchmark (PRIVATE — never show to candidates)

Applies to both tasks. Two candidates are compared per task on identical prompts. Every dimension
is scored on a 1–10 scale, then combined with the weights below. Outcome and process are weighted
50/50 overall.

## Blind-judging protocol (mandatory)

1. Candidates are labeled **Candidate-1** and **Candidate-2**; the mapping to Agent-A/Agent-B is
   recorded by the operator in a sealed file the judge never sees, and is randomized per task.
2. Presentation order to the judge is randomized per task (coin flip recorded in the sealed file).
3. Transcripts are sanitized before judging: remove skill names, skill-file paths, settings dumps,
   and any text that reveals whether a methodology was installed versus improvised. Process
   *behavior* (spawning verifiers, walling context, etc.) stays — judges score behavior, not
   branding.
4. Judges are never told which candidate used a skill, that a skill exists, or which condition is
   "expected" to win.
5. Judges score each candidate independently against this rubric *before* writing any comparison.
6. Deterministic items (O1, O2, O3 verdict match, Task 2 AC table) are computed mechanically from
   the answer key / re-execution first; judges may not override them.

## Outcome dimensions (50%)

### Task 1 (verification task)

| ID | Dimension | Weight (of task total) | Scoring |
|---|---|---|---|
| O1 | Defect recall | 25% | Deterministic, from `task1/ANSWER-KEY.md`. Points: obvious defect found = 1 (D1–D3), moderate = 2 (D4–D7), subtle = 3 (D8–D10); max 20. Half points if the defect is located but the explanation/failure mode is wrong. Score = 1 + 9 × (points/20), rounded to nearest 0.5. |
| O2 | Trap false positives | 10% | Deterministic. Neither T1 nor T2 reported as a defect = 10; one trap flagged = 5; both flagged = 1. Raising a trap and explicitly clearing it as spec-compliant is not a flag. Subtract 1 (floor 1) for every 2 additional non-trap false findings (claims of defects that are not in the key and are not real spec violations). |
| O3 | Verdict correctness | 15% | Expected verdict per answer key (ITERATE). Correct verdict with severity-aware justification = 9–10; correct verdict, weak justification = 7–8; defensible-but-wrong adjacent verdict (BLOCK) with sound severity reasoning = 4–5; PASS or unjustified verdict = 1–2. |

### Task 2 (implement-and-verify task)

| ID | Dimension | Weight (of task total) | Scoring |
|---|---|---|---|
| O4 | Acceptance-criteria satisfaction | 40% | Deterministic via re-execution per `task2/JUDGING.md`. Score = 1 + 9 × (ACs verified met / 17). An AC counts only if the judge's re-run confirms it. |
| O5 | Constraint compliance | 10% | G1–G5 honored (no deps, determinism, exit codes, stderr/stdout split, cleanup). Start at 10; −2 per violated constraint (floor 1). |

## Process dimensions (50%, both tasks)

| ID | Dimension | Weight | What 9–10 looks like | What 1–3 looks like |
|---|---|---|---|---|
| P1 | Independent parallel verification actually used | 10% | Distinct verification passes that do not share the author's working context; genuinely parallel or sequentially isolated checks whose conclusions are formed independently before being merged. | Single-pass self-review; "verification" that is the author re-reading its own output in the same context. |
| P2 | Context-wall discipline | 5% | Verifiers receive the artifact + spec, not the author's rationale, draft findings, or each other's conclusions; no leakage that anchors them. | Verifier prompts include the author's reasoning, prior verdicts, or expected findings; verifiers visibly anchor on each other. |
| P3 | Evidence anchoring on every finding | 10% | Every finding/claim carries a direct quote or `file:line` (Task 1: artifact lines + spec clause IDs; Task 2: command output, exit codes, file contents). | Findings asserted from memory; spec paraphrased inaccurately; claims with no checkable anchor. |
| P4 | Adversarial scrutiny with NAMED failure scenarios | 10% | Concrete, executable failure narratives ("two reserves under a frozen clock", "release 6 twice from a 10-hold", "tamper byte 3 of line 4's chain") that follow from the artifact; hostile reading of boundaries, ordering, and state. | Generic skepticism ("edge cases may exist", "consider concurrency") with no constructed scenario; only happy-path checking. |
| P5 | Disagreement handled explicitly | 5% | Conflicting findings between passes/verifiers are surfaced, adjudicated with evidence, and the resolution recorded; dissent is not silently dropped. | Conflicts averaged away, ignored, or resolved by authority ("majority says fine") without examining evidence. |
| P6 | Correct escalation / iteration behavior | 5% | Problems found → work returned/fixed and re-verified from scratch; severity drives disposition (fixable → iterate; fundamental → block/escalate); ambiguity flagged rather than guessed. | Problems noted but waved through; re-verification skipped after fixes; wrong disposition for the severity found. |
| P7 | Verification before completion claims | 5% | No "done"/"passes"/verdict until the supporting checks have actually run; final claims restate the evidence. | Completion declared first and verified later (or never); claims outrun the evidence shown. |

Notes for judges:
- P1/P2 score what happened, not what tooling was used: an agent without multi-agent tooling can
  still earn high P1/P2 via genuinely isolated re-derivation; an agent that spawns three verifiers
  that all read each other's output earns low P2.
- Do not reward verbosity. A short report with anchored findings outscores a long unanchored one.

## Composite score

For each candidate per task:

```
outcome  = Σ (Oi score × Oi weight) / Σ Oi weights          (per-task O-dimensions)
process  = Σ (Pi score × Pi weight) / Σ Pi weights
final    = 0.5 × outcome + 0.5 × process                     (1–10)
```

Report per candidate: the dimension table with one-line justification each (deterministic items:
show the arithmetic), the composite, and only then a head-to-head comparison paragraph. The A/B
unblinding happens after both judges' scores are committed.
