# Phases 1-2: Interview and Research

## 1.1-1.5 Interview

Ask the user directly, one topic at a time, and record each answer; gather all before 1.6.

1. **Purpose**: the problem, the domain, the user (an agent, a CLI, a human), what success looks like.
2. **Gold standards**: examples of good output (input/output pairs, reference artifacts, solved tasks, quality
   reports): where, what format, how many. None: reference materials or expert outputs; fewer than 3: create
   synthetic ones together, or find more.
3. **Study materials**: docs, code, transcripts, specs, style guides, existing skills, external references.
4. **Constraints**: conventions, skills to integrate with, anti-patterns, length (default < 500 lines), and
   `INVOCATION_MODE`: model-invoked (fires on its own; its description costs context every turn) or user-invoked
   (`disable-model-invocation: true`; the user must remember it). It sets the 3.3 description style.
5. **Existing skill** for this domain: then it is a study material and the baseline, mode **upgrade**.

## 1.6 Confirm and Create Workspace

Present the parameters as a table and wait for explicit confirmation. Then create the workspace:

`builds/<skill-name>/` holds `input/` (the human's gold standards and study materials, any structure), `work/`
(`manifest.yaml`, `research/`, `evaluation/`, `pids/`, `experiments/`, `handoffs/`; never shipped),
`output/<skill-name>/` (the publish-ready skill, never evaluation scripts) and `BENCHMARK.md` (Phase 5). A working directory already dedicated to the build (it holds the brief and inputs) is the workspace: create `work/`
and `output/<skill-name>/` in place. The workspace is its own git repository (the Phase 4 loop resets it): `git init`
unless `git rev-parse --show-toplevel` already prints its root; inside a parent project, gitignore it there. `work/`
is committed except `work/pids/` (`run_owned.py` ignores it), so no credential goes in it. The human may correct
`work/manifest.yaml`, `evaluation/rubric.yaml`, `evaluation/judges.yaml` and `evaluation/data-split.yaml` (`gate.py
record` owns `plugin_eval`) at phase boundaries. Only `output/<skill-name>/` ships; its uplift benchmark belongs
outside it, at `benchmarks/<skill-name>/`.

Never ask the user to hand-author the manifest: scan `input/`, classify each item as a gold standard (an exemplar
input/output pair or reference artifact) or a study material, write `work/manifest.yaml`, and present it to confirm:

```yaml
pairs:
  - { input: "input-01.md", reference: "output-01.md", tags: ["training"] }   # training | validation | test
```

## 2.1 Research the Study Materials

When the study materials total under 40 KB (`ls -l`), read them yourself and write the notes directly. Otherwise cluster them by source type or subtopic, 2-7 clusters, and launch one read-only
researcher subagent per cluster with this prompt:

```
Study these materials and write a research note to work/research/<NN>-<topic>.md: <files>
Cover: 1) patterns and conventions, 2) quality signals (what separates good from bad output), 3) domain terms,
4) candidate rubric dimensions (measurable), 5) anti-patterns and common mistakes. Read every file; quote examples.
```

## 2.2 Synthesize Research

After all researchers finish, write `work/research/00-synthesis.md`: cross-cutting patterns, quality signals,
proposed rubric dimensions, conventions to follow, anti-patterns, key references, and a **conflicts** table (every
point where a study material contradicts the gold standards, with the gold evidence). **The gold standards win**;
Phase 3 confirms the draft follows the gold side of every row before the 3.5 baseline.

## 2.3 Propose Rubric

Write `work/evaluation/rubric.yaml`:

```yaml
name: <skill-name>-rubric
dimensions:                 # 5-10; weights sum to 1.0
  - { name: correctness, weight: 0.20, scale: "1-10", criteria: "<falsifiable statement>" }
target_score: 0.85          # overall_score = sum(dimension_score / scale_max * weight)
max_iterations: 20          # experiment budget
plateau_window: 2           # experiments within noise that end the loop
metric: llm-judge           # or answer-key (3.4)
```

Always include correctness (0.20: accurate, executable), completeness (0.15: sections and edge cases), clarity (0.15:
a naive agent follows it unambiguously), consistency (0.10: matches existing conventions) and predictability (0.10:
the same process every run, checkable and exhaustive completion criteria, no vague gates, no no-op lines). Add 3-5
domain dimensions from the synthesis, sharing the remaining 0.30, each criterion a falsifiable statement ("every gold
pattern appears", not "well written"). Present the rubric; iterate until the user confirms it.
