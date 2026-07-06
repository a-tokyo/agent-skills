# Companion skills (factory arm only)

`create-skill-autoresearch` orchestrates sibling skills at runtime; the factory arm injects
these pinned copies into the run's isolated HOME alongside the skill under test. Vendored here
so the benchmark is self-contained. Sources (same upstreams pinned in the repo/workspace
`skills-lock.json`):

| Skill | Source |
|-------|--------|
| autoresearch | github/awesome-copilot |
| premortem | parcadei/continuous-claude-v3 |
| handoff | mattpocock/skills |

The `skill-creator` arm injects the repo's vendored `.agents/skills/skill-creator` instead.
The bare arm injects nothing.
