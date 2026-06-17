# Gen-3 blind judging — does the skill lift haiku toward sonnet?

Judged inline by the main agent (three judge subagents stalled on the infra watchdog while
reading the artifact). Scored against the executed ANSWER-KEY (D1–D13 + contradiction C1 +
traps T1–T3), tier-weighted per rubric: Tier-1 ×1, Tier-2 ×2, Tier-3 ×3 (max raw = 3 + 8 + 18 = 29).

Arms (unblinded after scoring):
- candidate-1 = sonnet, NO skill (the bar)
- candidate-2 = haiku + skill, **independent** 3-agent panel (separate subagent contexts)
- candidate-3 = haiku, NO skill (the floor)
- candidate-4 = haiku + skill, single-session (one haiku plays all roles)

## Defect matrix

| Defect (tier, x-file) | sonnet noskill | haiku+panel | haiku floor | haiku solo-skill |
|---|---|---|---|---|
| D1 exponent (1) | ✓ | ✓ | ✓ | ✓ |
| D2 peek/dequeue (1) | ✓ | ~ (verified in cite-table, not a distinct finding) | ✓ | ✓ |
| D3 seq=0 (1) | ✓ | ✓ | ✓ | ✓ |
| D4 jitter/clamp (2) | ✓ | ✓ | ✓ | ✓ |
| D5 recordCancel no-notify (2) | ✓ | ✗ | ✗ | ✗ |
| D6 isReady ANY (2) | ✓ | ✓ | ✓ | ✓ |
| D7 FIFO reversed (2) | ✓ | ✓ | ✓ | ✓ |
| D8 unit ms-as-sec (3,x) | ✓ | ✓ | ✓ | ✓ |
| D9 reverse worker fill (3,x) | ✗ | ✗ | ✗ | ✗ |
| D10 retry <= (3,x) | ✓ | ✓ | ✓ | ✓ |
| D11 cancel not transitive (3,x) | ✓ | ✓ | ✓ | ✓ |
| D12 cycle self-edges (3,x) | ✓ | ✓ | ✗ | ✗ |
| D13 budget mishandled (3,x) | ✗ | ✓ | ✗ | ✗ |
| C1 spec contradiction | ✗ | ✗ (touched S-SCHED-7 as a code bug, not as a spec contradiction) | ✗ | ✗ |

## Scores

| Arm | T1 | T2 (×2) | T3 (×3) | Raw/29 | Norm | Cross-file T3 | Verdict | Trap FPs | Fabricated cites |
|---|---|---|---|---|---|---|---|---|---|
| sonnet, no skill | 3/3 | 4/4 → 8 | 4/6 → 12 | 23 | **0.79** | 4/6 | BLOCK ✓ | 0 | 0 |
| haiku + indep panel | 2.5/3 | 3/4 → 6 | 5/6 → 15 | 23.5 | **0.81** | **5/6** | BLOCK ✓ | 0 | 0 |
| haiku, no skill | 3/3 | 3/4 → 6 | 3/6 → 9 | 18 | **0.62** | 3/6 | BLOCK ✓ | 0 | 0 |
| haiku + solo skill | 3/3 | 3/4 → 6 | 3/6 → 9 | 18 | **0.62** | 3/6 | BLOCK ✓ | 0 | 0 |

## Findings

1. **The hypothesis is supported — with one critical condition.** Haiku + a genuinely
   independent panel reached 0.81, matching sonnet-no-skill (0.79) and edging it on the
   hardest axis (cross-file tier-3: 5/6 vs 4/6; the haiku panel uniquely caught D13, the
   ~20%-single-pass three-hop budget trace, which sonnet missed). The skill lifted haiku
   +0.19 over its floor — essentially the entire haiku→sonnet gap.

2. **Single-session "skill" gave ZERO lift** (0.62 = the no-skill floor, identical defect
   set). One model playing all three roles has perfectly correlated blind spots; there is
   nothing to triangulate. The lift comes from *independent reader contexts*, not from the
   self-checking discipline alone. This is the load-bearing nuance for the README.

3. **Hallucination axis was inconclusive on this fixture.** Zero trap false-positives and
   zero fabricated citations in ALL four arms, including haiku-no-skill. The fixture was
   defect-dense with non-tempting traps, so no arm needed to invent findings — there was no
   hallucination to suppress. The "skill suppresses hallucination" claim is neither
   confirmed nor refuted here (the one prior data point remains the v0.1.0 frontier arm that
   fabricated spec quotes until the grep-citations guardrail was restored).

4. **Uniform misses (don't discriminate):** D9 (reverse initial worker fill) and C1 (the
   spec contradiction) were missed by every arm including sonnet — both need either opus-tier
   reading or a reader explicitly assigned to cross-clause / control-flow tracing. Headroom
   remains; a future opus arm or a lens explicitly tasked with "check spec clauses against
   each other" would test it.

5. n=1 per arm; ±0.05 is noise. The qualitative result (independent panel ≫ solo; panel ≈
   sonnet; solo = floor) is robust to that noise because the gaps are large and the
   mechanism (correlated vs independent blind spots) is principled.

## Answer-key disputes
None. Every mapped defect sits at its keyed location; the traps were correctly left
unflagged by all arms.

## Addendum — original prescriptive skill, haiku + independent panel (arm-HSP-orig)

Same fixture, same model (haiku), same genuine-independent-panel architecture (3 separate verifier subagents); only the SKILL version differs (original 208-line prescriptive v0.0.1 instead of the principles-first kept version).

Findings (9): D1, D2, D3, D5, D6, D7, D8, D10, D12. Missed: D4, D9, D11, D13, C1.
- T1: D1,D2,D3 = 3/3 → 3
- T2: D5,D6,D7 = 3/4 (missed D4) → 6
- T3: D8,D10,D12 = 3/6 (missed D9,D11,D13) → 9 of 18 ; cross-file T3 = 3/6
- Raw = 18/29 = **0.62**. Verdict BLOCK ✓. Trap FPs 0, fabricated citations 0.

### Reconciliation — this revises the Iter-15 conclusion
| haiku arm | recall | cross-file T3 |
|---|---|---|
| no skill (floor) | 0.62 | 3/6 |
| solo-skill (single-session) | 0.62 | 3/6 |
| indep panel, **principles-first** | 0.81 | 5/6 |
| indep panel, **prescriptive original** | 0.62 | 3/6 |

The two independent-panel runs span **0.62–0.81** — a large spread driven almost entirely by which deep cross-file defects the 3 readers happened to surface (principles caught D11+D13; prescriptive caught D5 instead). With 6 cross-file defects each caught by a 3-reader union at only ~50–60% probability, the count is Binomial-noisy (±~1.2 defects = ±~0.12 normalized); 5/6 vs 3/6 is within ~1 SD. **n=1 per condition cannot separate skill-effect from run-variance.**

### Honest conclusions (what survives the variance)
1. **Independent panel ≥ single-session / floor** — robust across runs; the architecture (distinct agent per role, parallel after doer, no cross-poisoning) is the load-bearing factor.
2. **No evidence the original prescriptive version is better** — it scored lower here (0.62 vs 0.81). So no reason to revert; keep principles-first (also simpler, and it carries the SHIP-row bug fix).
3. **The "haiku ≈ sonnet" magnitude was over-confident on n=1.** Truthful statement: an independent panel *can* lift haiku to sonnet-level recall, but at weak-model tier the lift on the hardest cross-file defects is HIGH-VARIANCE with only 3 readers — a single run overstates it. Robustly closing the gap would need more readers (5–7, raising union-catch probability) or averaged repeated runs.
4. D9 and C1 still missed by every arm/version — opus-tier or an explicit cross-clause/control-flow lens needed.

## Variance runs (n=3 per skill version, haiku + independent panel)

| Version | Run scores (tier-wtd /29) | Mean | Range |
|---|---|---|---|
| Principles-first (kept) | 0.83, 0.69, 0.72 | **0.75** | 0.69–0.83 |
| Prescriptive original | 0.62, 0.72, 0.72 | **0.69** | 0.62–0.72 |

Reference (n=1): sonnet no-skill 0.79; haiku floor 0.62; haiku solo-skill 0.62. ALL runs (6 panel + sonnet + floor) returned the correct BLOCK.

**Conclusions (n=3):**
1. Principles-first ≥ prescriptive: +0.06 mean and more floor-reliable (worst run 0.69 vs 0.62). Between-version gap (0.06) < within-version spread (~0.14): principles is *marginally/directionally* better, not dramatically — fully supports keeping it (also simpler + carries the SHIP-row fix).
2. Independent panel lifts haiku above its 0.62 floor regardless of version (+0.13 principles / +0.07 prescriptive), **approaching** sonnet's 0.79 but not matching it on the mean. The earlier n=1 "0.81 = matches sonnet" was a high draw; the honest claim is "approaches sonnet; best runs reach it."
3. Verdict calibration is robust where recall is not: 8/8 correct BLOCK across every model/version/config.
4. Single-session (solo-roles) stays at the floor (0.62) — independence is the load-bearing factor, confirmed.
