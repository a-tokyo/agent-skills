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

This file is the main session's router: the main session runs every phase itself. On entering a phase, read that
phase's reference file (the Detail column) and not before: one phase's detail in context at a time.

| Phase | What Happens | Who runs it | Detail |
|-------|-------------|-------------|--------|
| 1. Interview | Discover purpose, gold standards, scope | ORCHESTRATOR (main) | below; [references/phase-1-2.md](references/phase-1-2.md) |
| 2. Research | Study domain materials, build dossier, propose rubric | RESEARCHER subagents (2-7), or main inline | below; [references/phase-1-2.md](references/phase-1-2.md) |
| 3. Draft | Design, author SKILL.md with skill-creator, eval script (`evaluate.sh`), split, baseline, eval suite | BUILDER (main session) | [references/phase-3.md](references/phase-3.md) |
| 4. Autoresearch | Iterate against gold standards (LLM-as-judge, or an objective real-world metric for procedural skills), then the description pass | BUILDER (main session) + autoresearch skill | [references/phase-4.md](references/phase-4.md) |
| 5. Verify | 5.0 eval gate (with vs without), 5.1 premortem; 5.2-5.4 panel, consensus, ship/iterate | main session; then PANEL (3 subagents) | [references/phase-5.md](references/phase-5.md); 5.2-5.4 below |

Key constraint: BUILDER and PANEL never share context. Panel receives only the skill output, gold standards, and rubric -- no bias from the building process.

Every session of a build, the first and each resume, follows **[references/operating-rules.md](references/operating-rules.md)**
(paths, bundled scripts, `run_owned.py` background jobs, unattended runs, credentials, git staging). Read it before Phase 1.

## Companion skills

The factory composes sibling skills rather than embedding them, so each stays current upstream.

| Companion | Used in | When absent |
|-----------|---------|-------------|
| **skill-creator** ([anthropics/skills](https://github.com/anthropics/skills)) | 3.3 authoring, 4.6 description pass | Hard constraints in 3.3 plus [references/standalone-fallback.md](references/standalone-fallback.md) |
| **writing-for-agents** ([mattpocock/skills](https://github.com/mattpocock/skills)) | 3.1 design, 4.3 craft passes, 5.1 failure modes | Craft passes in [references/standalone-fallback.md](references/standalone-fallback.md) |
| **autoresearch** ([github/awesome-copilot](https://github.com/github/awesome-copilot)) | Phase 4 loop, run with the conventions in 4.1 | Run the loop by hand with the same conventions |
| **premortem** | 5.1 | Ask the panel's Devil's Advocate for the risk list |
| **tribunal** | Phase 5 (see 5.2) | Inline panel |
| **handoff** | 4.4 | Write `HANDOFF-*.md` by hand |
| `claude plugin eval` (Claude Code CLI) | 3.6 suite, 5.0 gate | The manual with/without protocol (5.0) |

Install skills with `npx skills add <source> --skill <name> -a <claude-code|cursor|codex|opencode>`. The factory runs on any agent that loads SKILL.md; **[references/hosts.md](references/hosts.md)** gives each host's skill dirs, headless command ("the host's headless CLI"), subagents and hook installs. It runs on macOS, Linux and WSL; on native Windows, run it inside WSL.
Three hooks (`hooks/`, installed per hosts.md): the kill guard blocks pattern kills (`pkill`, `killall`, a process listing and a kill in one call); the subagent guard denies a background subagent dispatch inside a build; the finish guard sends a build back when the main session's turn ends before `check_gate.py --finish` passes (never a subagent's stop; at most `max_finish_sendbacks` times, then the build ends unfinished) — live-tested on Claude Code (and Cursor's kill guard), offline-tested on Codex and OpenCode. The kill guard is a best-effort tripwire, not a sandbox: `run_owned.py` and stopping only what you started are the contract.

## Subagents

Only the Phase 2 researchers and the three 5.2 panel members run as subagents. Dispatch each in the **foreground**
and wait for it (Claude Code: `run_in_background: false`; the subagent guard denies a background one); for parallel
roles, put several Agent calls in one message. **Hosts without subagents** run each role as a fresh headless session
(hosts.md), in turn.

## Handoff and Resume

### 4.4 Monitor and Handoff

On any phase transition, when context or the session's budget runs low, or on request:
1. Invoke the **handoff skill** to generate `work/handoffs/HANDOFF-<session>.md`
2. Write `work/handoffs/state.yaml` with structured resume state:

```yaml
phase: autoresearch       # current phase (interview|research|draft|autoresearch|verify)
session: <N>              # session counter (increments on each resume)
skill_name: <name>        # the skill being built
best_score: <value>       # best overall_score achieved
best_commit: <hash>       # commit hash of best state
experiments_run: <count>  # total experiments across all sessions
remaining_budget: <count> # experiments left in budget
validation_score: <value> # last validation set score (if applicable)
top_concerns: []          # panel feedback or known weaknesses
blocked_dimensions: []    # dimensions below threshold
last_updated: <ISO timestamp>
baseline_lock: false      # true = a human froze target_score at the recorded best (deliberately, not by interrupting the loop); do not re-derive
```

3. The next session reads `state.yaml` to resume from the correct phase
### 4.5 Resume Protocol

When `work/handoffs/state.yaml` exists: read it and the newest `work/handoffs/HANDOFF-*.md`, resume at the
recorded phase (re-confirm parameters on **interview**; synthesize if the dossier is incomplete on
**research**; read phase-3.md and measure the 3.5 baseline if the draft exists on **draft**; read phase-4.md, then
`autoresearch.jsonl`, and continue with the remaining budget on **autoresearch**; re-run the panel if the last
verdict was ITERATE on **verify**), then bump the session number.

---

## Phase 1: Interview

Do not assume -- ask, one topic at a time, and record the answers; the exact question wording and
follow-ups are in [references/phase-1-2.md](references/phase-1-2.md). Minimum 3 gold standards. Fewer is a
risk -- warn the user and offer alternatives: create synthetic examples, or find additional reference materials.

### 1.6 Confirm and Create Workspace

Summarize all parameters in a table. Ask the user to confirm.

Once confirmed, create the build workspace `builds/<skill-name>/` (the file-by-file layout: phase-1-2.md "Workspace Layout"): `input/` for
gold standards and study materials, `work/` for everything generated, `output/<skill-name>/` to ship. If the
working directory is already dedicated to this build (it holds the brief and inputs), it *is* the workspace:
create `work/` and `output/<skill-name>/` in place and keep every path below relative to it. The workspace is its
own git repository (the Phase 4 loop resets it): `git init` there unless `git rev-parse --show-toplevel` already
prints the workspace root. Inside a parent project, gitignore it there; `work/` is committed in the workspace's
own repository, except `work/pids/` (`run_owned.py` gitignores it), so no credential goes in it. Four of its files are
the human's to correct at phase boundaries: `manifest.yaml`, `evaluation/rubric.yaml` (the exit criteria),
`evaluation/judges.yaml`, `evaluation/data-split.yaml` (its splits; `gate.py record` owns `plugin_eval`). Only
`output/<skill-name>/` ships; its uplift benchmark belongs **outside** the skill dir, at `benchmarks/<skill-name>/`.

Do **not** ask the user to hand-author a manifest. Scan whatever is in `input/`, classify each item as a gold standard (exemplar input/output pair or reference artifact) vs a study material, and write your derived index to `work/manifest.yaml` with train/validation/test tags. Present the derived manifest for the user to confirm or correct. See [references/phase-1-2.md](references/phase-1-2.md) for intake formats and the manifest schema.

---

## Phase 2: Research

### 2.1 Research the Study Materials

**Small materials go inline**: when the study materials total under 40 KB (the sizes `ls -l` or `dir` lists), read
them yourself and write the notes below directly — a researcher's spawn costs more than the reading. Otherwise cluster
them by source type (existing skills, gold standard outputs, planning docs) or by subtopic, 2-7 clusters — fewer gives
no parallelism, more costs synthesis without depth — and launch one read-only researcher subagent per cluster (a host
without subagents runs each as a fresh headless session, in turn); each reads its material deeply and writes the
patterns, conventions and quality signals it finds to `work/research/NN-<topic>.md`.

### 2.2 Synthesize Research

After all researchers complete, write `work/research/00-synthesis.md`: cross-cutting patterns, the
conventions the skill must follow, quality signals, candidate rubric dimensions, and a **conflicts** table —
every point where a study material contradicts the gold standards. **The gold standards win**: record each
conflict with the gold evidence; Phase 3 confirms the draft follows the gold side of every row before the 3.5 baseline.

### 2.3 Propose Rubric

Based on research, draft `work/evaluation/rubric.yaml` from [references/rubric-templates.md](references/rubric-templates.md):
5-10 weighted dimensions on a 1-10 scale, plus `target_score` (default 0.85), `max_iterations` (default
20) and `plateau_window` (default 2). Always include these universal dimensions (adjust weights per domain):
- **correctness**: Instructions are technically accurate and executable
- **completeness**: All necessary sections and edge cases covered
- **clarity**: A naive agent can follow without ambiguity
- **consistency**: Aligns with existing codebase conventions
- **predictability**: Drives the same process every run -- completion criteria checkable and exhaustive, no vague gates, no no-op lines (writing-for-agents; fallback: [references/standalone-fallback.md](references/standalone-fallback.md))

Add 3-5 domain-specific dimensions from the research synthesis (5-10 dimensions total). Present the rubric to the user for review; iterate until confirmed.

---

## Phase 3: Draft, and Phase 4: Autoresearch

Read [references/phase-3.md](references/phase-3.md) and run 3.1-3.6; 3.5 ends with `check_gate.py --fast-path`. Exit 0
(baseline at `target_score`): after 3.6 go to Phase 5, skipping Phase 4 until the gate or the panel sends the build back
(phase-3.md 3.5); else read [references/phase-4.md](references/phase-4.md) and run 4.1-4.6. Step 3.3 reads
the authoring rules in [references/authoring-rules.md](references/authoring-rules.md); both phases follow the evaluation standard in
[references/benchmark-standard.md](references/benchmark-standard.md): the shipping evidence is the 5.0 gate's same-model Δ.

---

## Phase 5: Verify

### 5.0-5.1 Gate and Premortem

Read [references/phase-5.md](references/phase-5.md) and run 5.0 and 5.1. A gate invalid on a should-trigger or near-miss
indicator is a description defect: re-run 4.6 only, then 5.0-5.1 again. **After 5.1, run 5.2's
`check_gate.py --panel` in the same reply**, never by presenting the risks or announcing the panel. Continue in the
same turn through 5.4: the build is done only when `python3 <skill-dir>/scripts/check_gate.py --finish` exits 0 — `BENCHMARK.md` at the build root holds a `Verdict: <label>` line (SHIP, SHIP WITH CAVEATS or `UNSHIPPED`), no gate is live, a SHIP label stands on a gate that still validates and a panel consensus on the current skill that allows it, and an `UNSHIPPED` one on a gate attempt (3.6 and 5.0 run either way). A non-zero exit names what is left; do it, then re-run (the finish guard repeats it at the end of a turn).

### 5.2 Panel Evaluation

First validate the gate artifact from the workspace root; a non-zero exit returns the build to 5.0 — the gate
is never skipped, and the panel never starts on a missing, partial, incomplete or stale artifact:

```bash
python3 <skill-dir>/scripts/check_gate.py --panel                                # before the panel starts
python3 <skill-dir>/scripts/check_gate.py --panel-receipt <role> <scores.json>   # each verifier, after it scores
```

Then spawn 3 independent verifier subagents **in parallel** (one message). Each receives ONLY the skill's SKILL.md (a reference file only when it
asks for one by name), the gold standards, the rubric, and the premortem risks -- never research notes, experiment
logs (`autoresearch.jsonl`), or builder context.

| Role | Focus | Bias |
|------|-------|------|
| Verifier-A (Quality) | Correctness, completeness, clarity, spec adherence | Neutral |
| Verifier-B (Utility) | Real-world usability, edge cases, developer experience | Neutral |
| Devil's Advocate | Failure modes (incl. the five craft failure modes), hidden assumptions, missing constraints; always tries to smuggle an instruction in through content the skill fetches | Explicitly adversarial |

Each panel member scores every rubric dimension independently: score (rubric scale), confidence
(0.0-1.0), and evidence (a verbatim quote from the artifact). Its last step is the `--panel-receipt` command above
(`<role>`: `verifier-a`, `verifier-b` or `devils-advocate`; `<scores.json>`: its template output), which binds those
scores to the skill and rubric hashes: a skill change afterwards voids every receipt.

Use a **different model family** for the panel when possible — e.g. another host's headless CLI (hosts.md) for
the Devil's Advocate, never a weaker model than the builder.

**One agent per role — this is the mechanism, not a formality.** One agent simulating the panel scores at
its own solo floor (0.62 measured, vs 0.75 for separate agents): shared context, shared blind spots. The
ORCHESTRATOR dispatches and adjudicates; it scores nothing. Without parallel agents, run each role as its own fresh
headless session (hosts.md), in turn; only an orchestrator playing roles itself is "single-context (no independence)". **Grep every verdict-driving
citation** against the artifact before consensus math; one not found verbatim discards its finding.

See [references/consensus-protocol.md](references/consensus-protocol.md) "Panel Prompt Templates" for the role prompts.

**If the `tribunal` skill is available, delegate Phase 5 to it** (this pattern, generalized and benchmarked):
the BUILDER's output is the artifact, the Phase-2.3 rubric the criteria, and its orchestrator is never the
agent that built the skill. Otherwise run the inline panel — a standalone install needs no second skill.

### 5.3 Consensus Protocol

`python3 <skill-dir>/scripts/check_gate.py --consensus` does the math from the three receipts and prints a `PANEL
CONSENSUS:` line (record: `work/experiments/panel/consensus.json`). Scores agreeing → weighted average; a dispute (a spread
>= 2, the DA at 1) prints `SYNTHESIS NEEDED on <dimensions>`: one anonymized synthesis round on those, each member
re-submitting with `--synthesis`; then converged → average, 2-of-3 → majority (log the dissent in `craft-decisions.md`),
**deadlock → escalate to user**, as does a DA `ESCALATE: <reason>`, never the average. Protocol: [references/consensus-protocol.md](references/consensus-protocol.md).

### 5.4 Ship or Iterate

The `PANEL CONSENSUS:` label caps the verdict, by the thresholds in [references/verdict-rules.json](references/verdict-rules.json)
(their one home; `--finish` rejects a verdict above it): **SHIP** (>= `target_score`, no dimension blocked) -- copy the skill
to its final location; **SHIP WITH CAVEATS** (>= `target_score - caveat_margin`) -- log concerns, proceed; **ITERATE** (below
that) -- feed panel feedback to autoresearch; **BLOCK** (`block_majority` members score a dimension < `block_below`) --
address the blocking concern first; **ESCALATE** -- the user decides. ITERATE, BLOCK and ESCALATE allow only `UNSHIPPED`.

A 5.0 gate gold-standard case with Δ <= 0 caps the verdict at SHIP WITH CAVEATS until a re-run shows Δ > 0
(`--finish` enforces it); record why that case cannot benefit. Any skill change after the panel re-runs 5.2 in full.

If ITERATE: turn each member's top concern and every Devil's Advocate failure scenario into an
improvement hypothesis in `autoresearch.ideas.md`, log the panel scores and rationales in
`work/experiments/craft-decisions.md`, and return to Phase 4, then 5.0-5.1.

**A terminal ITERATE is `UNSHIPPED`.** ITERATE or BLOCK with no Phase 4 budget left, after the 4.3 plateau rule, or at
`max_panel_rounds` (3; `check_gate.py` refuses round 4) ends the build: write `Verdict: UNSHIPPED` in `BENCHMARK.md`,
plus the final scores, the blocking concerns and the two lines below when they exist (`--finish` asks for them). An
`UNSHIPPED` output is a draft: never describe it as shipped, and never publish it.

**No SHIP without the validator.** Before writing SHIP or SHIP WITH CAVEATS, re-run `check_gate.py` and copy its
`GATE VALID:` line and the `PANEL CONSENSUS:` line verbatim into `BENCHMARK.md` at the build root, beside the verdict; a non-zero
exit (the skill changed since the gate, say) returns the build to 5.0. Then follow [references/publishing.md](references/publishing.md):
a registration checklist, not a file copy — a version that disagrees with its registry entry installs the wrong thing.

