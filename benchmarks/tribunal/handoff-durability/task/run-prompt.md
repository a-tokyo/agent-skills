# Operator prompt — produce a dispatch to check

`arms/run-arm.sh` sends the text below (everything after the rule) to a fresh `claude -p`
session that has exactly one version of the `tribunal` skill installed. Its purpose is
only to generate a real dispatch for `check.mjs`; the slice's quality is not scored.

The prompt deliberately says **nothing** about addresses, commits, or budgets. Whether
those appear in the dispatched prompts is precisely what is being measured, so any hint
here would contaminate both arms. It also does not ask the agent to save its own prompts
— `extract-dispatch.mjs` recovers them from the `stream-json` transcript, which keeps the
agent unaware that the dispatch itself is under inspection.

---

You are in a git repository containing `spec.md`. Run the **tribunal** skill to
build-and-verify the slice described in `spec.md` (doer → verifier panel → consensus).

Dispatch a separate doer and a separate verifier panel as subagents — you are the
orchestrator: you do not implement and you do not score. Adjudicate to a verdict and
report it.
