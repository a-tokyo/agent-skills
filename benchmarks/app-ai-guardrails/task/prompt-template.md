Task prompt template — benchmark harness (ticket 10)

Verbatim from research/00-synthesis.md §e (Q9). One template, one `{{STACK}}` slot, phrased
for comparable bare-arm difficulty and **zero probe leak** (no gate names, thresholds, tool
names, "guardrails", "sonar", "coverage %", or "verified green"). Both arms (bare and
with-skill) get the byte-identical rendered prompt; the scorer (`check-guardrails.mjs`) runs
against whatever each arm produces. Do not edit the prose below without re-checking research/00
§e — it is the rubric's designed-neutral surface, not free text.

---

Create a new production-ready {{STACK}} application in the current directory. Implement one
small feature exposing at least one endpoint (or page) with real conditional logic — for
example an input-validation or classification handler that responds differently to valid vs
invalid input. Set the project up to the standard you would expect for a codebase a team will
maintain long-term and hand off to other engineers and to CI: it should be well-tested and in a
clean, working, committed state when you finish.
