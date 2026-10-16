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

The factory orchestrates 4 agent roles through 5 phases:

| Phase | What Happens | Agent Role |
|-------|-------------|------------|
| 1. Interview | Discover purpose, gold standards, scope | ORCHESTRATOR |
| 2. Research | Study domain materials, build dossier, propose rubric | RESEARCHER (N parallel) |
| 3. Draft | Design structure, author SKILL.md with skill-creator, measure baseline, scaffold the eval suite | BUILDER + skill-creator |
| 4. Autoresearch | Iterate skill against gold standards (LLM-as-judge, or an objective real-world metric for procedural skills — see 3.4) | BUILDER + autoresearch skill |
| 5. Verify | Eval gate (with vs without), premortem, panel scoring, consensus, ship/iterate | PANEL (3 subagents) |

Key constraint: BUILDER and PANEL never share context. Panel receives only the skill output, gold standards, and rubric -- no bias from the building process.

**Unattended runs** (a brief, no user at the terminal): where a step asks to confirm, adopt the brief's
answer or the stated default, answer autoresearch's setup without waiting for its confirmation, and
record each assumption in `work/experiments/craft-decisions.md`.

**Background jobs** (eval runs, `claude plugin eval`, loops): capture each PID at start (`cmd & pid=$!`
or a pidfile under `work/`), then wait on or stop only those PIDs or their process group. Never `pkill`,
`killall` or `pgrep | xargs kill` by name or pattern: that kills unrelated processes on the machine.

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
| `claude plugin eval` (Claude Code CLI) | 3.6 suite, 5.0 gate | `evaluate.sh` plus the manual uplift benchmark |

Install skills with `npx skills add <source> --skill <name>`; `claude plugin eval` ships with Claude Code.

---

## Phase 1: Interview

Do not assume -- ask, one topic at a time, and record the answers; the exact question wording and
follow-ups are in [references/pipeline-phases.md](references/pipeline-phases.md).

### 1.1 - 1.5 What to discover

| Topic | Ask about | Record |
|-------|-----------|--------|
| **Purpose and domain** | the problem it solves, the domain, which agent will use it, what "success" looks like | `SKILL_PURPOSE`, `DOMAIN`, `TARGET_USER`, `SUCCESS_CRITERIA` |
| **Gold standards** | examples of "what good looks like" — input/output pairs, reference artifacts, previously solved problems, existing quality reports; where they are, what format, how many | `GOLD_STANDARD_SOURCE`, `GOLD_STANDARD_FORMAT`, `GOLD_STANDARD_COUNT` |
| **Study materials** | docs, existing code, transcripts, design docs, reference implementations, specs, style guides | `STUDY_MATERIALS` |
| **Scope and constraints** | conventions to follow, skills to integrate with, anti-patterns to avoid, target line count (default < 500), and invocation mode — model-invoked (pays permanent context load) or user-invoked (`disable-model-invocation: true`) | `CONSTRAINTS`, `INTEGRATION_SKILLS`, `ANTI_PATTERNS`, `INVOCATION_MODE` |
| **Existing skill** | is there one for this domain already? If so it is both a study material and a baseline — research it, measure it against the rubric, improve it rather than starting over | `EXISTING_SKILL`, and set mode to **upgrade** rather than **greenfield** |

Minimum 3 gold standards. Fewer is a risk -- warn the user and offer alternatives: create synthetic
examples, or find additional reference materials.

### 1.6 Confirm and Create Workspace

Summarize all parameters in a table. Ask the user to confirm.

Once confirmed, create the build workspace `builds/<skill-name>/` (layout under Output Structure): `input/`
for gold standards and study materials, `work/` for everything generated, `output/<skill-name>/` to ship.
If the working directory is already dedicated to this build (it holds the brief and inputs), it *is* the
workspace: create `work/` and `output/<skill-name>/` in place and keep every path below relative to it.
The workspace is its own git repository, because the Phase 4 loop resets it: run `git init` there
unless `git rev-parse --show-toplevel` already prints the workspace root.

Do **not** ask the user to hand-author a manifest. Scan whatever is in `input/`, classify each item as a gold standard (exemplar input/output pair or reference artifact) vs a study material, and write your derived index to `work/manifest.yaml` with train/validation/test tags. Present the derived manifest for the user to confirm or correct. See [references/pipeline-phases.md](references/pipeline-phases.md) for intake formats and the manifest schema.

---

## Phase 2: Research

### 2.1 Spawn Researcher Subagents

Cluster study materials by source type (existing skills, gold standard outputs, planning docs) or by
subtopic, 2-7 clusters — fewer gives no parallelism, more costs synthesis without depth. Launch one
`explore` subagent per cluster; each reads its material deeply and writes the patterns, conventions and
quality signals it finds to `work/research/NN-<topic>.md`.

### 2.2 Synthesize Research

After all researchers complete, write `work/research/00-synthesis.md`: cross-cutting patterns, the
conventions the skill must follow, quality signals, candidate rubric dimensions, and a **conflicts** table —
every point where a study material contradicts the gold standards. **The gold standards win**: record each
conflict with the gold evidence, and before the 3.5 baseline confirm the draft follows the gold side of every row.

### 2.3 Propose Rubric

Based on research, draft `work/evaluation/rubric.yaml` from [references/rubric-templates.md](references/rubric-templates.md):
5-10 weighted dimensions on a 1-10 scale, plus `target_score` (default 0.85), `max_iterations` (default
20) and `plateau_window` (default 5).

Always include these universal dimensions (adjust weights per domain):
- **correctness**: Instructions are technically accurate and executable
- **completeness**: All necessary sections and edge cases covered
- **clarity**: A naive agent can follow without ambiguity
- **consistency**: Aligns with existing codebase conventions
- **predictability**: Drives the same process every run -- completion criteria checkable and exhaustive, no vague gates, no no-op lines (writing-for-agents; fallback: [references/standalone-fallback.md](references/standalone-fallback.md))

Add 3-5 domain-specific dimensions from the research synthesis (5-10 dimensions total).

Present the rubric to the user for review. Iterate until confirmed.

---

## Phase 3: Draft

### 3.1 Design Document

Create `work/experiments/DESIGN.md` with:
- Skill name and description (following the hard constraints in 3.3)
- Structural decisions: section count, reference file split, progressive disclosure plan
- Invocation mode, information-hierarchy plan (steps vs reference; inline vs disclosed,
  licensed by branching), and candidate leading words -- per **writing-for-agents**
- The deterministic/judgement split: which operations become bundled scripts the skill tells the
  agent to run (lookups, filtering, arithmetic, validation), and which stay in prose
- Integration points with other skills
- Key terminology and voice decisions

### 3.2 Grill the Design

Before writing any skill code, challenge the design adversarially: what would make it fail in practice,
which structural decisions are assumed rather than justified, whether it matches what the gold standards
demonstrate, and whether a simpler alternative exists. Present concerns to the user; iterate until the
design survives scrutiny.

### 3.3 Generate SKILL.md Draft

Author the draft with **skill-creator**'s writing guide (skill anatomy, progressive disclosure,
writing patterns), feeding it DESIGN.md and the research synthesis instead of re-interviewing --
the factory's Phase 1-2 already answered its intent questions. Skip its eval loop and packaging;
Phases 3.4-5 replace them. Without skill-creator, write it directly using
[references/standalone-fallback.md](references/standalone-fallback.md).

Either way, run this **pre-flight checklist** before writing -- hard constraints, not preferences:
- `name`: <= 64 chars, lowercase/numbers/hyphens only, no leading or trailing hyphen and no `--`, **no reserved words `anthropic`/`claude`**; descriptive kebab-case, matching the naming of the set it ships with
- `description`: <= 1024 chars measured on the joined folded block, no `<` or `>`, **third person**, states both WHAT it does and WHEN to use it
- Body < 500 lines; progressive disclosure (essentials in SKILL.md, detail in `references/`)
- File references **one level deep** only; a table of contents for any reference file > 100 lines
- Concrete examples over abstract instructions; consistent terminology; forward-slash paths
- Deterministic operations from DESIGN.md ship as small functions in `scripts/`, run by a path relative to the skill's base directory — never a hard-coded `~/.claude/skills/...`, which breaks under plugin loading
- Description craft: leading word front-loaded, one trigger per branch, no synonym padding;
  user-invoked skills get a one-line human-facing description

Write the draft to `output/<skill-name>/SKILL.md` (reference files in `output/<skill-name>/references/`, scripts in `output/<skill-name>/scripts/`).

### 3.4 Build Evaluation Script

Create `work/evaluation/evaluate.sh` that:
1. With no argument, scores every case in the current `training` split of `work/evaluation/data-split.yaml`
   (the Phase 4 metric command); with a gold standard test case path, scores that one case
2. Extracts the input from each test case
3. Invokes the skill on the input -- since skills are markdown instructions (not
   executables), this means calling an LLM with the SKILL.md as a system prompt
   and the test case input as the user message. Use `curl` to an OpenAI-compatible
   API, or a language-specific SDK. Capture the LLM's output.
4. Compares the output to the gold standard reference using an LLM-as-judge
5. Emits `METRIC <dimension>=<score>` lines to stdout
6. Emits `METRIC overall_score=<weighted_average>` (the mean over the cases scored) as the primary metric

See `self-test/evaluation/evaluate.sh` in the [agent-skills-harness](https://github.com/a-tokyo/agent-skills-harness) repo for a complete reference implementation.

**`overall_score` steers Phase 4; it is not the shipping evidence.** It measures absolute output quality
against gold standards, with no bare-model comparison in it, so it cannot answer "does this skill help".
That question needs a **same-model uplift benchmark** -- the same model on identical tasks with the skill
and without -- built before ship and reported with its honest negatives. Reading effectiveness off
`overall_score` is the most common way to conclude a working skill is useless. Standard, arms, sample
sizes and the retry-parity rule: [references/benchmark-standard.md](references/benchmark-standard.md).

**Prefer deterministic checks** — line count, frontmatter, link integrity, regex pattern coverage,
structural validation — and emit their METRIC lines directly. Use an LLM judge only for judgement
dimensions (clarity, quality match, curation): structured JSON per dimension, each scored
independently, verbatim evidence for extreme scores, a different model family from the builder. With no
LLM available, score deterministically and log a warning.

**Procedural / agentic skills (prefer this when it applies)**: some skills don't *generate* an artifact in
one shot — they instruct an agent to perform a multi-step task on a real artifact (migrate a framework
version, refactor a module, scaffold infra), or bundle `scripts/` the agent must run. A system-prompt
call cannot execute a script, so the single-call harness above is the wrong
instrument: evaluate by **execution against a real artifact with an objective real-world metric**, where
the artifact's own ground truth replaces the judge. Full method — baseline capture, reset-then-fresh-agent
orchestration, and why a fresh agent per run is the point — in
[references/benchmark-standard.md](references/benchmark-standard.md).

When budget allows, run 2-3 judge models from different families, average per dimension, and treat
high per-judge variance as a sign the criterion is ambiguous; configure them in
`work/evaluation/judges.yaml` ([references/pipeline-phases.md](references/pipeline-phases.md)).

Optionally create `work/evaluation/evaluate-checks.sh` for correctness gates.

### 3.5 Measure Baseline

Run `evaluate.sh` with the initial draft, record the scores as experiment 0, and report them to the user.

### 3.6 Scaffold the Eval Suite

Build the suite the 5.0 gate runs, in a wrapper plugin under `work/` so nothing eval-related ships:
`work/evaluation/plugin/` holds `.claude-plugin/plugin.json` (`name`, `version`), a copy of the skill at
`skills/<skill-name>/` (refresh it before every run), and `evals/`. From that directory, scaffold each
case with `claude plugin eval init --bare <case>` (a human at a terminal may use the `init` interview):
- one case per **gate gold standard** — the test split with >= 10 gold standards, every gold standard
  with 3-9 (4.2): the prompt as a user would phrase it, input files
  copied in by a `scaffold_script` ([worked case](references/pipeline-phases.md)), graders on decisive facts — `regex` on
  `{ source: file, path: <output> }` where mechanical, `llm` only for judgement;
- two or more **should-trigger** prompts graded `tool_used` on `Skill` (model-invoked skills only);
- one **should-not-trigger** near miss, graded by `graders/not-fired.md`:

```yaml
---
type: tool_used
tool: Skill
min: 0
max: 0
arm: both
---
```

Record the cases in `work/evaluation/data-split.yaml` under `plugin_eval`. Claude Code is available iff
`claude plugin eval --help` exits 0; headless and unattended runs still build and run this suite.
Without it, log `plugin_eval: unavailable` there and still write the cases' prompts — 5.0 runs them by hand.

---

## Phase 4: Autoresearch

Invoke the **autoresearch skill** to iterate the skill draft against the evaluation rubric.

### 4.1 Configure Autoresearch

Answer every autoresearch setup question up front from this list, so its interactive setup
confirms once and the loop never waits on a user. All paths are relative to the build workspace
root (`builds/<skill-name>/`), the autoresearch working directory. Session files (`results.tsv`,
`run.log`, `autoresearch.jsonl`, `autoresearch.ideas.md`) live at the workspace root during the
session and are archived to `work/experiments/` when it ends or on handoff.

- **Goal**: Improve `<skill-name>` quality as measured by `overall_score` (LLM-as-judge against gold standards, or the objective real-world metric for procedural skills — see 3.4)
- **Metric command**: `./work/evaluation/evaluate.sh` (no argument: the training split, 3.4)
- **Metric extraction**: the value of the `METRIC overall_score=<value>` line in `run.log`
  (regex `^METRIC overall_score=([0-9.]+)$`); the other `METRIC` lines are per-dimension context
- **Direction**: `higher_is_better`
- **In-scope files**: `output/<skill-name>/SKILL.md`, `output/<skill-name>/references/*`, `output/<skill-name>/scripts/*`
- **Out-of-scope files**: `input/`, `work/`
- **Constraints**: the hard constraints in 3.3; if `work/evaluation/evaluate-checks.sh` exists, it
  must exit 0 **before** measuring — a failing check is a discard, not a measurement
- **Budget**: From rubric config `max_iterations` (default 20)
- **Simplicity policy**: the autoresearch default (simpler wins ties)
- **Loop conventions**: apply [references/pipeline-phases.md](references/pipeline-phases.md) "Loop Conventions
  the Factory Owns" in every experiment; its noise threshold overrides upstream's keep-any-improvement rule

The factory owns three records the upstream loop does not keep. Write them yourself in the LOG step:
- **`autoresearch.jsonl`**: one JSON line per experiment — `experiment`, `commit`, `metric`,
  `status`, per-dimension scores, and `hypothesis` / `learned` / `rollback_reason` — plus
  `"type": "validation_check"` lines (4.2). Resume and Phase 5 read this file.
- **`autoresearch.ideas.md`**: the backlog seeded in 4.3. Read it in every THINK step; tick what you try.
- **`results.tsv`**: the upstream table, unchanged.

### 4.2 Data Split

With >= 10 gold standards, split **70% training** (every experiment), **20% validation** (checked
adaptively, below) and **10% test** (held out until Phase 5). With 3-9, use **leave-one-out rotation**:
each experiment evaluates all but one, rotating which is held out; there is no test split, so the 3.6
gate uses every gold standard as a case.

Record the split in `work/evaluation/data-split.yaml`.

**Large sets (100+ gold standards)**: evaluate a rotating sample of 10-15 training cases per experiment;
run the full training set only on kept experiments and at phase boundaries.

**Overfitting detection**: run `evaluate.sh` against the validation set adaptively — after every **kept**
experiment, after a **plateau** (is the ceiling real or training-specific?), and when the training score
jumps by more than 0.05. If training improves while validation drops by more than 0.05, warn the user
that recent changes may be over-fitted and offer to generalize them, revert to the last
validation-stable commit, or widen rubric criteria that have become too narrow. Log validation checks in
`autoresearch.jsonl` as `"type": "validation_check"`.

**Overfitting detection for leave-one-out** (< 10 gold standards): with no fixed validation set, track
per-case variance — if it widens while the mean improves, the skill is specializing for some cases at
others' expense. Flag when any single case drops > 1.0 point while others improve.

### 4.3 Let Autoresearch Run

The autoresearch skill runs the THINK-EDIT-COMMIT-RUN-MEASURE-DECIDE-LOG cycle (commit-first git
model, revert on no improvement, diversify after plateaus). The factory runs the checks before
MEASURE and writes the jsonl record in LOG, as configured in 4.1.

Seed `autoresearch.ideas.md` before the first experiment with:
- Ideas from research synthesis
- Per-dimension improvement strategies from the rubric
- Patterns observed in gold standards that aren't yet reflected in the skill
- Craft passes from **writing-for-agents** (fallback: [references/standalone-fallback.md](references/standalone-fallback.md)): leading-word hunt, no-op/duplication/sediment prune, disclosure rebalance
- Moving deterministic work from prose into `scripts/` where the executor keeps getting it wrong

**Ending the loop is the factory's call, not the loop's.** The autoresearch skill treats a plateau as
advisory and continues while budget remains, so a `target_score` the task cannot reach burns the whole
budget and reports "exit criteria not met" -- forever. Override that:

- **A confidence-qualified plateau is terminal.** If the plateau sits within judge variance (~0.2-0.3 on
  a 1-10 scale, below which gains are not measurable), stop and carry best-so-far into Phase 5.
- **Below target is a verdict, not a failure** -- Phase 5.4 grades it (SHIP WITH CAVEATS at or above
  `target_score - 0.10`). Honour `baseline_lock` in `state.yaml` the same way, logging the real target
  in the ideas backlog.
- Never ask for repeated re-runs toward an unreachable number: report the ceiling, name the binding
  constraint (target too high, or gains below judge variance), and proceed.

### 4.4 Monitor and Handoff

On any phase transition, when context or the session's budget runs low, or on request:
1. Invoke the **handoff skill** to generate `work/handoffs/HANDOFF-<session>.md`
2. Write `work/handoffs/state.yaml` with structured resume state — phase, session counter, best score
   and commit, experiments run, remaining budget, validation score, top concerns, blocked dimensions,
   and `baseline_lock` (full schema in [references/pipeline-phases.md](references/pipeline-phases.md))
3. The next session reads `state.yaml` to resume from the correct phase

### 4.5 Resume Protocol

When `work/handoffs/state.yaml` exists: read it for the current phase, read the most recent
`work/handoffs/HANDOFF-*.md` for context, resume at the recorded phase (re-confirm parameters on
**interview**; synthesize if the dossier is incomplete on **research**; measure a baseline if the draft
exists on **draft**; read `autoresearch.jsonl` for what each experiment tried and learned and continue with
the remaining budget on **autoresearch**; re-run the panel if the last verdict was ITERATE on **verify**),
then bump the session number. Per-phase detail: [references/pipeline-phases.md](references/pipeline-phases.md).

### 4.6 Description Pass

Model-invoked skills only, after the loop ends: run **skill-creator**'s description optimizer
(`scripts/run_loop.py`) on the best commit, with the 3.6 trigger and near-miss prompts as its seed
queries. Keep the new description only if the 3.6 trigger cases still pass and `overall_score`
does not drop; log the decision in `autoresearch.jsonl`. Pass `--report <path>` under `work/` in
headless runs (flags: `python3 -m scripts.run_loop --help` from the skill-creator dir). Without
skill-creator or `claude -p`, skip 4.6 and log the skip in `autoresearch.jsonl`.

---

## Phase 5: Verify

### 5.0 Eval Gate

Refresh `work/evaluation/plugin/skills/<skill-name>/` from `output/`, then run the 3.6 suite with and
without the skill (grant only the gated tools the skill needs; pin the model the skill targets):

```bash
cd work/evaluation/plugin && claude plugin eval . --ablation with-without --runs 3 -j 4 \
  --model <model> --allow-tools Bash Write --scaffold --trust-plugin --no-publish --threshold 0 \
  --json ../plugin-eval.json
```

Read WITH, W/OUT and Δ per case from the JSON; the verdict comes from Δ, never the exit code. This is
the same-model uplift the shipping evidence needs ([references/benchmark-standard.md](references/benchmark-standard.md)):
- Δ <= 0 on a gate gold-standard case is a **verified failure scenario** for the panel — the skill adds
  nothing there, or hurts;
- a should-trigger case that fails WITH is a description defect — return to 4.6, not to the panel;
- pass the JSON summary to the panel as a known risk list and copy it into `BENCHMARK.md`.

Without Claude Code (3.6), run the manual with/without protocol in
[references/benchmark-standard.md](references/benchmark-standard.md) on the same cases and write WITH,
W/OUT and Δ per case to `work/evaluation/uplift.json`. The gate is never skipped: Phase 5 always starts
with `plugin-eval.json` or `uplift.json`.

### 5.1 Premortem

Invoke the **premortem skill** on the skill artifact. Feed identified risks into the panel evaluation as additional test scenarios, including the five craft failure modes (premature completion, duplication, sediment, sprawl, no-op) as required probes.

### 5.2 Panel Evaluation

Spawn 3 independent verifier subagents **in parallel**. Each receives ONLY:
- The skill SKILL.md and references
- The gold standards
- The rubric
- The premortem risks

They do NOT receive: research notes, experiment logs (`autoresearch.jsonl`), or builder context.

**Panel roles:**

| Role | Focus | Bias |
|------|-------|------|
| Verifier-A (Quality) | Correctness, completeness, clarity, spec adherence | Neutral |
| Verifier-B (Utility) | Real-world usability, edge cases, developer experience | Neutral |
| Devil's Advocate | Failure modes (incl. the five craft failure modes), hidden assumptions, missing constraints | Explicitly adversarial |

Each panel member scores every rubric dimension independently: score (rubric scale), confidence
(0.0-1.0), and evidence (a verbatim quote from the artifact).

Use a **different model family** for the panel when possible — e.g. another harness's headless CLI
(`cursor-agent -p --model <other-family>`, `codex exec`) for the Devil's Advocate, never a weaker
model than the builder.

**One agent per role — this is the mechanism, not a formality.** A single agent simulating the panel in
one context scores at its own solo floor (0.62 measured, vs 0.75 for separate agents): shared context
means shared blind spots. The ORCHESTRATOR dispatches and adjudicates; it scores nothing. Without
parallel agents, run each role as its own fresh-context session and label the result "single-context (no
independence)". **Grep every verdict-driving citation** against the artifact before consensus math; one
that cannot be found verbatim discards its finding.

See [references/pipeline-phases.md](references/pipeline-phases.md) for panel prompt templates.

**If the `tribunal` skill is available, delegate Phase 5 to it** — this pattern generalized and separately
benchmarked. Pass the BUILDER's output as the artifact and the Phase-2.3 rubric (frozen before the Phase-3
draft) as the criteria; its orchestrator must not be the agent that built the skill. Otherwise run the
inline panel — a standalone install must not depend on a second skill.

### 5.3 Consensus Protocol

All scores within 1 point on every dimension → weighted average. Otherwise (a spread >= 2, or the DA
at 1) run one anonymized synthesis round on the disputed dimensions; then converged → average, 2-of-3
→ majority with the dissent logged in `work/experiments/craft-decisions.md`, **deadlock → escalate to
user**. A DA `ESCALATE: <reason>` reaches the user, never the average. Full protocol and anti-patterns:
[references/consensus-protocol.md](references/consensus-protocol.md).

### 5.4 Ship or Iterate

| Final Score | Action |
|-------------|--------|
| >= target_score AND no dimension blocked | **SHIP** -- copy skill to final location |
| >= target_score - 0.10 | **SHIP WITH CAVEATS** -- log concerns, proceed |
| < target_score - 0.10 | **ITERATE** -- feed panel feedback to autoresearch |
| Any dimension < 3/10 by majority | **BLOCK** -- address blocking concern first |

A 5.0 gate gold-standard case with Δ <= 0 caps the verdict at SHIP WITH CAVEATS until a re-run shows Δ > 0
or the panel records why that case cannot benefit.

If ITERATE: turn each member's top concern and every Devil's Advocate failure scenario into an
improvement hypothesis in `autoresearch.ideas.md`, log the panel scores and rationales in
`work/experiments/craft-decisions.md`, and return to Phase 4.

On SHIP, write `BENCHMARK.md` at the build root (the panel's final scores and verdict), then follow
[references/publishing.md](references/publishing.md) — shipping is a registration checklist, not a file
copy, and a version that disagrees with its registry entry installs the wrong thing. Prove the skill
earns its place with an uplift benchmark: [references/benchmark-standard.md](references/benchmark-standard.md).

---

## Output Structure

Each build lives in one self-contained folder, `builds/<skill-name>/`, with three zones:

```
builds/<skill-name>/
  input/                  # HUMAN: gold standards + study materials (any structure)
  work/                   # FACTORY: process artifacts (not shipped)
    manifest.yaml         #   derived gold-standard index    <- yours to correct
    research/             #   study notes and dossier
    evaluation/           #   rubric.yaml (exit criteria), evaluate.sh, judges.yaml, data-split.yaml,
                          #   plugin/ (3.6 wrapper plugin + evals/), plugin-eval.json or uplift.json (5.0)
    experiments/          #   results.tsv, autoresearch.jsonl, ideas, run.log, DESIGN.md, craft-decisions.md
    handoffs/             #   cross-session context (state.yaml, HANDOFF-*.md)
  output/<skill-name>/    # FACTORY: the publish-ready skill -- SKILL.md, plus README.md, references/,
                          #   scripts/ (never evaluation scripts) and assets/ as needed
  BENCHMARK.md            # FACTORY: final panel scores, plugin-eval delta, verdict (Phase 5)
```

`work/` is generated, but four files are the human's to correct at phase boundaries: `manifest.yaml`,
`evaluation/rubric.yaml` (the exit criteria), `evaluation/judges.yaml`, `evaluation/data-split.yaml`.
Never write a credential into any of them — `work/` is often committed inside a real project repo.

Only `output/<skill-name>/` ships; its uplift benchmark belongs **outside** the skill dir, at
`benchmarks/<skill-name>/`. To publish: [references/publishing.md](references/publishing.md).
