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
| skill-creator | anthropics/skills (v0.3.0+: authoring and the description pass) |
| writing-for-agents | mattpocock/skills (v0.3.0+: craft passes) |

`tribunal` is deliberately absent, so Phase 5 runs the factory's inline panel in every arm.
The `skill-creator` arm injects the repo's vendored `.agents/skills/skill-creator` instead.
The `factory-v021` arm injects the pinned v0.2.1 companions (autoresearch, premortem, handoff) via
`COMPANIONS_DIR`. The bare arm injects nothing.
