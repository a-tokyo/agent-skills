// production-grade arm: the same model with this skill's SKILL.md as the system prompt.
// Reads the sibling skill file directly, so the benchmark is self-contained. Only the SKILL.md
// body is injected (references/ load on demand in a real session), so this is a conservative
// lower bound on the skill's effect.
const fs = require('fs');
const path = require('path');
const system = fs.readFileSync(path.join(__dirname, '..', '..', 'SKILL.md'), 'utf8');
module.exports = ({ vars }) => [
  { role: 'system', content: system },
  { role: 'user', content: vars.task },
];
