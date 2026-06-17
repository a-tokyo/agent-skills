# Specification: `ledgerctl` — append-only ledger CLI, v1.0

Context: "Brookmill Tool Library", a fictional community workshop where members earn and spend
**tool tokens**. Token movements are recorded in an append-only, hash-chained plain-text ledger
file. `ledgerctl` is the only program that touches these files.

The deliverable is split into **three slices**. Each slice has its own acceptance criteria (AC) and
verification commands. A slice is complete only when all of its ACs are demonstrably met.

## Global constraints (apply to every slice)

- **G1** Node.js ≥ 20, ESM. Entry point: `ledgerctl.mjs` (executable via `node ledgerctl.mjs …`).
- **G2** No external dependencies. Only `node:` builtins (`node:fs`, `node:crypto`,
  `node:process`, etc.).
- **G3** Automated tests use the built-in runner: tests live in `tests/` and run with
  `node --test tests/`. Each slice ships with its own test file (`tests/slice1.test.mjs`, etc.).
- **G4** Determinism: all timestamps enter via the `--ts` flag (ISO-8601 UTC, e.g.
  `2026-03-01T10:00:00Z`); the program MUST NOT read the system clock. Output ordering MUST be
  deterministic (accounts sorted lexicographically wherever account lists appear).
- **G5** Errors go to **stderr** prefixed with a stable error token (see each slice); normal output
  goes to **stdout**. Exit codes: `0` success, `2` validation error, `3` overdraft, `4` not found,
  `5` corrupt ledger. Usage errors (unknown command/missing args) exit `2` with `ERR_USAGE: …`.

## Ledger file format (normative)

- **F1** Line 1 (header): exactly `tgl1`.
- **F2** Each subsequent line is one entry: `seq|ts|account|delta|memo|chain` —
  - `seq`: integer, first entry is `1`, increments by 1 per entry, no gaps;
  - `ts`: ISO-8601 UTC timestamp as provided via `--ts`; MUST be greater than or equal to the
    previous entry's `ts` (monotonic, ties allowed);
  - `account`: matches `^[a-z][a-z0-9-]{2,19}$`;
  - `delta`: signed non-zero integer, `1 ≤ |delta| ≤ 10000`, no leading zeros, optional leading `-`;
  - `memo`: 1–80 characters, MUST NOT contain `|` or newline;
  - `chain`: first 12 lowercase hex chars of `sha256(prevChain + "\n" + seq + "|" + ts + "|" +
    account + "|" + delta + "|" + memo)`, where `prevChain` is the previous entry's `chain`, or the
    literal string `tgl1` for the first entry.
- **F3** An account's **balance** is the sum of its deltas. Balances MUST never go below zero
  (overdraft rule, enforced at append time).

---

## Slice 1 — `append`

`node ledgerctl.mjs append <file> --account <a> --delta <n> --memo <m> --ts <iso>`

Acceptance criteria:

- **AC1.1** If `<file>` does not exist, it is created with the `tgl1` header before appending.
- **AC1.2** Inputs are validated per F2 (account regex, delta range/format, memo length/charset,
  `--ts` parseable ISO-8601 UTC). On violation: stderr `ERR_VALIDATION: <field>` (field is one of
  `account`, `delta`, `memo`, `ts`), exit 2, file unchanged.
- **AC1.3** `--ts` earlier than the last entry's `ts` → stderr `ERR_VALIDATION: ts`, exit 2, file
  unchanged.
- **AC1.4** A negative delta that would take the account's balance below zero → stderr
  `ERR_OVERDRAFT: <account> <currentBalance>`, exit 3, file unchanged.
- **AC1.5** On success: entry appended with correct `seq` and `chain` per F2; stdout is exactly the
  new entry's `seq` followed by a newline; exit 0.
- **AC1.6** `tests/slice1.test.mjs` covers: file creation, happy-path chain correctness (recompute
  the hash in the test), each validation field, ts monotonicity, and the overdraft rejection — and
  passes under `node --test tests/`.

Verification commands (must behave as stated):

```
node ledgerctl.mjs append demo.tgl --account ada --delta 5 --memo seed --ts 2026-03-01T10:00:00Z   # stdout "1", exit 0
node ledgerctl.mjs append demo.tgl --account ada --delta -9 --memo spend --ts 2026-03-01T11:00:00Z  # ERR_OVERDRAFT: ada 5, exit 3
node --test tests/
```

## Slice 2 — `balance` and `query`

`node ledgerctl.mjs balance <file> [--account <a>]`
`node ledgerctl.mjs query <file> --account <a> [--since <seq>] [--limit <n>]`

Acceptance criteria:

- **AC2.1** `balance` with no `--account`: one line per account that appears in the ledger,
  sorted lexicographically, formatted `<account>\t<balance>`; exit 0. Accounts with balance 0 are
  still listed.
- **AC2.2** `balance --account <a>`: that account's balance only (same line format). If the account
  never appears in the ledger: stderr `ERR_NOT_FOUND: <a>`, exit 4.
- **AC2.3** `query` prints matching entry lines verbatim (exact raw lines, original order). With
  `--since <seq>`, only entries with `seq >= <seq>`; with `--limit <n>`, at most the first `n`
  matches. No matches → empty stdout, exit 0. Unknown account → empty stdout, exit 0 (query is a
  filter, not a lookup).
- **AC2.4** Both commands fail with `ERR_CORRUPT: …` and exit 5 if the file is missing the `tgl1`
  header. (Full chain verification is Slice 3; only the header check is required here.)
- **AC2.5** `tests/slice2.test.mjs` covers: multi-account balances and sorting, zero-balance
  listing, `ERR_NOT_FOUND`, `--since`/`--limit` combinations, and the header check — and passes
  under `node --test tests/`.

## Slice 3 — `verify` and `compact`

`node ledgerctl.mjs verify <file>`
`node ledgerctl.mjs compact <file> --ts <iso>`

Acceptance criteria:

- **AC3.1** `verify` checks, in order: header (F1); per-line field validity (F2); seq contiguity
  from 1; ts monotonicity; chain hash of every entry (F2). First failure: stderr
  `ERR_CORRUPT: line <lineNumber>: <reason>`, exit 5. (`<reason>` is free text; `<lineNumber>` is
  the 1-based line number in the file.)
- **AC3.2** `verify` on a valid ledger: stdout `OK <entryCount> entries`, exit 0. A header-only
  file is valid with 0 entries.
- **AC3.3** `compact` first verifies; on corruption it behaves exactly like AC3.1 (same stderr,
  exit 5) and changes nothing.
- **AC3.4** `compact` on a valid ledger: writes the original file to `<file>.bak` (overwriting any
  existing `.bak`), then rewrites `<file>` as: header, then one entry per account with **non-zero**
  balance, accounts sorted lexicographically, `seq` starting at 1, `ts` = the `--ts` value,
  `delta` = the account's balance, `memo` = `carryover`, chain recomputed per F2. Zero-balance
  accounts are dropped.
- **AC3.5** After `compact`: `verify` passes, and `balance` output is identical to the
  pre-compact `balance` output minus zero-balance accounts. stdout of `compact` is exactly
  `compacted <entriesBefore> -> <entriesAfter>`, exit 0.
- **AC3.6** `tests/slice3.test.mjs` covers: corruption detection (bad chain, bad seq, bad field,
  missing header — one tampered fixture each), header-only verify, compact happy path including
  `.bak` contents and post-compact verify, and zero-balance dropping — and passes under
  `node --test tests/`.

---

## Definition of done (whole deliverable)

- All ACs of all three slices met; `node --test tests/` passes with all three test files present.
- No constraint in G1–G5 violated.
- Working files for the demo commands cleaned up (committed fixtures live under `tests/` only).
