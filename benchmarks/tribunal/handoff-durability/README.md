# Handoff-durability eval

`recall-task/` and `build-task/` measure verification **quality**; `propagation-fidelity/`
measures whether an **operative skill** reaches the subagents. None of them looks at how
the **artifact itself** is handed over.

Tribunal `v0.0.3` made that a protocol matter: the doer materializes the artifact durably
and reports a **fetchable address**; the orchestrator hands that address — not a
working-tree path — to every verifier; and the round index plus remaining doer budget
travel in the handoff rather than living in orchestrator context. This eval is its
dedicated test.

Production provenance: on a live detached run, a doer completed its slice but left the
spec uncommitted, and the orchestrator had to notice and re-dispatch
([lqa-app#3506](https://github.com/LeadingQuality/lqa-app/pull/3506)). On a shared
filesystem that is merely fragile; under a torn-down or namespace-isolated sandbox the
verifiers would have scored a path they could not open.

## What it asserts

Given the prompts the orchestrator actually dispatched, plus the doer's report:

1. **Materialize** — the doer prompt instructs durable materialization (commit / push /
   publish) *and* reporting the artifact's address.
2. **Address reported** — the doer's report contains a fetchable address, not only
   working-tree paths.
3. **Address propagated** — every verifier prompt carries *that* address.
4. **Budget carried** — the round index and remaining budget appear in the dispatch, or
   in a durable ledger the run wrote (`.tribunal/`). The orchestrator's closing summary
   does **not** count: the whole point is that it survives the orchestrator.

The check is **deterministic** — string/structure assertions on the prompts, no LLM judge,
no network. `check.mjs`'s header records exactly what counts as an address (SHA forms,
branch/ref, URI, PR) and, deliberately, that a path never *dis*qualifies anything: real
doers report an address **and** the paths, so every assertion is presence-of-address.

```bash
node check.mjs <dispatched-dir>
```

Prints `METRIC handoff_durability=1|0` plus `doer_materialize_instruction`,
`artifact_address_reported`, `verifier_count`, `verifier_address_propagation` and
`budget_carried`; exits non-zero on any failure.

## Self-test (offline, no API)

```bash
node selftest.mjs
```

Four fixtures, and the self-test asserts **which metric** each one breaks — not merely
that it exits non-zero, since a checker that fails everything wholesale would pass a naive
exit-code test while being useless on a live run:

| fixture | breaks | story |
|---|---|---|
| `pass/` | — | committed, address propagated to both verifiers, budget carried |
| `fail-no-address/` | `doer_materialize_instruction`, `artifact_address_reported`, `verifier_address_propagation` | the **pre-`v0.0.3`** dispatch: nothing asked the doer to materialize, so the report offers only paths |
| `fail-ephemeral-path/` | `verifier_address_propagation` | the doer *did* commit, but the panel was pointed at its working tree |
| `fail-budget-not-carried/` | `budget_carried` | durable handoff, but the counters live only in orchestrator context |

Fixtures are written by the same hand as the regexes, so a green self-test is necessary
and not sufficient. Two extra hand-written captures in a deliberately different voice
(short SHAs in backticks, "iteration 2 of 3", "3 dispatches left") were run against the
checker during development; both were initially misjudged and the matcher was widened —
line-wrap tolerance, `Committed`/`pushed` anchors, round synonyms — until each landed on
the correct verdict. Repeat that exercise before trusting a new capture batch.

## Live before/after

```bash
# once: mint a headless credential (the fake HOME cannot see the keychain)
claude setup-token > .auth-token     # gitignored

# capture (arm is the ONLY variable: v002 = git show origin/main, v003 = working tree)
arms/run-arm.sh v003 sonnet v003-sonnet-1
node check.mjs runs/v003-sonnet-1/dispatched
```

`arms/run-arm.sh` copies exactly one tribunal `SKILL.md` into `$HOME/.claude/skills/tribunal`
inside an isolated fake HOME — the only placement Claude Code reliably discovers, and the
reason this is **not** done with `--append-system-prompt-file`. Containment (isolated
`HOME`/`XDG_*`/`GIT_CONFIG_GLOBAL`, `GIT_CEILING_DIRECTORIES`, `env -i` allowlist, stdin
from `/dev/null`, `perl alarm` timeout, `exit 75` on env failure) is inherited from
[`create-skill-autoresearch/arms/run-arm.sh`](../../create-skill-autoresearch/arms/run-arm.sh);
read that file's comments before changing this one. The work dir gets its own `git init`,
without which the v003 arm would fail for the wrong reason.

`extract-dispatch.mjs` recovers the dispatched prompts from the `stream-json` transcript,
so — unlike `propagation-fidelity/`, which asks the agent to save its own prompts — the
agent is never told its dispatch is under inspection. The raw transcript is kept, so a
parser fix can be replayed offline without re-spending a capture.

**Ship gate.** `v0.0.3` ships only if no cell regresses against `v0.0.2` — better or not
at all. A mixed result is a fix list, not a footnote.

## Scope — what this can and cannot show

Claude Code is synchronous with a shared filesystem, so it **cannot** demonstrate the
*consequence* of a path-based handoff: here a path usually still resolves. That is by
design. This eval measures the **form** of the handoff in the dispatched prompts, which is
host-neutral and observable on any runtime — the same reason `propagation-fidelity/` works
on prompts rather than outcomes. The consequence is evidenced separately by the field case
above, on a detached sandboxed runtime.

Both arms are tribunal-vs-tribunal (`v0.0.2` vs `v0.0.3`), not bare-vs-skill, mirroring how
`propagation-fidelity/` frames pre-`v0.0.2` against `v0.0.2`. Stated here because
[`docs/benchmarking.md`](../../../docs/benchmarking.md) requires the method be declared
rather than implied.
