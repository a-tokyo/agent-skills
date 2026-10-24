"""Shared fixtures for the offline script tests: a fake `claude` on PATH, fake run-case.sh adapters,
temp workspaces (TempWorkspace) and process helpers. No network, no credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import run_owned  # noqa: E402

PY = sys.executable


def py(code: str) -> list[str]:
    return [PY, "-c", code]


SLEEP = py("import time; time.sleep(300)")
TRUE = py("pass")
# The job and a child it starts; the child's pid goes to the log so a test can check the whole tree ended.
TREE = py("import subprocess, sys, time\n"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
          "print('child', child.pid, flush=True)\ntime.sleep(300)")
IGNORES_TERM = py("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)")
DETACHED = {"start_new_session": True}

FAKE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Fake `claude plugin eval`; behaviour from FAKE_MODE: complete | partial | hang | fewruns | silent |
    nodelta (gold cases report a null delta); FAKE_TRIGGER_MISSES / FAKE_NEAR_MISS_FIRES fail that many
    trigger-* with-arm runs / near-miss* bare-arm runs. Cases named trigout-* get skill-fired plus an output regex
    that passes in both arms (delta 0, the k-* task-2 Sonnet 5 trigger-2 shape); trigger-* only skill-fired,
    near-miss* only a not-fired one (max 0), holdout-* the worked case's approval regex plus skill-fired.
    A case directory holding a file named INVALID fails to load: like the real CLI, a `✗` line names it
    and the run skips it. `--max-cost-usd 0` loads the cases and stops before any run (exit 2)."""
    import json, os, sys, time
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    log = os.environ.get("FAKE_ARGV_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write(json.dumps(args) + "\\n")
    if args[:3] == ["plugin", "eval", "--help"]:
        sys.exit(int(os.environ.get("FAKE_HELP_RC", "0")))
    invalid = sorted(p.name for p in Path("evals").iterdir() if (p / "INVALID").is_file())
    for name in invalid:
        print(f"✗ {Path('evals', name, 'case.yaml').resolve()}: invalid case.yaml:   graders.0: Unrecognized key")
    if invalid:
        print(f"{len(invalid)} case file(s) failed to load")
    if "--max-cost-usd" in args:
        sys.exit(2)
    mode = os.environ.get("FAKE_MODE", "complete")
    if mode == "hang":
        time.sleep(300)
    if mode == "silent":
        sys.exit(1)
    runs = int(args[args.index("--runs") + 1])
    if mode == "fewruns":
        runs -= 1
    out = args[args.index("--json") + 1]

    def case(name):
        if name.startswith("trigout"):
            graders = [{"name": "skill-fired", "type": "tool_used", "config": {"tool": "Skill"}},
                       {"name": "valid", "type": "regex", "config": {}}]
            fired = {"score": 1, "error": None, "graders": [{"name": "skill-fired", "passed": True},
                                                             {"name": "valid", "passed": True}]}
            bare = {"score": 1, "error": None, "graders": [{"name": "valid", "passed": True}]}
            return {"name": name, "graders": graders, "arms": {"with": [fired] * runs, "without": [bare] * runs},
                    "aggregates": {"delta": 0}}
        def indicator(grader_name, misses):
            runs_ = [{"score": 1, "error": None, "graders": [{"name": grader_name, "passed": i >= misses}]}
                     for i in range(runs)]
            return runs_
        if name.startswith("trigger"):
            grader = {"name": "skill-fired", "type": "tool_used", "config": {"tool": "Skill"}}
            with_runs = indicator("skill-fired", int(os.environ.get("FAKE_TRIGGER_MISSES", "0")))
            return {"name": name, "graders": [grader], "arms": {"with": with_runs, "without": indicator("skill-fired", 0)},
                    "aggregates": {"delta": 0}}
        if name.startswith("near-miss"):
            grader = {"name": "not-fired", "type": "tool_used", "config": {"tool": "Skill", "min": 0, "max": 0}}
            bare = indicator("not-fired", int(os.environ.get("FAKE_NEAR_MISS_FIRES", "0")))
            return {"name": name, "graders": [grader], "arms": {"with": indicator("not-fired", 0), "without": bare},
                    "aggregates": {"delta": 0}}
        delta = None if mode == "nodelta" else 0.5
        if name.startswith("holdout"):
            graders = [{"name": "approval", "type": "regex",
                        "config": {"target": {"source": "file", "path": "po.json"}, "pattern": "director"}},
                       {"name": "skill-fired", "type": "tool_used", "config": {"tool": "Skill"}}]
            good = {"score": 1, "error": None, "graders": [{"name": "approval", "passed": True},
                                                            {"name": "skill-fired", "passed": True}]}
            bare = {"score": 0.6, "error": None, "graders": [{"name": "approval", "passed": False}]}
            return {"name": name, "graders": graders, "arms": {"with": [good] * runs, "without": [bare] * runs},
                    "aggregates": {"delta": None if mode == "nodelta" else 0.4}}
        grader = {"name": "fact", "type": "regex", "config": {}}
        good = {"score": 1, "error": None, "graders": [{"name": "fact", "passed": True}]}
        bare = {"score": 0.5, "error": None, "graders": [{"name": "fact", "passed": False}]}
        return {"name": name, "graders": [grader], "arms": {"with": [good] * runs, "without": [bare] * runs},
                "aggregates": {"delta": delta}}

    cases = sorted(p.name for p in Path("evals").iterdir() if (p / "prompt.md").is_file() and p.name not in invalid)
    data = {
        "schemaVersion": 1, "partial": mode == "partial",
        "partialReason": "interrupted" if mode == "partial" else None,
        "skillCopy": sorted(p.name for p in Path("skills").rglob("*") if p.is_file()),
        "cases": [case(c) for c in cases],
    }
    Path(out).write_text(json.dumps(data))
''')

SCORING_ADAPTER = textwrap.dedent('''\
    #!/bin/sh
    # with -> 0.9, without -> 0.4; case "flaky" fails its first attempt of run 2; "slow" hangs
    case="$1"; arm="$2"
    marker="work/pids/.flaky-$arm"  # scratch outside work/evaluation/, whose files are gate inputs
    if [ "$case" = "flaky" ] && [ "$GATE_RUN" = "2" ] && [ ! -f "$marker" ]; then
      : > "$marker"; echo "transient failure" >&2; exit 1
    fi
    if [ "$case" = "slow" ]; then sleep 300; fi
    echo "running $case $arm on $GATE_MODEL"
    echo "EXECUTOR=$GATE_EXECUTOR"
    if [ "$arm" = "with" ]; then echo "SCORE=0.9"; else echo "SCORE=0.4"; fi
''')


def _env(bin_dir: Path, **extra: str) -> dict[str, str]:
    """CLAUDECODE=1 as inside Claude Code, where the gate defaults to plugin eval; pass CLAUDECODE="" for
    another host."""
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "PYTHONDONTWRITEBYTECODE": "1", "CLAUDECODE": "1", **extra}


def _path_without_claude() -> str:
    dirs = [d for d in os.environ["PATH"].split(os.pathsep)
            if d and not os.path.exists(os.path.join(d, "claude"))]
    return os.pathsep.join(dirs or ["/usr/bin", "/bin"])


def _script(name: str, scripts: Path = SCRIPTS) -> list[str]:
    return [sys.executable, "-B", str(scripts / name)]


def as_native_windows(script: Path) -> subprocess.CompletedProcess:
    """Run `script` with os.name set to "nt", as on native Windows (stdlib modules that check os.name at
    import are loaded first)."""
    code = f"import os, pathlib, runpy, shutil, subprocess, sys, tempfile; os.name = 'nt'; sys.argv = [{str(script)!r}]; runpy.run_path(sys.argv[0], run_name='__main__')"
    return subprocess.run([sys.executable, "-B", "-c", code], input="{}", capture_output=True, text=True, timeout=30,
                          cwd=script.parent)


NATIVE_WINDOWS = "native Windows is not supported; run the factory inside WSL"


def _alive(pid: int) -> bool:
    return run_owned._pid_alive(pid)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _until(predicate, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


class TempWorkspace(unittest.TestCase):
    scripts = SCRIPTS  # a test may point this at a copy of the skill with other verdict-rules.json values

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        self.argv_log = self.root / "claude-argv.jsonl"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_build(self, cases: list[str], invoked: bool = True, ws: Path | None = None,
                   training: str = "[]", manual: bool | None = None) -> None:
        """A build whose suite holds `cases`. Manual builds (default: user-invoked skills) also list them
        as gold standards under all_cases, the manual protocol's case source."""
        ws = ws or self.ws
        skill = ws / "output" / "demo"
        skill.mkdir(parents=True)
        flag = "" if invoked else "disable-model-invocation: true\n"
        (skill / "SKILL.md").write_text(f"---\nname: demo\n{flag}description: Demo.\n---\n# Demo\n")
        evaluation = ws / "work" / "evaluation"
        evaluation.mkdir(parents=True)
        listed = "\n".join(f'  - "{c}"' for c in cases)
        gold = f"all_cases:\n{listed}\n" if (not invoked if manual is None else manual) and cases else ""
        (evaluation / "data-split.yaml").write_text(
            f"{gold}training: {training}\nplugin_eval_available: 0  # probe exit code\nplugin_eval:\n{listed}\n",
            encoding="utf-8")
        plugin = evaluation / "plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "demo-eval", "version": "0.0.0"}')
        for case in cases:
            (plugin / "evals" / case).mkdir(parents=True)
            (plugin / "evals" / case / "prompt.md").write_text(f"Do {case}.\n", encoding="utf-8")

    def write_adapter(self, body: str = SCORING_ADAPTER) -> None:
        self.write_adapter_to(self.ws, body)

    def write_adapter_to(self, ws: Path, body: str = SCORING_ADAPTER) -> None:
        adapter = ws / "work" / "evaluation" / "run-case.sh"
        adapter.write_text(body)
        adapter.chmod(0o755)

    def run_script(self, name: str, *args: str, timeout: float = 60, ws: Path | None = None,
                   no_claude: bool = False, **env: str) -> subprocess.CompletedProcess:
        """no_claude: PATH holds no `claude` at all (neither the fake nor a real install)."""
        full = _env(self.bin, FAKE_ARGV_LOG=str(self.argv_log), **env)
        if no_claude:
            full["PATH"] = _path_without_claude()
        return subprocess.run([*_script(name, self.scripts), "--workspace", str(ws or self.ws), *args], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout, env=full)

    def evaluation(self, name: str) -> dict:
        return json.loads((self.ws / "work" / "evaluation" / name).read_text(encoding="utf-8"))

    def record(self, name: str, ws: Path | None = None) -> dict:
        return json.loads(((ws or self.ws) / "work" / "pids" / f"{name}.json").read_text(encoding="utf-8"))

    def write_rubric(self, target: float = 0.85) -> None:
        (self.ws / "work" / "evaluation" / "rubric.yaml").write_text(textwrap.dedent(f"""\
            name: demo-rubric
            dimensions:
              - name: correctness  # the first dimension
                weight: 0.6
                scale: "1-10"
                criteria: "Accurate."
              - name: clarity
                weight: 0.4
                criteria: "Clear."
            target_score: {target}
            max_iterations: 20
            """))

    def receipt(self, role: str, correctness: float, clarity: float, escalation: str | None = None,
                synthesis: dict | None = None) -> subprocess.CompletedProcess:
        """Write `role`'s scoring JSON (the panel template's output) and run --panel-receipt on it."""
        def dim(score: float) -> dict:
            return {"score": score, "confidence": 0.9, "evidence": "verbatim quote"}
        body = ({"revised_scores": synthesis, "final_recommendation": "SHIP"} if synthesis is not None else
                {"scores": {"correctness": dim(correctness), "clarity": dim(clarity)},
                 "pass_recommendation": "SHIP_WITH_CAVEATS", "escalation": escalation})
        path = self.root / f"{role}{'-synthesis' if synthesis is not None else ''}.json"
        path.write_text(json.dumps(body))
        return self.run_script("check_gate.py", "--panel-receipt", role, str(path),
                               *(["--synthesis"] if synthesis is not None else []))

    def run_panel(self, scores: dict[str, tuple[float, float]], escalation: str | None = None) -> str:
        """Every role's receipt, then --consensus; returns the PANEL CONSENSUS line."""
        for role, (correctness, clarity) in scores.items():
            out = self.receipt(role, correctness, clarity, escalation if role == "devils-advocate" else None)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        out = self.run_script("check_gate.py", "--consensus")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.strip()
