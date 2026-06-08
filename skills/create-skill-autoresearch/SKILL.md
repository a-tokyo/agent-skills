---
name: create-skill-autoresearch
version: 0.0.1
license: MIT
description: >-
  Factory skill that creates production-grade, benchmarked, autonomously improved,
  and verified agent skills. Orchestrates a 5-phase pipeline: interview the
  user to discover purpose and gold standards, research domain materials with parallel
  subagents, draft the skill with a design-first approach, invoke autoresearch to
  iterate against gold-standard-driven LLM-as-judge evaluation, and verify quality
  through multi-agent consensus with a devil's advocate. Use when building a new
  skill, creating a skill from existing materials, or upgrading a skill to
  production quality with benchmarking and autonomous improvement.
---

# Create Skill via Autoresearch Factory

A factory for forging production-grade agent skills through gold-standard-driven autoresearch, multi-agent verification, and structured consensus.

The factory orchestrates 4 agent roles through 5 phases:

| Phase | What Happens | Agent Role |
|-------|-------------|------------|
| 1. Interview | Discover purpose, gold standards, scope | ORCHESTRATOR |
| 2. Research | Study domain materials, build dossier, propose rubric | RESEARCHER (N parallel) |
| 3. Draft | Design structure, generate SKILL.md, measure baseline | BUILDER |
| 4. Autoresearch | Iterate skill against gold standards (LLM-as-judge, or an objective real-world metric for procedural skills — see 3.4) | BUILDER + autoresearch skill |
| 5. Verify | Premortem, panel scoring, consensus, ship/iterate | PANEL (3 subagents) |

Key constraint: BUILDER and PANEL never share context. Panel receives only the skill output, gold standards, and rubric -- no bias from the building process.

## Relation to create-skill

This factory **extends** the official single-pass skill creators (Anthropic's Skills best-practices and `skill-creator`; Cursor's `create-skill`) rather than replacing them. It adds what a one-shot generator cannot: a research dossier, gold-standard benchmarking, an autonomous improvement loop, and independent multi-agent verification. The skills it produces follow the same official conventions -- see [references/skill-authoring-best-practices.md](references/skill-authoring-best-practices.md).

## Companion skills

The factory orchestrates these sibling skills at runtime: **autoresearch** (Phase 4 improvement loop), **premortem** (Phase 5 risk pass), and **handoff** (cross-session continuity); the Phase 5 panel/consensus design draws on **llm-council**. In this harness they are vendored under `.agents/skills/`. If you install this skill standalone, install those alongside it.

---

## Phase 1: Interview

Discover what the user needs through structured questions. Do not assume -- ask.

### 1.1 Purpose and Domain

> **What skill do you want to build? What problem does it solve?**
>
> Describe the domain, the target user (which agent will use this skill),
> and what "success" looks like when the skill is used correctly.

Record: `SKILL_PURPOSE`, `DOMAIN`, `TARGET_USER`, `SUCCESS_CRITERIA`.

### 1.2 Gold Standards

> **Do you have examples of "what good looks like"?**
>
> Gold standards are the benchmark. They can be:
> - **Input/output pairs**: given this input, the skill should produce output like this
> - **Reference artifacts**: existing documents, code, or outputs that represent ideal quality
> - **Previously solved problems**: tasks that were completed successfully by humans
> - **Quality reports**: existing evaluations or benchmarks
>
> Where are they? What format are they in? How many do you have?

Record: `GOLD_STANDARD_SOURCE`, `GOLD_STANDARD_FORMAT`, `GOLD_STANDARD_COUNT`.

Minimum: 3 gold standards. Fewer than 3 is a risk -- warn the user and suggest alternatives (create synthetic examples, find additional reference materials).

### 1.3 Study Materials

> **What materials should I study to understand this domain?**
>
> Examples: documentation, existing code, transcripts, design docs,
> reference implementations, specifications, style guides.

Record: `STUDY_MATERIALS` (list of paths/URLs).

### 1.4 Scope and Constraints

> **Any constraints on the skill itself?**
>
> - Must it follow specific conventions? (e.g., existing team patterns)
> - Are there skills it should integrate with?
> - Any anti-patterns to avoid?
> - Target line count? (default: < 500 lines per create-skill conventions)

Record: `CONSTRAINTS`, `INTEGRATION_SKILLS`, `ANTI_PATTERNS`.

### 1.5 Existing Skill Check

> **Is there an existing skill for this domain that we're upgrading?**
>
> If yes, that skill becomes a study material AND a baseline. The factory will
> research it, measure it against the rubric, then improve it -- not start from scratch.

If an existing skill is found, record `EXISTING_SKILL` path and set the factory mode to **upgrade** (baseline from existing) rather than **greenfield** (baseline from scratch).

### 1.6 Confirm and Create Workspace

Summarize all parameters in a table. Ask the user to confirm.

Once confirmed, create the build workspace at `builds/<skill-name>/` with three ownership zones:
- `input/` -- where the user drops gold standards + study materials, in **any** structure
- `work/` -- everything the factory generates: `manifest.yaml`, `research/`, `evaluation/`, `experiments/`, `handoffs/`
- `output/<skill-name>/` -- the finished skill (`SKILL.md` + `references/`) in its own named dir, publish-ready

Do **not** ask the user to hand-author a manifest. Scan whatever is in `input/`, classify each item as a gold standard (exemplar input/output pair or reference artifact) vs a study material, and write your derived index to `work/manifest.yaml` with train/validation/test tags. Present the derived manifest for the user to confirm or correct. See [references/pipeline-phases.md](references/pipeline-phases.md) for intake formats and the manifest schema.

---

## Phase 2: Research

Study the domain thoroughly before writing any skill code.

### 2.1 Spawn Researcher Subagents

Cluster study materials by relatedness, then launch one `explore` subagent per cluster. Clustering heuristic:
- **By source type**: existing skills in one cluster, gold standard outputs in another, planning docs in a third
- **By subtopic**: if materials cover distinct areas (e.g., backend vs frontend), split by area
- **Cap at 5-7 clusters**: more than 7 creates synthesis overhead without proportional depth gain
- **Minimum 2 clusters**: a single cluster means no parallelism benefit

Each subagent:
1. Reads the assigned material deeply
2. Distills findings into a research note in `work/research/`
3. Identifies patterns, conventions, and quality signals relevant to the skill

Naming: `work/research/01-<topic>.md`, `work/research/02-<topic>.md`, etc.

### 2.2 Synthesize Research

After all researchers complete, synthesize findings into `work/research/00-synthesis.md`:
- Cross-cutting patterns
- Key conventions the skill must follow
- Quality signals that distinguish good from bad output
- Potential rubric dimensions

### 2.3 Propose Rubric

Based on research, draft `work/evaluation/rubric.yaml`:

```yaml
name: <skill-name>-rubric
dimensions:
  - name: <dimension>
    weight: <0.0-1.0>
    scale: "1-10"
    criteria: "<what this dimension measures>"
  # ... 5-10 dimensions
target_score: 0.85
max_iterations: 20
plateau_window: 5
```

Always include these universal dimensions (adjust weights per domain):
- **correctness**: Instructions are technically accurate and executable
- **completeness**: All necessary sections and edge cases covered
- **clarity**: A naive agent can follow without ambiguity
- **consistency**: Aligns with existing codebase conventions

Add 3-6 domain-specific dimensions from the research synthesis.

Present the rubric to the user for review. Iterate until confirmed.

See [references/rubric-templates.md](references/rubric-templates.md) for templates.

---

## Phase 3: Draft

Design before writing. Write before measuring.

### 3.1 Design Document

Create `work/experiments/DESIGN.md` with:
- Skill name and description (following create-skill conventions)
- Structural decisions: section count, reference file split, progressive disclosure plan
- Integration points with other skills
- Key terminology and voice decisions

### 3.2 Grill the Design

Before writing any skill code, challenge the design adversarially:
- What would make this skill fail in practice?
- Are the structural decisions justified or assumed?
- Does the design match what the gold standards demonstrate?
- Are there simpler alternatives?

Present concerns to the user. Iterate until the design survives scrutiny.

### 3.3 Generate SKILL.md Draft

Following the design and the official skill-authoring rules (see [references/skill-authoring-best-practices.md](references/skill-authoring-best-practices.md)), run this **pre-flight checklist** before writing -- these are hard constraints, not preferences:
- `name`: <= 64 chars, lowercase/numbers/hyphens only, **no reserved words `anthropic`/`claude`**, gerund form preferred
- `description`: <= 1024 chars, **third person**, states both WHAT it does and WHEN to use it
- Body < 500 lines; progressive disclosure (essentials in SKILL.md, detail in `references/`)
- File references **one level deep** only; a table of contents for any reference file > 100 lines
- Concrete examples over abstract instructions; consistent terminology; forward-slash paths

Write the draft to `output/<skill-name>/SKILL.md` (reference files in `output/<skill-name>/references/`).

### 3.4 Build Evaluation Script

Create `work/evaluation/evaluate.sh` that:
1. Takes a gold standard test case path as argument
2. Extracts the input from the test case
3. Invokes the skill on the input -- since skills are markdown instructions (not
   executables), this means calling an LLM with the SKILL.md as a system prompt
   and the test case input as the user message. Use `curl` to an OpenAI-compatible
   API, or a language-specific SDK. Capture the LLM's output.
4. Compares the output to the gold standard reference using an LLM-as-judge
5. Emits `METRIC <dimension>=<score>` lines to stdout
6. Emits `METRIC overall_score=<weighted_average>` as the primary metric

See `self-test/evaluation/evaluate.sh` for a reference implementation.

The LLM judge should:
- Use structured JSON output for per-dimension scoring
- Score each dimension independently (prevent halo effects)
- Require evidence (verbatim quotes) for extreme scores
- Use a different model family from the builder when possible

**Deterministic vs LLM-judge evaluation**: Not every dimension needs an LLM judge. Prefer deterministic checks where possible:
- Line count, frontmatter validation, link integrity → shell/grep checks
- Pattern coverage (does output mention X?) → regex matching
- Structural conformance → programmatic validation

Use LLM-as-judge only for dimensions that require subjective judgment (clarity, quality match, curation). Mix both in `evaluate.sh`: deterministic checks emit METRIC lines directly, LLM judges handle the rest. If no LLM API is available, fall back to deterministic-only scoring and log a warning.

**Procedural / agentic skills (prefer this when it applies)**: Some skills don't *generate* an artifact in one shot — they instruct an agent to *perform a multi-step task on a real artifact* (migrate a framework version, refactor a module, scaffold infra, rebuild a repo). For these, the single-call "SKILL.md as system prompt + input as user message" model in 3.4 is the wrong harness. Evaluate them by **execution against a real artifact with an objective real-world metric** instead:

1. **Pick a real test repo** where "correct" has a ground-truth signal — ideally one where a correct application is a *no-op against a captured baseline*. (Example: a Tailwind v3→v4 migration should be visually identical, so committed golden screenshots become the gold standard; the metric is pixel-parity + `build`/`lint`/`typecheck`/tests pass + a static "residual v3 markers = 0" grep.)
2. **`evaluate.sh` orchestrates: reset → fresh-agent applies the skill → measure.** Reset the repo to the captured baseline; spawn a **fresh subagent** told to perform the task following *only* the skill under test (no other guides, no builder context); then run deterministic real-world checks on the result and emit `METRIC` lines. The artifact's own ground truth replaces the LLM judge — cheaper, deterministic, and far stronger signal than judging prose.
3. **Fresh agent every run is the point.** It measures skill *self-sufficiency*, not the builder's accumulated context. A clean reset between runs (e.g. `git reset --hard <baseline> && git clean -fd` + reinstall) is mandatory or scores drift. Vary the executor model (e.g. a smaller model) as a robustness check — if a smaller model + the skill still hits the target, the skill is robust.
4. **Capture the baseline before drafting.** On the unmodified repo, set up the metric (golden snapshots / test suite), confirm it's deterministic (run it twice, expect identical), and confirm the *unmigrated* state scores 0 so you know the gate discriminates.

This makes the codemod/tool-first pattern natural too: have the skill run any deterministic tool (a codemod, a formatter, a generator) for the mechanical bulk first, and reserve the skill's prose for the judgment the tool can't do — the real-world metric then verifies the whole.

For **multi-judge evaluation** (recommended when budget allows):
- Run 2-3 different LLM models as judges on the same output
- Average their per-dimension scores for a more robust signal
- Track per-judge variance -- high variance on a dimension indicates the criteria may be ambiguous
- Configure judges in `work/evaluation/judges.yaml`:
  ```yaml
  judges:
    - model: "<model-1>"
      weight: 1.0
    - model: "<model-2>"
      weight: 1.0
  aggregation: "mean"
  ```

Optionally create `work/evaluation/evaluate-checks.sh` for correctness gates.

### 3.5 Measure Baseline

Run `evaluate.sh` on the test cases with the initial draft.
Record baseline scores. This is experiment 0.

Report to the user:
> Baseline established: **overall_score = [value]**
> Dimensions: [per-dimension breakdown]

---

## Phase 4: Autoresearch

Invoke the **autoresearch skill** to iterate the skill draft against the evaluation rubric.

### 4.1 Configure Autoresearch

Provide these parameters to the autoresearch skill. All paths are relative to the
build workspace root (`builds/<skill-name>/`), which is the autoresearch
working directory. Autoresearch session files (`.md`, `.jsonl`, `.tsv`, `run.log`)
are created at the workspace root during the active session, then archived to
`work/experiments/` when the session ends or on handoff.

- **Goal**: Improve `<skill-name>` quality as measured by `overall_score` (LLM-as-judge against gold standards, or the objective real-world metric for procedural skills — see 3.4)
- **Metric command**: `./work/evaluation/evaluate.sh` (relative to workspace root)
- **Primary metric**: `overall_score`
- **Direction**: `higher_is_better`
- **In-scope files**: `output/<skill-name>/SKILL.md`, `output/<skill-name>/references/*`
- **Out-of-scope files**: `input/`, `work/`
- **Constraints**: Must follow the official skill-authoring rules (< 500 lines, frontmatter format -- see 3.3)
- **Budget**: From rubric config `max_iterations` (default 20)
- **Checks**: If `work/evaluation/evaluate-checks.sh` exists, create `autoresearch.checks.sh` at workspace root that calls it (autoresearch skill expects this name)

### 4.2 Data Split

If gold standards count >= 10:
- **70% training**: Used during each autoresearch experiment
- **20% validation**: Checked adaptively to detect overfitting (see below)
- **10% test**: Held out entirely until Phase 5 verification

If gold standards count 3-9:
- **Leave-one-out rotation**: Each experiment evaluates against all but one, rotating which is held out

Record the split in `work/evaluation/data-split.yaml`.

**Cost awareness for large sets (100+ gold standards)**: Each LLM-as-judge call costs real money. With 70 training cases at ~$0.50/call, that's ~$35/experiment. Mitigate with a sampling strategy: evaluate against a random sample of training cases per experiment (e.g., 10-15), rotating the sample. Run the full training set only when validating kept experiments or at phase boundaries.

**Overfitting detection**: Run `evaluate.sh` against the validation set (not just training) adaptively:
- After every **kept** experiment (improvements are when overfitting risk changes)
- After a **plateau** is detected (to check if the ceiling is real or just training-specific)
- When training score crosses a **milestone** (e.g., jumps by > 0.05 in a single experiment)

If training score improves but validation score drops by more than 0.05, flag overfitting:

> **Overfitting warning**: Training score [X] improving but validation score [Y] declining.
> Consider: generalizing recent changes, reverting to last validation-stable commit, or reviewing if rubric criteria are too narrow.

Log validation checks in `autoresearch.jsonl` with `"type": "validation_check"`.

**Overfitting detection for leave-one-out** (< 10 gold standards): Since there is no fixed validation set, track per-case score variance. If variance across cases increases while the mean improves, the skill is specializing for some cases at the cost of others. Flag when any single case drops > 1.0 point while others improve.

### 4.3 Let Autoresearch Run

The autoresearch skill handles the loop:
- THINK-EDIT-COMMIT-RUN-MEASURE-DECIDE-LOG cycle (commit-first git model)
- METRIC protocol for measurement
- ASI fields for structured memory
- Plateau detection
- Results logging to `autoresearch.jsonl` and `results.tsv`

The factory adds to the autoresearch ideas backlog (`autoresearch.ideas.md`):
- Ideas from research synthesis
- Per-dimension improvement strategies from the rubric
- Patterns observed in gold standards that aren't yet reflected in the skill

### 4.4 Monitor and Handoff

If the autoresearch session exceeds context limits or the experiment budget:
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
   top_concerns:             # panel feedback or known weaknesses
     - <concern 1>
     - <concern 2>
   blocked_dimensions: []    # dimensions below threshold
   last_updated: <ISO timestamp>
   ```
3. The next session reads `state.yaml` to resume from the correct phase

### 4.5 Resume Protocol

When the factory detects `work/handoffs/state.yaml` exists:

1. Read `state.yaml` to determine current phase
2. Read the most recent `work/handoffs/HANDOFF-*.md` for rich context
3. Resume from the recorded phase:
   - **interview**: Re-confirm parameters with user (may have changed)
   - **research**: Check if dossier is complete, synthesize if needed
   - **draft**: Check if SKILL.md draft exists, measure baseline if needed
   - **autoresearch**: Read `autoresearch.jsonl` for ASI history, continue loop with remaining budget
   - **verify**: Re-run panel if previous verification returned ITERATE
4. Update `state.yaml` with new session number

---

## Phase 5: Verify

Independent verification by agents that did NOT participate in building. The context wall between BUILDER and PANEL is critical -- it prevents bias from the building process.

### 5.1 Premortem

Invoke the **premortem skill** on the skill artifact. Feed identified risks into the panel evaluation as additional test scenarios.

### 5.2 Panel Evaluation

Spawn 3 independent verifier subagents **in parallel**. Each receives ONLY:
- The skill SKILL.md and references
- The gold standards
- The rubric
- The premortem risks

They do NOT receive: research notes, experiment logs, builder context, or ASI.

**Panel roles:**

| Role | Focus | Bias |
|------|-------|------|
| Verifier-A (Quality) | Correctness, completeness, clarity, spec adherence | Neutral |
| Verifier-B (Utility) | Real-world usability, edge cases, developer experience | Neutral |
| Devil's Advocate | Failure modes, hidden assumptions, missing constraints | Explicitly adversarial |

Each panel member scores every rubric dimension independently with:
- Score (per rubric scale)
- Confidence (0.0-1.0)
- Evidence (verbatim quote from artifact)

Use a **different model family** for the panel when possible (e.g., if the builder used one model, use a different one for verifiers).

See [references/pipeline-phases.md](references/pipeline-phases.md) for panel prompt templates.

### 5.3 Consensus Protocol

After collecting all 3 scoring outputs:

1. **Agreement check**: All scores within 1 point on every dimension → consensus reached, take weighted average
2. **Synthesis round**: If any dimension has spread >= 2, or DA scores any dimension at 1:
   - Each member writes a rationale on disputed dimensions (max 500 words)
   - Rationales are **anonymized** and shared simultaneously
   - Members may revise disputed scores with written justification
   - Members who maintain their score must rebut the strongest opposing argument
3. **Resolution**: After synthesis, test convergence:
   - Converged (within 1 point) → weighted average
   - Majority (2-of-3 agree) → majority score adopted, dissent logged as minority report in `work/experiments/craft-decisions.md`
   - Deadlock → escalate to user
4. **DA escalation**: DA may write `ESCALATE: <reason>` for critical concerns the majority dismisses. This surfaces the concern to the user.

See [references/consensus-protocol.md](references/consensus-protocol.md) for the full protocol, anti-patterns, and research basis.

### 5.4 Ship or Iterate

| Final Score | Action |
|-------------|--------|
| >= target_score AND no dimension blocked | **SHIP** -- copy skill to final location |
| >= target_score - 0.10 | **SHIP WITH CAVEATS** -- log concerns, proceed |
| < target_score - 0.10 | **ITERATE** -- feed panel feedback to autoresearch |
| Any dimension < 3/10 by majority | **BLOCK** -- address blocking concern first |

If ITERATE:
1. Extract top concerns from each panel member
2. Extract failure scenarios from the Devil's Advocate
3. Add specific improvement hypotheses to `autoresearch.ideas.md`
4. Log panel scores and rationales in `work/experiments/craft-decisions.md`
5. Return to Phase 4 with structured feedback

---

## Output Structure

Each build lives in one self-contained folder, `builds/<skill-name>/`, with three zones:

```
builds/<skill-name>/
  input/                  # HUMAN: gold standards + study materials (any structure)
  work/                   # FACTORY: process artifacts (not shipped)
    manifest.yaml         #   derived gold-standard index
    research/             #   study notes and dossier
    evaluation/           #   rubric.yaml, evaluate.sh, judges.yaml, data-split.yaml
    experiments/          #   results.tsv, autoresearch.jsonl, run.log, DESIGN.md, craft-decisions.md
    handoffs/             #   cross-session context
  output/                 # FACTORY: the finished, publish-ready skill
    <skill-name>/         #   the skill in its own named dir
      SKILL.md
      references/         #   if needed
      scripts/            #   if needed (NOT evaluation scripts)
      assets/             #   if needed
```

Only `output/` ships. To publish: copy `builds/<skill-name>/output/<skill-name>/` straight into a skills repo's `skills/` directory.

---

## Handoff Rules

Write a handoff when any of these occur:
- Context window approaching limit (high turn count)
- Experiment budget for current session exhausted
- Phase transition (research → draft, draft → autoresearch, etc.)
- User explicitly requests

Each handoff produces:
1. `work/handoffs/state.yaml` -- structured state for automatic resume
2. `work/handoffs/HANDOFF-<label>.md` -- rich context for human readability

To resume: read `state.yaml`, determine current phase, load relevant context, continue.
