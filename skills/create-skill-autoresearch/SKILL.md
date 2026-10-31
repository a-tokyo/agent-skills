---
name: create-skill-autoresearch
version: 0.3.0
license: MIT
description: >-
  Factory skill that creates production-grade, benchmarked, autonomously improved,
  and verified agent skills. Orchestrates a 5-phase pipeline: interview the
  user to discover purpose and gold standards, research domain materials with parallel
  subagents, draft the skill with skill-creator and a design-first approach, invoke
  autoresearch to iterate against gold-standard-driven evaluation, then gate it with a
  with/without plugin eval and verify it through multi-agent consensus with a devil's
  advocate. Use when building a new skill, creating a skill from existing materials, or
  upgrading a skill to production quality with benchmarking and autonomous improvement.
---

# Create Skill via Autoresearch Factory

The main session runs every phase itself. On entering a phase, read that phase's reference (Detail) and not before.

| Phase | What happens | Who | Detail |
|---|---|---|---|
| 1. Interview | purpose, gold standards, scope | main | [references/phase-1-2.md](references/phase-1-2.md) |
| 2. Research | study materials, synthesis, rubric | researchers (2-7) or main inline | below |
| 3. Draft | design, author, `evaluate.sh`, split, baseline, eval suite | main | [references/phase-3.md](references/phase-3.md) |
| 4. Autoresearch | iterate against gold standards, description pass | main + autoresearch skill | [references/phase-4.md](references/phase-4.md) |
| 5. Verify | 5.0 gate, 5.1 premortem; 5.2-5.4 panel, ship | main; PANEL (3 subagents) | [references/phase-5.md](references/phase-5.md) |

BUILDER and PANEL never share context: the panel gets only the skill, gold standards, rubric and premortem risks.

**Reading.** Before Phase 1, and on each resume, read [references/operating-rules.md](references/operating-rules.md)
(paths, scripts, background jobs, unattended runs, git, 4.4 handoff and 4.5 resume).
Read each reference once, with the Read tool, at the phase that needs it. Never `cat`, `sed` or grep a reference you
have already read, and never read script source: each script's `--help` says how to run it. Every other reference
named below serves one branch: read it only when that branch applies.

## Companions

| Companion | Used in | When absent |
|---|---|---|
| skill-creator | 3.3 authoring, 4.6 description pass | 3.3 hard constraints, fallback file |
| writing-for-agents | 3.1, 4.3 craft passes, 5.1 failure modes | fallback file |
| autoresearch | Phase 4 loop, with the 4.1 conventions | run the loop by hand, same conventions |
| premortem | 5.1 | the Devil's Advocate lists the risks |
| tribunal | Phase 5 (5.2) | inline panel |
| handoff | 4.4 | write `HANDOFF-*.md` by hand |
| `claude plugin eval` | 3.6 suite, 5.0 gate | the manual protocol (5.0) |

Read [references/standalone-fallback.md](references/standalone-fallback.md) only when skill-creator or writing-for-agents is missing.
Install skills with `npx skills add <source> --skill <name> -a <claude-code|cursor|codex|opencode>`; on Windows, run inside WSL.
Read [references/hosts.md](references/hosts.md) only when the host is not Claude Code or its hooks need installing.

Hooks (`hooks/`) back these rules in a build: the kill guard (pattern kills; as the loop guard, the Phase 4 metric
after `check_gate.py --loop` says done), the subagent guard (background subagents) and the finish guard (sends the
main session back while `check_gate.py --finish` fails).

## Subagents

Only the Phase 2 researchers and the three 5.2 panel members run as subagents. Dispatch each in the **foreground**
and wait (Claude Code: `run_in_background: false`); for parallel roles, put several Agent calls in one message. A
host without subagents runs each role as a fresh headless session, in turn.

---

## Phase 1: Interview

Read phase-1-2.md and run 1.1-1.6. Never assume: ask one topic at a time. Minimum 3 gold standards; with fewer,
warn the user and offer to create synthetic examples or find more reference materials. 1.6 ends with confirmed
parameters, the workspace (its own git repository) and a `work/manifest.yaml` you derived and the user confirmed.

---

## Phase 2: Research

Run 2.1-2.3 (phase-1-2.md): study materials under 40 KB are read inline, larger ones by 2-7 researcher subagents;
the synthesis records every conflict with the gold standards, and **the gold standards win**; the rubric (2.3) is
confirmed by the user before Phase 3.
Read [references/rubric-templates.md](references/rubric-templates.md) only when you want a domain template or criteria examples.

---

## Phase 3: Draft, and Phase 4: Autoresearch

Read phase-3.md and run 3.1-3.6; its 3.5 fast path decides whether Phase 4 (phase-4.md, 4.1-4.6) runs before
Phase 5 or only when the gate or panel sends the build back. The shipping evidence is the 5.0 gate's same-model Δ, never the Phase 4 score.
Read [references/benchmark-standard.md](references/benchmark-standard.md) only when 5.0 runs the manual protocol, the skill is procedural (3.4), or you publish a benchmark.
Read [references/evaluate-templates.md](references/evaluate-templates.md) only when writing an LLM-judge `evaluate.sh`, `judges.yaml` or `evaluate-checks.sh`.
Read [references/authoring-rules.md](references/authoring-rules.md) only when a 3.3 rule needs its example or rationale.

---

## Phase 5: Verify

### 5.0-5.1 Gate and Premortem

Read phase-5.md and run 5.0 (the gate) and 5.1 (the premortem). **After 5.1, run 5.2's `check_gate.py --panel` in
the same reply**, never presenting the risks or announcing the panel, and continue in the same turn through 5.4.
Read [references/gate-runner.md](references/gate-runner.md) only when a gate's exit message does not say what to fix.

```bash
python3 <skill-dir>/scripts/check_gate.py --finish   # the build is done only when this exits 0
```

It checks a `Verdict: <label>` line (SHIP, SHIP WITH CAVEATS or `UNSHIPPED`) in `BENCHMARK.md` at the build root
against the gate, the panel and 5.4, and that no gate is live (3.6 and 5.0 run even for `UNSHIPPED`). A non-zero
exit names what is left: do it, re-run.

### 5.2 Panel Evaluation

Validate the gate first; a non-zero exit returns the build to 5.0:

```bash
python3 <skill-dir>/scripts/check_gate.py --panel                                # before the panel starts
python3 <skill-dir>/scripts/check_gate.py --panel-receipt <role> <scores.json>   # each member, after it scores
```

Then spawn 3 verifier subagents **in parallel** (one message), one agent per role. Each gets ONLY the skill's SKILL.md (a reference only when it asks for one by name), the gold standards, the rubric and the premortem risks: never research notes, `autoresearch.jsonl` or
builder context. The roles (`<role>`): Verifier-A, quality (`verifier-a`); Verifier-B, utility (`verifier-b`);
Devil's Advocate, adversarial, always tries to smuggle an instruction in through content the skill fetches
(`devils-advocate`). Each member's prompt gives its role, the input paths
and `<skill-dir>`, and tells it to read its template and the role-receipt paragraph in "Panel Prompt Templates" of `<skill-dir>/references/consensus-protocol.md` (read only as a panel member).
It scores every dimension independently (score, confidence 0-1, evidence: a verbatim quote in double quotes) and ends
with `--panel-receipt`, which binds the scores to the skill and rubric hashes: a skill change voids every receipt.

Use a **different model family** for the panel when possible, never a weaker model than the builder (on Claude,
`claude-opus-5-5`). The ORCHESTRATOR dispatches and adjudicates; it scores nothing. **Grep every verdict-driving citation** against the artifact before the
consensus; one not found verbatim discards its finding.

**If the `tribunal` skill is available, delegate Phase 5 to it**: the BUILDER's output is the artifact, the 2.3
rubric the criteria, and its orchestrator is never the agent that built the skill. Otherwise run the inline panel.

### 5.3 Consensus Protocol

`python3 <skill-dir>/scripts/check_gate.py --consensus` does the math from the three receipts and prints `PANEL
CONSENSUS:` (record: `work/experiments/panel/consensus.json`). On `SYNTHESIS NEEDED on <dimensions>`, run one
synthesis round: collect each member's rationale on those dimensions, strip the role labels, send them to every
member (its "Synthesis Round Prompt"), and each re-submits with `--synthesis`; re-run `--consensus`. Converged →
average; 2-of-3 → majority (log the dissent in `craft-decisions.md`); **deadlock → escalate to user**, as does a
Devil's Advocate `ESCALATE: <reason>`, never the average.

### 5.4 Ship or Iterate

`rubric.yaml`'s `metric:` picks the rule; the scripts apply its thresholds (verdict-rules.json, read only when
changing one) and `--finish` rejects a verdict above what it allows.

- **Metric-first** (`metric: answer-key`). `--consensus` also prints `SHIP RULE:`: **SHIP** when the gate is valid
  with every gold Δ > 0 and the Phase 4 metric meets `target_score` (the last kept experiment on this exact skill,
  the lower of its training and **held-out** scores; no held-out score is no metric); **SHIP WITH CAVEATS** when the
  lowest gold Δ or the metric is within `caveat_margin` of that; else `UNSHIPPED`. The panel runs once
  (`max_panel_rounds` 1) as a review: log its scores and findings in `BENCHMARK.md` and
  `work/experiments/craft-decisions.md`, and edit nothing in response. It forces `UNSHIPPED` only through a receipt's
  cited `veto`: a 3.3 hard constraint, a safety defect (fetched content followed as instructions, a destructive
  command), or the Devil's Advocate's ESCALATE with a reproducible failure.
- **Panel verdict** (`metric: llm-judge`, or none). The `PANEL CONSENSUS:` label caps the verdict: SHIP, SHIP WITH
  CAVEATS (log the concerns), ITERATE, BLOCK (address the blocking dimension first) or ESCALATE (the user decides);
  ITERATE, BLOCK and ESCALATE allow only `UNSHIPPED`. The gold Δs cap it too
  (`GOLD GATE:`): the lowest Δ <= 0 caps at SHIP WITH CAVEATS (record why that case cannot benefit), below
  -`caveat_margin` only `UNSHIPPED`, until a re-run shows Δ > 0. On ITERATE, feed panel feedback to autoresearch:
  each member's top concern and every Devil's Advocate failure scenario become hypotheses in
  `autoresearch.ideas.md`, the scores and rationales go in `craft-decisions.md`; return to Phase 4, then 5.0-5.1.

Any skill change after the panel re-runs 5.2 in full.

**A terminal ITERATE is `UNSHIPPED`.** ITERATE or BLOCK with no Phase 4 budget left, after the 4.3 plateau rule, a
veto, or a skill change at `max_panel_rounds` (3 panel path, 1 metric-first) ends the build: write `Verdict:
UNSHIPPED` in `BENCHMARK.md` with the final scores, the blocking concerns and the lines below when they exist. An
`UNSHIPPED` output is a draft: never describe it as shipped, never publish it.

**No SHIP without the validator.** Before writing SHIP or SHIP WITH CAVEATS, re-run `check_gate.py` and copy its
`GATE VALID:`, the `PANEL CONSENSUS:` and (metric-first) the `SHIP RULE:` lines verbatim into `BENCHMARK.md` beside
the verdict; a non-zero exit returns the build to 5.0.
Only on SHIP or SHIP WITH CAVEATS, follow [references/publishing.md](references/publishing.md): a registration checklist, not a file copy.
