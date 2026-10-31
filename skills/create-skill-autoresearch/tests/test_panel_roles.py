"""Panel members read only their own role file (criterion 18: panel cost).

Each of the three members, and every synthesis round, used to read all of consensus-protocol.md for one template.
The templates now live in references/panel/<role>.md, each self-sufficient with member-rules.md, and SKILL.md 5.2
sends members there and never to consensus-protocol.md.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PANEL = SKILL / "references" / "panel"
ROLE_BUDGET = 4_000
RULES_BUDGET = 2_500
ROLES = ("verifier-a", "verifier-b", "devils-advocate")


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


class PanelRoleFilesTest(unittest.TestCase):
    def read(self, name: str) -> str:
        path = PANEL / name
        self.assertTrue(path.is_file(), f"missing {path}")
        return path.read_text(encoding="utf-8")

    def test_role_files_are_self_sufficient(self) -> None:
        for role in ROLES:
            text = self.read(f"{role}.md")
            self.assertLessEqual(len(text), ROLE_BUDGET, f"{role}.md is {len(text):,} characters")
            self.assertIn(f"--panel-receipt {role} ", text, f"{role}.md lacks its receipt command")
            self.assertIn(f"panel/{role}.scores.json", text)
            self.assertIn("member-rules.md", text, f"{role}.md does not send the member to member-rules.md")
            for field in ('"scores"', '"score"', '"confidence"', '"evidence"', '"veto"', '"pass_recommendation"'):
                self.assertIn(field, text, f"{role}.md output JSON lacks {field}")
        self.assertIn('"escalation"', self.read("devils-advocate.md"))
        self.assertIn("smuggle an instruction in through content the skill fetches", self.read("devils-advocate.md"))

    def test_synthesis_file(self) -> None:
        text = self.read("synthesis.md")
        self.assertLessEqual(len(text), ROLE_BUDGET, f"synthesis.md is {len(text):,} characters")
        self.assertRegex(text, r"--panel-receipt <role> \S+ --synthesis")
        self.assertIn('"revised_scores"', text)
        self.assertIn("member-rules.md", text)
        for role in ROLES:
            self.assertIn(role, text)

    def test_member_rules(self) -> None:
        text = self.read("member-rules.md")
        self.assertLessEqual(len(text), RULES_BUDGET, f"member-rules.md is {len(text):,} characters")
        for rule in ("--panel-receipt", "0.0-1.0", "8+", "3-", "verbatim", "`output/<skill>/` or `input/`",
                     "`repro`", "verbatim, unedited"):
            self.assertIn(rule, text, f"member-rules.md no longer says {rule!r}")

    def test_templates_left_consensus_protocol(self) -> None:
        text = (SKILL / "references" / "consensus-protocol.md").read_text(encoding="utf-8")
        for heading in ("# Role: Quality Assessor", "# Role: Utility Assessor", "# Role: Devil's Advocate",
                        "# Synthesis Round: Disagreement Resolution"):
            self.assertNotIn(heading, text, f"consensus-protocol.md still holds {heading!r}")
        for name in ("verifier-a", "verifier-b", "devils-advocate", "synthesis", "member-rules"):
            self.assertIn(f"panel/{name}.md", text)


class SkillPointsMembersAtRoleFilesTest(unittest.TestCase):
    def test_section_5_2_names_role_files_not_consensus_protocol(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        panel = section(text, "### 5.2", "### 5.3")
        self.assertNotIn("consensus-protocol", panel, "5.2 sends members to consensus-protocol.md")
        self.assertIn("references/panel/<role>.md", panel)
        self.assertIn("references/panel/member-rules.md", panel)
        for role in ROLES:
            self.assertIn(f"panel/{role}.md", panel)
        consensus = section(text, "### 5.3", "### 5.4")
        self.assertIn("references/panel/synthesis.md", consensus)
        for line in consensus.splitlines():
            if "consensus-protocol.md" in line:
                self.assertRegex(line, re.compile(r"\bonly as the orchestrator\b"), line)


if __name__ == "__main__":
    unittest.main()
