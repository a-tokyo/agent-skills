"""Offline tests for gate_common.py: the data-split reader and the tree hash.

Fixtures (fake `claude`, fake adapters, temp workspaces) are in helpers.py. No network, no credentials.

    python3 -B -m unittest discover -s <skill>/tests -v
"""

from __future__ import annotations

import unittest

from helpers import NATIVE_WINDOWS, SCRIPTS, TempWorkspace, as_native_windows
import gate_common  # noqa: E402  (on sys.path through helpers)


class DataSplitReaderTest(TempWorkspace):
    def read(self, text: str) -> gate_common.DataSplit:
        path = self.ws / "data-split.yaml"
        path.write_text(text)
        return gate_common.read_data_split(path)

    def test_flow_and_block_lists(self) -> None:
        self.assertEqual(self.read("plugin_eval: [a, 'b', \"c\"]  # note\n").plugin_eval, ["a", "b", "c"])
        split = self.read("plugin_eval_available: 0\nplugin_eval:\n  # comment\n  - gold-01\n  - \"gold-02\"\nnext: 1\n")
        self.assertEqual((split.plugin_eval, split.plugin_eval_available), (["gold-01", "gold-02"], 0))

    def test_rejects_shapes_it_does_not_read(self) -> None:
        self.assertIn("list of case names", self.read("plugin_eval:\n  cases: [a, b]\n").plugin_eval_error)
        self.assertIn("mapping", self.read("plugin_eval: {cases: [a, b]}\n").plugin_eval_error)
        self.assertIn("scalar", self.read("plugin_eval: not_scaffolded\n").plugin_eval_error)
        self.assertIn("twice", self.read("plugin_eval: [a, a]\n").plugin_eval_error)
        self.assertEqual(self.read("plugin_eval_available: manual\n").plugin_eval_available, "manual")

    def test_double_quoted_names_are_json_decoded(self) -> None:
        self.assertEqual(self.read('plugin_eval: ["gold-\\u00e9", "a\\"b", "plain"]\n').plugin_eval,
                         ["gold-é", 'a"b', "plain"])
        self.assertEqual(self.read("plugin_eval:\n  - 'it''s'\n  - \"x\\\\y\"\n").plugin_eval, ["it''s", "x\\y"])

    def test_flow_lists_split_only_outside_quotes(self) -> None:
        split = self.read('training: ["input/request, urgent.md", \'b, c\', d]\nplugin_eval: [a]\n')
        self.assertEqual(split.gold["training"], ["input/request, urgent.md", "b, c", "d"])
        self.assertIn("unclosed quote", self.read('training: ["a, b]\nplugin_eval: [a]\n').gold_error)

    def test_non_ascii_case_names_round_trip_through_write_and_read(self) -> None:
        path = self.ws / "data-split.yaml"
        cases = ["gold-é", "naïve-02", "日本-03", "plain-04"]
        gate_common.write_gate_cases(path, cases, 0, "plugin/evals", "probe exit code")
        self.assertIn('"gold-\\u00e9"', path.read_text(), "written as JSON escapes")
        self.assertEqual(gate_common.read_data_split(path).plugin_eval, cases)

    def test_gold_standards_are_recorded_by_any_split_key(self) -> None:
        self.assertFalse(self.read("training: []\nplugin_eval: [a]\n").gold_recorded)
        self.assertTrue(self.read("test: [input/g1.md]\nplugin_eval: [a]\n").gold_recorded)
        self.assertTrue(self.read("all_cases:\n  - input/g1.md\nplugin_eval: [a]\n").gold_recorded)


class TreeHashTest(TempWorkspace):
    def test_hash_covers_paths_and_contents_but_not_caches(self) -> None:
        (self.ws / "a").mkdir()
        (self.ws / "a" / "x.md").write_text("one")
        first = gate_common.tree_sha256(self.ws)
        (self.ws / "a" / "__pycache__").mkdir()
        (self.ws / "a" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
        (self.ws / ".DS_Store").write_bytes(b"\0")
        self.assertEqual(gate_common.tree_sha256(self.ws), first)
        (self.ws / "a" / "x.md").write_text("two")
        self.assertNotEqual(gate_common.tree_sha256(self.ws), first)
        (self.ws / "a" / "x.md").write_text("one")
        (self.ws / "a" / "x.md").rename(self.ws / "a" / "y.md")
        self.assertNotEqual(gate_common.tree_sha256(self.ws), first, "a rename changes the hash")



class NativeWindowsTest(TempWorkspace):
    def test_gate_and_check_gate_exit_early_with_one_message(self) -> None:
        for script in ("gate.py", "check_gate.py"):
            with self.subTest(script=script):
                out = as_native_windows(SCRIPTS / script)
                self.assertEqual((out.returncode, out.stderr.strip()), (1, NATIVE_WINDOWS))


if __name__ == "__main__":
    unittest.main()
