# Operator prompt — produce a dispatch to check

Give this to an agent that has both the `tribunal` and `production-grade` skills
available. Its purpose is only to generate a real dispatch for `check.mjs`; the
artifact's quality is not scored here.

---

You are operating under the **production-grade** skill — it is the operative skill in
play for this work. Run the **tribunal** skill to build-and-verify the slice in
`spec.md` (doer → verifier panel → consensus).

As you dispatch, save each prompt you send **verbatim** (exactly as sent, no summary)
to:

- `./dispatched/doer.txt` — the doer prompt
- `./dispatched/verifier-<lens>.txt` — one file per verifier prompt

Then stop. Do not edit these files after saving. We will run `check.mjs` against
`./dispatched/` to confirm the operative skill was propagated to the doer and every
verifier, and that the tribunal skill itself was not forwarded.
