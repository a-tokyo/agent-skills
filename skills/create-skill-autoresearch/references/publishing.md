# Publishing a Finished Skill

Shipping is not a file copy. A skill that is installed but unregistered is invisible; a skill whose
version disagrees with its registry entry installs the wrong thing. This is the checklist.

## Contents
- [What ships and what does not](#what-ships-and-what-does-not)
- [Frontmatter](#frontmatter)
- [Register it, in every place that lists skills](#register-it-in-every-place-that-lists-skills)
- [If the skill is developed in one repo and published from another](#if-the-skill-is-developed-in-one-repo-and-published-from-another)
- [Checklist](#checklist)

## What ships and what does not

Ships — the skill directory, and only this:

```
skills/<skill-name>/
  SKILL.md
  references/       # if any
  scripts/          # if any
  assets/           # if any
  README.md         # optional, user-facing: what it does, method, results
```

Does not ship, and belongs at the repository root instead:

```
benchmarks/<skill-name>/    # arms, scorer, fixtures, results
```

Keeping the benchmark out of the skill directory is deliberate: installs stay small, and nobody
receives your fixtures. It also keeps the arms honest, since they resolve the skill by relative path
and therefore test the shipped file.

Build-workspace material — research notes, experiment journals, handoffs, craft ledgers — never ships.

## Frontmatter

`name` and `description` are required by the platform. Beyond those, carry:

```yaml
version: 0.2.0      # semver — bump whenever behaviour changes
license: MIT        # or whatever applies
```

Bump `version` on every behavioural change: patch for wording and fixes, minor for new capability.
The version is what a registry entry has to agree with, so a forgotten bump becomes a mismatch.

## Register it, in every place that lists skills

A repository of skills usually has more than one index, and they do not sync themselves. Before
calling a skill published, walk every surface:

| Surface | What to add | Trap |
|---------|-------------|------|
| The catalogue in `README.md` | a row: name, one-line description, install line | easy to forget for the *second* skill onward |
| A plugin marketplace manifest (e.g. `.claude-plugin/marketplace.json`) | an entry per skill: `name`, `source`, `skills: ["./skills/<name>"]`, `strict: false`, `version`, `description` | **its `version` is an independent field — nothing syncs it to the skill's frontmatter.** Bump both, together |
| An "install everything" aggregate entry, if one exists | the new skill in its description | silently omits the new skill otherwise |
| Release automation (e.g. a zip-per-skill workflow) | usually automatic — confirm it picked the skill up | a new skill can fall outside a glob |

The version mismatch is the one that bites: the skill says `0.2.0`, the marketplace entry still says
`0.1.0`, and installs resolve to a stale description while the files are current.

## If the skill is developed in one repo and published from another

Pick one **source of truth** and never edit the published copy directly. The publish step is then a
whole-directory replace, so a `references/` file cannot silently drift:

`<source-dir>` is the directory that *contains* your source-of-truth copy of the skill, and it is not
necessarily named `skills/` — in this harness it is `.agents/skills/`, while the registry it publishes to
uses `skills/`. Set both explicitly so the three commands cannot drift apart:

```bash
SKILL=<skill-name>
SOURCE=<source-dir>          # e.g. .agents/skills   — holds <skill-name>/
PUBLISHED=<registry-repo>    # e.g. ../agent-skills  — holds skills/<skill-name>/

rm -rf "$PUBLISHED/skills/$SKILL"
cp -r "$SOURCE/$SKILL" "$PUBLISHED/skills/$SKILL"
diff -rq "$SOURCE/$SKILL" "$PUBLISHED/skills/$SKILL"   # must print nothing
```

The `diff -rq` is the point — copy without it and a stale reference file survives. Editing the
published copy directly is how two copies diverge, and by the time anyone notices, it is unclear which
one is correct.

Better still, make the check automatic: a CI job that diffs source against published catches drift on
the pull request rather than months later. A skill that lives in two places and is only compared by
hand *will* drift.

## Checklist

- [ ] `version` bumped in frontmatter; `license` present
- [ ] Body under 500 lines; references one level deep; TOC on any reference over ~100 lines
- [ ] Benchmark exists at `benchmarks/<skill-name>/`, outside the skill directory
- [ ] Catalogue row added
- [ ] Marketplace/plugin entry added, with `version` matching the frontmatter
- [ ] Aggregate "all skills" entry updated, if there is one
- [ ] If developed elsewhere: whole-directory re-sync, `diff -rq` clean
- [ ] No credential in any shipped file
