# Rubric — `taskq` v3 fixture

Outcome-weighted **80 / 20** (Outcomes 80, Process 20). Score a review report produced for
`prompt.md` against `ANSWER-KEY.md`. The rubric is deliberately **methodology-agnostic**: a
single linear report and a multi-agent panel that surface the *same* findings receive the *same*
score (see Process, below). What is scored is **what was found and concluded**, not how.

Apply the key as ground truth. A finding "matches" a defect if it identifies the same root cause
at the right location (line within the documented range, or unambiguous description) and the
behavior it describes is correct. Paraphrases are fine; misdescribed mechanics do not match.

---

## O1 — Tier-weighted recall of real defects (max 100 raw → normalized)

Each correctly identified defect earns its tier weight:

- Tier-1 defect: **×1** each (D1, D2, D3) → 3 points available.
- Tier-2 defect: **×2** each (D4, D5, D6, D7) → 8 points available.
- Tier-3 defect: **×3** each (D8, D9, D10, D11, D12, D13) → 18 points available. Every Tier-3
  defect carries ×3; the cross-file ones (the bulk of them) already dominate the point mass.

**O1 raw total available = 3 + 8 + 18 = 29.**

Partial credit: a finding that flags the right location but misattributes the cause/clause earns
**half** the tier weight. A finding that only flags a symptom without identifying the defective
site earns **half**.

`O1_score = (earned / 29)`.

> Cross-file emphasis is already encoded: the ×3 Tier-3 defects (which are the cross-file ones)
> dominate the available points (18 of 29). A reviewer who only catches the single-pass-easy
> Tier-1/Tier-2 issues caps at 11/29 ≈ 0.38 on O1.

---

## O2 — False-positive penalty

Penalize claims that are not real defects. **Verify each flagged item against the spec before
penalizing** (a real defect reported with a slightly wrong line is NOT an FP — score it under O1).

- Reporting a **trap** (T1, T2, T3) as a defect: **−2** each. (These are the discriminating FPs:
  they look wrong but are spec-mandated.)
- Any **other** false positive (flagging compliant code, inventing a clause, mis-tracing behavior
  into a non-issue): **−1** each.

`O2_penalty = 2 × (traps flagged) + 1 × (other FPs)`, expressed as a deduction.

---

## O3 — Verdict correctness + contradiction surfacing

- **Verdict (max 10):**
  - **BLOCK** = full 10 (matches the key: liveness/safety defects make shipping unsafe).
  - **ITERATE** = 4 (defensible only if the reviewer genuinely missed all liveness defects; still
    not the correct call given D10/D11/D12/D13).
  - **PASS** = 0 (incorrect; the codebase has shipping-blockers).
  - The verdict must be *consistent* with the findings: BLOCK with no serious finding cited, or
    PASS while listing liveness bugs, caps this component at 2 (internally incoherent).
- **Contradiction bonus (max 10):** surfacing **C1** (the S-SCHED-6 vs S-SCHED-7 impossibility)
  as a contradiction to escalate — *not* silently picking a side, and *not* merely reporting D13 —
  earns the full **10**. Identifying the budget area as "confusing/underspecified" without naming
  the two conflicting clauses earns **4**. Missing it earns **0**.

`O3_score = (verdict + contradiction_bonus) / 20`.

> The contradiction bonus is the **discriminating behavior** for this fixture: it is the single
> highest-value item a reviewer can surface, and it is the thing a hurried single pass most
> reliably misses. It is weighted to matter.

---

## Outcome aggregate (80%)

```
O1_w = 0.45      # tier-weighted recall — the core signal
O2_w = 0.15      # false-positive discipline (subtractive)
O3_w = 0.20      # verdict + contradiction

Outcomes = O1_w * O1_score
         - O2_w * min(1, O2_penalty / 10)     # cap the FP deduction at the full O2 weight
         + O3_w * O3_score
Outcomes_pct = 0.80 * clamp(Outcomes / (O1_w + O3_w), 0, 1)
```

(Intuition: recall and verdict/contradiction are the positive drivers; false positives erode the
score, capped so that one bad FP does not zero out an otherwise strong review.)

---

## Process (20%) — outcome-linked ONLY

Process credit is awarded *only* for properties that are verifiable against the report's actual
findings. **Ceremony earns nothing.** Specifically:

- **+10 (of 20): Evidence anchoring on real findings.** Every *correct* finding cites a concrete
  `file:line` and a real spec clause id, and (where applicable) a concrete failure example/trace
  that holds up. Credit scales with the fraction of correct findings that are properly anchored.
- **+10 (of 20): No unverified claims.** The report makes no assertion it did not check —
  i.e., no fabricated behavior, no clause that doesn't exist, no "this might be wrong" hedges
  presented as findings. Each unverified/fabricated assertion costs 2 of these 10 points.

**Explicit exclusion (no credit):** panel structure, number of reviewers/rounds, devil's-advocate
ceremony, synthesis prose, confidence theater, or any description of *process*. A single-pass
report and a multi-agent report with **identical findings, identical anchors, and the identical
verdict score identically.** Process points reward only that the *delivered findings* are
anchored and verified — never the apparatus that produced them.

`Process_pct = 0.20 * (process_points / 20)`.

---

## Total

```
TOTAL = Outcomes_pct + Process_pct        # in [0, 1]
```

### Reference outcomes

- **Strong triangulating review:** catches most Tier-3 cross-file defects (high O1), surfaces C1
  (full O3 bonus), avoids the traps (no O2 hit), BLOCK verdict → high TOTAL.
- **Competent single pass:** catches the Tier-1s and some Tier-2s, misses most Tier-3 cross-file
  defects and C1, may trip one trap → middling-to-low TOTAL, gated by the 18/29 Tier-3 mass and
  the missed contradiction bonus.
