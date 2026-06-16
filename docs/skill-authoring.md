# Skill authoring

How skills in this repo are built. The **source of truth** for the platform rules is Anthropic's
documentation — read it for current detail; this file carries only a quick-reference mirror plus the
conventions specific to this repo.

## Source of truth (read these)

- **Best practices** — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
  — conciseness, progressive disclosure, degrees of freedom, evaluation-driven development.
- **API skills guide** — https://platform.claude.com/docs/en/build-with-claude/skills-guide
  — packaging, the `container` parameter, beta headers, the execution sandbox.

Authoring questions resolve *there*, not here. The limits below are a convenience snapshot — if they
disagree with the guide, the guide wins.

## Hard limits (mirror — confirm at source)

| What | Limit |
|------|-------|
| `SKILL.md` body | < 500 lines |
| `name` | ≤ 64 chars, `[a-z0-9-]` only, no XML tags, no reserved words (`anthropic`, `claude`) |
| `description` | ≤ 1024 chars, non-empty, third person, says *what* it does **and** *when* to use it |
| Skill upload (all files) | ≤ 30 MB |
| Reference file with a TOC | required when the file is > 100 lines |
| Reference depth | one level deep from `SKILL.md` (no reference that only links to another reference) |

`name`, `description`, and upload size are enforced by Anthropic at upload; the body-line count is a
performance guideline (Anthropic's wording is "body under 500 lines"). The check below counts the
whole file — frontmatter adds only a few lines — as a conservative proxy. The table is an early
warning, not the authority.

## Check current compliance

`name` and `description` use folded YAML (`description: >-`) in most skills, so a one-line reader
under-counts. This parses the frontmatter properly:

```bash
# from repo root
python3 - <<'PY'
import glob, re
for f in sorted(glob.glob("skills/*/SKILL.md")):
    with open(f, encoding="utf-8") as fh:
        s = fh.read()
    fm = s.split("---", 2)[1]
    name = re.search(r'^name:\s*(.+)$', fm, re.M).group(1).strip().strip("\"'")
    m = re.search(r'^description:[ \t]*(.*)$', fm, re.M)
    val = m.group(1).strip()
    if val in (">", "|", ">-", "|-", ">+", "|+", ""):       # folded/literal block scalar
        body = []
        for ln in fm[m.end():].splitlines():
            if ln.strip() == "":
                continue
            if re.match(r'^\S', ln):                          # next top-level key
                break
            body.append(ln.strip())
        val = " ".join(body)
    else:
        val = val.strip('"\'')
    n = s.count("\n") + 1
    warn = " <-- review" if (n >= 450 or len(val) >= 950 or len(name) > 64) else ""
    print(f"{n:4d} lines  name={len(name):2d}/64  desc={len(val):4d}/1024  {f}{warn}")
PY
```

A `<-- review` row is approaching or past a limit — split the body into `references/`, or tighten
the `description`, before it breaks.

## Authoring rules

- **Progressive disclosure.** `SKILL.md` is the overview; push depth into `references/<topic>.md` and
  link them. Split before the body nears 500 lines.
- **References one level deep.** Every reference links directly from `SKILL.md`. Don't chain
  reference → reference; Claude may only partially read a nested file.
- **TOC for long references.** Any reference > 100 lines opens with a `## Contents` list.
- **Fully-qualified MCP names.** Write `ServerName:tool_name` (e.g. `GitHub:create_issue`), never a
  bare tool name.
- **Forward-slash paths**, always — `references/guide.md`, never backslashes.
- **Provide a default, not a menu.** One recommended path with an escape hatch beats listing five.
- **No time-sensitive info.** No "before August 2025…"; put superseded guidance in an "old patterns"
  `<details>` block instead.
- **Description in third person.** "Migrates a project…", not "I can…" / "You can…".

## Repo conventions

- **Skill folder.** `skills/<name>/` holds `SKILL.md`, an optional `references/` dir, and an optional
  `README.md` (user-facing; ships on install). Nothing else is required.
- **Naming.** Descriptive kebab-case noun phrase (`production-grade`, `tribunal`,
  `tailwind-v3-to-v4-migration`). Anthropic's guide prefers gerunds, but in this repo *consistency
  wins* — match the existing set.
- **Benchmarks live outside `skills/`** — in `benchmarks/<name>/`, so they don't ship when a skill is
  installed. See [benchmarking.md](benchmarking.md).
- **Untrusted third-party content.** A skill that tells the agent to fetch external content (docs,
  web, MCP issue/ticket bodies, other repos) must frame that content as *data, not instructions* —
  the indirect-prompt-injection posture. `skills/production-grade/SKILL.md` (M2) is the reference
  implementation.
- **Sandbox constraints (API).** The execution environment has **no network access** and **no runtime
  package install** — bundle resources in the skill and list required packages in `SKILL.md`.
- **Evaluation-driven.** Build ≥ 3 evaluations before writing extensive docs, and test the skill on
  Haiku, Sonnet, and Opus — a skill that helps Opus may under-guide Haiku.
