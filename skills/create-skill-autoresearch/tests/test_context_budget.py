"""The builder's main-session context budget (criterion 18, the builder context diet).

Every builder turn re-reads its whole context, so the files the main session reads on the default path are a
per-turn cost. The default path is SKILL.md plus every reference that SKILL.md, or a file already on the path,
mentions without a condition. A mention is any occurrence of a file name from references/; it is conditional only
when its line says `only when`, `only if`, `only on` or `only as` (a pointer for a named branch, a subagent, or a
script). Conditional files are not followed. Sizes are characters (Python `len` of the UTF-8 text), not bytes.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SKILL_MD_BUDGET = 12_000
DEFAULT_PATH_BUDGET = 38_000
CONDITION = re.compile(r"\bonly (?:when|if|on|as)\b", re.IGNORECASE)


def default_path(skill_dir: Path) -> dict[str, int]:
    """{file name: characters} for SKILL.md and every reference it reaches through unconditional mentions."""
    refs = skill_dir / "references"
    names = sorted((p.name for p in refs.iterdir() if p.is_file()), key=len, reverse=True)
    mention = re.compile(r"(?<![\w.-])(?:references/)?(" + "|".join(map(re.escape, names)) + r")(?![\w-])")
    path: dict[str, int] = {}
    todo = [skill_dir / "SKILL.md"]
    while todo:
        file = todo.pop()
        if file.name in path:
            continue
        text = file.read_text(encoding="utf-8")
        path[file.name] = len(text)
        for line in text.splitlines():
            if CONDITION.search(line):
                continue
            for name in mention.findall(line):
                if name not in path:
                    todo.append(refs / name)
    return path


def report(path: dict[str, int]) -> str:
    rows = [f"  {name:28} {size:>7,}" for name, size in sorted(path.items(), key=lambda kv: -kv[1])]
    return "\n".join(rows + [f"  {'total':28} {sum(path.values()):>7,}"])


class ContextBudgetTest(unittest.TestCase):
    def test_skill_md_within_budget(self) -> None:
        size = len((SKILL / "SKILL.md").read_text(encoding="utf-8"))
        self.assertLessEqual(size, SKILL_MD_BUDGET, f"SKILL.md is {size:,} characters")

    def test_default_path_within_budget(self) -> None:
        path = default_path(SKILL)
        self.assertLessEqual(sum(path.values()), DEFAULT_PATH_BUDGET, "default-path read set:\n" + report(path))

    def test_branch_files_stay_off_the_default_path(self) -> None:
        """Files read only by subagents, scripts or on a named branch: an unmarked mention would pull them in."""
        path = default_path(SKILL)
        for name in ("benchmark-standard.md", "consensus-protocol.md", "hosts.md", "publishing.md",
                     "standalone-fallback.md", "verdict-rules.json", "authoring-rules.md", "rubric-templates.md",
                     "gate-runner.md", "evaluate-templates.md"):
            self.assertNotIn(name, path, f"{name} is mentioned without a condition:\n" + report(path))

    def test_read_discipline_is_stated(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for rule in ("with the Read tool", "Never `cat`, `sed` or grep a reference", "never read script source"):
            self.assertTrue(rule in text, f"SKILL.md no longer says: {rule}")


class ScriptHelpTest(unittest.TestCase):
    """SKILL.md forbids reading script source, so each script's --help must name how to run it."""

    NEEDS = {
        "gate.py": ["--detach --wait --model", "record", "--restart", "--stop", "Exit codes", "124"],
        "check_gate.py": ["--panel-receipt", "--consensus", "--finish", "--fast-path", "--loop", "--synthesis",
                          "verifier-a", "devils-advocate", "Exit codes"],
        "panel.py": ["check_gate.py --panel-receipt ROLE SCORES_JSON", "--consensus", "devils-advocate"],
        "run_owned.py": ["start <name>", "wait <name>", "stop <name>", "status <name>", "Exit codes"],
    }

    def test_help_is_sufficient(self) -> None:
        for script, needs in self.NEEDS.items():
            out = subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / script), "--help"],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, f"{script} --help: {out.stderr}")
            for need in needs:
                self.assertIn(need, out.stdout, f"{script} --help no longer says {need!r}")


class DefaultPathParserTest(unittest.TestCase):
    """The parser itself: an unmarked mention pulls a file in, transitively; a marked one does not."""

    def build(self, skill: str, refs: dict[str, str]) -> Path:
        root = Path(self.tmp.name)
        (root / "references").mkdir()
        (root / "SKILL.md").write_text(skill, encoding="utf-8")
        for name, text in refs.items():
            (root / "references" / name).write_text(text, encoding="utf-8")
        return root

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_unconditional_mentions_are_followed(self) -> None:
        root = self.build("Read [references/a.md](references/a.md).\n",
                          {"a.md": "then b.md 4.2\n", "b.md": "x" * 10, "c.md": "unused"})
        self.assertEqual(set(default_path(root)), {"SKILL.md", "a.md", "b.md"})

    def test_conditional_mentions_are_not(self) -> None:
        root = self.build("Read references/a.md only when the host is Cursor.\n",
                          {"a.md": "b.md", "b.md": ""})
        self.assertEqual(set(default_path(root)), {"SKILL.md"})

    def test_one_unmarked_mention_is_enough(self) -> None:
        root = self.build("references/a.md only if needed\n", {"a.md": "", "b.md": "", "z.md": "see a.md\n"})
        self.assertEqual(set(default_path(root)), {"SKILL.md"})
        (root / "SKILL.md").write_text("references/a.md only if needed\nsee a.md 3.1\n", encoding="utf-8")
        self.assertEqual(set(default_path(root)), {"SKILL.md", "a.md"})

    def test_longer_names_are_not_matched_by_their_suffix(self) -> None:
        root = self.build("see phase-1-2.md\n", {"phase-1-2.md": "", "2.md": "x"})
        self.assertEqual(set(default_path(root)), {"SKILL.md", "phase-1-2.md"})


if __name__ == "__main__":
    unittest.main()
