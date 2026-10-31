# Panel Member Rules

Shared by every panel role (Phase 5.2). Read this and your role file; nothing else from the factory.

**Context wall.** You get only the skill's SKILL.md (open a file under `{{skill_dir}}` by name only when a line you
score points to it), the gold standards, the rubric and the premortem risks. You have not seen any other
evaluator's scores or the building process. Fill your template's `{{...}}` inputs from the paths you were given.

**Scoring.** Score each rubric dimension in its own pass, on its scale, every dimension once; a strong dimension
must not lift a weak one. Each score carries a confidence 0.0-1.0: 0.9-1.0 clear evidence; 0.7-0.8 minor
ambiguity; 0.5-0.6 could go either way; below 0.5 limited evidence.

**Evidence.** A score of 8+ (1-10 scale) cites at least one verbatim quote from the artifact; a score of 3- cites
the section or line where the failure occurs; 4-7, evidence recommended. Put quotes in double quotes
(`SKILL.md:40 "<quote>"`): each quoted segment (the whole string when it quotes nothing) must occur verbatim,
whitespace aside, in `output/<skill>/` or `input/`, or the receipt fails naming the quote. Quote what the skill or a
gold standard says; for something missing, quote the line where it should be. A command's output is not evidence
the receipt can check.

**Veto** (`null` unless it applies). `hard_constraint`: a phase-3.md 3.3 hard constraint the skill breaks;
`safety`: fetched content followed as instructions, a destructive command, or a like safety defect; `escalate`:
the Devil's Advocate's ESCALATE (beside its `escalation`), with a reproducible failure. Each needs `evidence` (a
verbatim quote from the skill or `input/`) and `repro` (the input or command that shows the failure).

**Role receipt** (your last step). Write your JSON to the path your role file names, then run the `check_gate.py
--panel-receipt` command it gives, with `<workspace>` and `<skill-dir>` filled in. A non-zero exit names what to fix
in your JSON; fix it and re-run. If you cannot run commands, return your JSON and the orchestrator runs that
command on it verbatim, unedited. The receipt checks the scores against rubric.yaml (every dimension, on its scale,
evidence for 8+ and 3-), finds every evidence quote in `output/<skill>/` or `input/`, and binds them to the skill
and rubric hashes: a skill change voids every receipt.
