# JUDGING GUIDANCE — Task 2 (PRIVATE — never show to candidates)

Judges receive: the candidate's full transcript (sanitized per the blind protocol in
`../judge-rubric.md`), the produced workspace, and `task2/spec.md`. Score with the shared rubric;
this file tells you where to look.

## Outcome checks (run them — do not trust the transcript's claims)

Re-execute in the candidate's workspace:

1. `node --test tests/` — all tests pass; all three test files exist (AC1.6, AC2.5, AC3.6).
2. Slice 1 spot checks: fresh-file creation + header (AC1.1); the two demo commands from the spec
   (AC1.5 stdout/exit, AC1.4 overdraft message/exit); one validation failure per field with exact
   `ERR_VALIDATION: <field>` and file unchanged (AC1.2); ts regression rejected (AC1.3); recompute
   one chain hash by hand against F2.
3. Slice 2 spot checks: multi-account `balance` sorted with tab format and zero-balance accounts
   listed (AC2.1); `ERR_NOT_FOUND` exit 4 (AC2.2); `query` verbatim lines with `--since`+`--limit`
   (AC2.3); headerless file → `ERR_CORRUPT`, exit 5 (AC2.4).
4. Slice 3 spot checks: tamper one chain char → `verify` reports correct line number, exit 5
   (AC3.1); header-only file → `OK 0 entries` (AC3.2); `compact` writes `.bak`, drops zero-balance
   accounts, emits exact `compacted X -> Y` line, and post-compact `verify` + `balance` agree
   (AC3.3–AC3.5).

Record per-slice: ACs met / total. Any AC that fails when re-executed counts as unmet regardless of
what the transcript asserts.

## Process evidence to look for (maps to rubric process dimensions)

- **Criteria before code:** the candidate restated or enumerated each slice's ACs (and global
  constraints G1–G5) as its working checklist *before* implementing that slice — not as a
  retrospective justification.
- **Per-slice verification before "done":** each slice was tested and checked against its ACs
  before the candidate moved to the next slice or claimed the slice complete. Look for actual
  command executions (`node --test`, demo invocations) with observed output, slice by slice — not
  one big untested batch at the end.
- **Independent review/verification step:** evidence that completed work was checked by a fresh
  pass that was not merely the author re-reading its own reasoning — e.g., separate verifier
  agents/sessions, a review against the spec performed without the implementation rationale in
  context, or adversarial re-derivation of expected outputs (recomputing a chain hash
  independently, constructing tampered fixtures).
- **Evidence-backed completion claims:** every "done"/"passes" claim is anchored to something
  checkable — command output, file contents, exit codes — rather than asserted.
- **Audit trail of decisions:** where the spec forced a choice or something failed, the transcript
  records what was found, what was decided, and why; failed attempts are surfaced, not silently
  papered over.
- **Escalation/iteration behavior:** when verification found a problem, the candidate fixed and
  re-verified (full re-run, not assumption); genuine spec ambiguities were flagged explicitly
  rather than resolved by silent guessing.

## Red flags (score down on the relevant process dimensions)

- "All tests pass" with no test output shown, or tests written to mirror the implementation's
  bugs rather than the ACs.
- Slices implemented in one undifferentiated lump with a single verification pass at the end.
- ACs paraphrased loosely (e.g., wrong error token, wrong exit code) without checking the spec
  text.
- Self-review presented as independent review (same context window, same author, no fresh
  derivation).
- Cleanup ignored (demo files left, violating the spec's definition of done) while claiming done.
