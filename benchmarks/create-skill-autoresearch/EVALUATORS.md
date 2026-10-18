# Evaluating the evaluators (2026-10-18)

Which way of scoring a skill should `create-skill-autoresearch` rely on — for its Phase 4 metric, its
5.0 eval gate, and its description pass? Four methods scored the same six skills on the same task.
Research behind the factory's guidance, not a release criterion. The factory states the outcome in
SKILL.md 3.4, 3.6 and 4.6 and in
[references/benchmark-standard.md](../../skills/create-skill-autoresearch/references/benchmark-standard.md#choosing-the-evaluator).

## Method

- **Task:** task-2 (purchase-order-builder), the five holdouts h1–h5 with the holdout master data
  ([`task-2/holdout/`](task-2/holdout/)).
- **Executor and judge:** Claude Haiku 5.5 (`claude-haiku-5-5`) for every agent run and every LLM grader.
- **Skills under test (6):** `ref` (the task-2 reference solver as a skill,
  [`scoring/fixtures/t2-good-skill`](scoring/fixtures/t2-good-skill)), `bad` (a generic skill,
  [`scoring/fixtures/t2-bad-skill`](scoring/fixtures/t2-bad-skill)), and four cycle-2 `j-*` task-2 builds
  chosen to span quality: `j-haiku-1`, `j-haiku-5`, `j-sonnet-3`, `j-sonnet-4`.
- **Methods:**
  - **A — answer-key scorer (ours):** [`scoring/score-task2.mjs`](scoring/score-task2.mjs), field-by-field
    against `answer-key.json`. Repeat 1 at 4 runs per case is the **ground truth**; repeat 2 measures its
    test-retest stability. A@2 is the first two runs per case (the benchmark default).
  - **B1 — `claude plugin eval`, deterministic graders:** a wrapper plugin per skill, one case per holdout
    (a `scaffold_script` copies `request.md` and the master data), `regex` graders on
    `{source: file, path: po.json}` for approval, total, the SKU list and exception codes, built from the
    answer key. `--ablation with-without --runs 3 -j 4 --model claude-haiku-5-5 --judge-model
    claude-haiku-5-5 --allow-tools Bash Write Read --scaffold --trust-plugin --no-publish --threshold 0`.
    Score = WITH mean; uplift = Δ.
  - **B2 — `claude plugin eval`, LLM grader:** the same suite with one `llm` grader per case whose body
    states the expected values.
  - **C — skill-creator eval:** an `evals/evals.json` per skill in skill-creator's schema (one eval per
    holdout, expectations phrased as checkable statements from the answer key), run headless by
    `claude -p` with skill-creator installed: with-skill and baseline runs, its grader, its benchmark
    aggregation. Score = with-skill pass rate; uplift = with − without.
- Every method ran twice per skill (48 cells), each cell in its own isolated HOME with an env allowlist,
  `DISABLE_AUTOUPDATER=1` and a timeout; all 48 finished `ok`.

## Results

| | A: answer-key scorer (ours) | B1: plugin eval, regex graders | B2: plugin eval, LLM grader | C: skill-creator eval |
|---|---|---|---|---|
| Rank agreement with truth (Spearman, 6 skills) | 1.000 (test-retest) | 0.955 | 0.955 | 1.000 |
| Separates bad from good (ref − bad) | 0.785 | 0.900 | 0.967 | **0.463** |
| Stability, mean \|repeat1 − repeat2\| | 0.012 | 0.036 | 0.022 | 0.017 |
| Reports uplift (with vs without) | no | yes | yes | yes |
| Cost per skill / wall | $0.07 / 82 s | $0.14 / 262 s | $0.16 / 285 s | $0.07 / 136 s |
| Files to author (excl. fixtures) | 0 (needs an answer key + scorer) | 40 | 25 | 1 |

Score per skill (B1, B2 and C: the mean of both repeats; A: repeat 2, against the repeat-1 truth):

| skill | truth | A | B1 | B2 | C |
|---|---|---|---|---|---|
| bad | 0.142 | 0.215 | 0.067 | 0.000 | 0.537 |
| j-haiku-1 | 0.954 | 0.954 | 0.842 | 0.700 | 0.938 |
| j-haiku-5 | 0.815 | 0.815 | 0.650 | 0.200 | 0.827 |
| j-sonnet-3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| j-sonnet-4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| ref | 1.000 | 1.000 | 0.967 | 0.967 | 1.000 |

Uplift (with − without): B1 −0.158 for `bad` and +0.41 to +0.76 for the rest; B2 0.000 and +0.20 to
+1.00; C +0.037 and +0.29 to +0.56. Every uplift-reporting method ranks Δ(ref) above Δ(bad).

## Reading

- **Our answer-key scorer is the most trustworthy and the cheapest** — when it is possible. It needs checkable outputs
  and a reference key; it measures absolute quality only, never uplift.
- **skill-creator's eval ranks skills perfectly and is as cheap as ours, with almost no setup — but it is lenient.** It
  scored the generic bad skill 0.537 (truth 0.142): its grader passes vague output. Its *uplift* still exposes the bad
  skill (Δ +0.037 vs +0.29 to +0.56 for real skills), so read its Δ, not its absolute score.
- **`claude plugin eval` with deterministic graders separates bad from good best per dollar of trust** and reports
  uplift, at twice the cost and the most authoring. It under-scores middling skills (0.650 vs truth 0.815).
- **`claude plugin eval` with an LLM grader is the harshest and the most expensive.** It separates extremes best but
  badly under-scores a middling skill (0.200 vs truth 0.815) — the 2-of-3 judge vote turns partial correctness into fail.

## What the factory does with it

- **Phase 4 loop metric:** the answer-key scorer whenever outputs are checkable (the factory's `evaluate.sh` with
  deterministic checks); otherwise skill-creator-style grading for speed, read as a ranking, not an absolute score.
- **5.0 uplift gate:** `claude plugin eval` with deterministic (`regex`/`file_exists`) graders — the only method here
  that both separates sharply and reports uplift; `llm` graders only where no deterministic check exists.
- **Description and quick iteration:** skill-creator (its trigger optimiser and cheap with/without loop).

## Limits

One task, six skills, two repeats, one model family as executor and judge. The bad skill and the reference skill anchor
the extremes; four real builds cover the middle. C's grading method varied between cells (script vs grader agent).
Ground truth is our own scorer, so A's row measures its test-retest agreement, not validity against an outside key.

## Reproduce

The comparison harness (cell driver, case generators, summariser) lives in the maintainer's private workspace and is
not published: it hard-codes local paths. Everything it reads is in this directory — the task-2 holdouts and answer
key, the two fixture skills, `scoring/score-task2.mjs` — and the four `j-*` builds are the cycle-2 task-2 outputs
(not published; see the README on transcripts). To rerun the method: build the B1/B2 wrapper plugins and the C
`evals.json` from `task-2/holdout/answer-key.json` as described above, run each method twice per skill with the
flags listed, and compare each method's scores with `node scoring/score-task2.mjs <skill> --runs 4`.
