# Adapter — Django (uv-native, greenfield)

Runner is **poethepoet** (`uv run poe <gate>`) — the only documented path, no justfile/Makefile
alternative (D12). Templates carry no exact app-dep versions; `pyproject.toml` uses `>=` and
`uv.lock` pins reality.

## Contents

- Scaffold + day-1 pre-fixes
- Static analysis (ruff + mypy)
- Testing + coverage
- E2E gate
- poe tasks (verbatim)
- Hooks (verbatim)
- Supply chain
- CI

## Scaffold + day-1 pre-fixes

**Prerequisite (Phase 0 preflight):** `uv` must be on PATH (it drives every gate). If missing, offer
the install and, if the user declines, abort before Phase 1 (§6 — greenfield has no partial success):
`brew install uv` (macOS) or `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux). All
other tools (ruff, mypy, poe, pre-commit) are dev-group deps `uv` installs; `pre-commit install`
wires the hooks.

```bash
uv init --app --vcs none {{APP}}   # flat layout, NEVER --lib/src-layout; --vcs none: Phase 1 owns git init (uv would otherwise create .git itself)
cd {{APP}}
uv add django
uv run django-admin startproject config .   # trailing "." required, else it nests
uv run manage.py startapp core
```

`uv init` generates `.python-version` automatically. Apply these pre-fixes immediately, or the repo
is red on commit #1:

- `manage.py`: `def main():` → `def main() -> None:` (mypy `--strict`).
- `config/settings.py`: `ALLOWED_HOSTS = []` → `ALLOWED_HOSTS: list[str] = []` (mypy `var-annotated`).
- Run `ruff check --fix` on the generated tree (clears `F401` placeholder-import hits in
  `core/admin.py`/`core/models.py`).
- `config/settings.py`: add `STATIC_ROOT = BASE_DIR / "staticfiles"` — the `build` gate runs
  `collectstatic`, which is a hard code-red without it (live-verified).
- Delete the `main.py` stub `uv init` drops at the root (dead code under `select=["ALL"]`).
- Seed tests live in a `core/tests/` package (`core/tests/test_seed.py`, delete `startapp`'s
  `tests.py` stub) — package layout keeps test discovery and tooling conventions unambiguous.
- Seed code with numeric literals: name them as constants (ruff `PLR2004` fires on magic values
  under `select=["ALL"]`).
- `.gitignore` additions Phase 7 needs for a clean tree: `staticfiles/`, `coverage.xml`,
  `.coverage`, `db.sqlite3`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`.

## Static analysis

Ruff `select = ["ALL"]` (maximalism is the point; pin the ruff version, review new rules on bump),
`preview = false`, with a rationale-commented ignore list + per-file ignores. mypy `--strict` +
django-stubs (the only actively-maintained framework-aware type integration — pyright has no Django
plugin). Cognitive complexity has no ruff rule; degrade honestly to cyclomatic `C901` at
`max-complexity = 15`. See the skeleton below for the full ignore lists.

## Testing + coverage

pytest + pytest-django + pytest-cov, all config in `pyproject.toml`. `--cov-branch` gives branch
coverage (coverage.py has statements + branches, no functions metric → one blended
`--cov-fail-under=85`, matching `references/canon/coverage.md`). `--cov-report=xml` writes Cobertura
`coverage.xml` for Sonar.

Seed set (a fresh scaffold has zero real branch logic):

- `core/utils.py` — a branchy pure function (`classify_amount`), parametrized-tested (the "util with
  real branches").
- `core/views.py` — one view (`health`) wired into `config/urls.py`, tested via the `client` fixture.
- Settings smoke is implicit: `DJANGO_SETTINGS_MODULE` loading at pytest collection proves settings
  import cleanly.

## E2E gate

pytest-django's `live_server` fixture + httpx — a real WSGI server on a real port, zero extra
dependency, zero binary download (Playwright-python is the documented escalation once real HTML
exists). Mark it `@pytest.mark.e2e`; the `e2e` task runs `pytest --no-cov -m e2e`. **`--no-cov` on
e2e is load-bearing** — without it the global `--cov-fail-under` applies to the e2e-only run and
fails it at low coverage (a probed footgun).

## poe tasks (verbatim)

```toml
[tool.poe.tasks]
lint = "ruff check ."
format = "ruff format --check ."     # auxiliary, not a gate
typecheck = "mypy ."
test = "pytest --no-cov -m 'not e2e'"
coverage = "pytest -m 'not e2e'"
e2e = "pytest --no-cov -m e2e"
audit = "uv audit"
build = { sequence = [
    { cmd = "python manage.py check --deploy" },
    { cmd = "python manage.py collectstatic --noinput" },
] }
gate = ["lint", "format", "typecheck", "coverage", "build", "audit"]
```

`build`'s teeth are compile/collectstatic failure. `check --deploy` runs **without**
`--fail-level WARNING` (D16): on dev defaults it prints 7 warnings but exits 0 — the warnings are
real and visible in CI, and failing them day-1 would force fake prod settings (lying to go green).
Document `--fail-level WARNING` as the escalation once env-split prod settings exist (`DEBUG=False`,
`SECRET_KEY` from env, real `ALLOWED_HOSTS`).

## Hooks (verbatim)

`.pre-commit-config.yaml` — pin each `rev:` to the version resolved at generation time:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.20
    hooks:
      - id: ruff-check        # hook id is ruff-check, not the old "ruff"
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: uv run mypy .
        language: system      # NOT mirrors-mypy — its isolated venv can't see project deps
        types: [python]
        pass_filenames: false
  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: 0.11.1
    hooks:
      - id: uv-lock
```

## Supply chain

`uv audit` (native OSV) is the `audit` gate — **experimental** at uv 0.11.1 (prints an experimental
warning); document the caveat, `pip-audit` is the fallback. `uv.lock` committed; CI runs
`uv sync --locked` then `uv run --frozen poe <gate>`. `.python-version` pins the toolchain. No native
min-release-age (honest negative).

## CI

`astral-sh/setup-uv` (replaces setup-python) → `uv sync --locked` → `uv run --frozen poe gate`.
Sonar property `sonar.python.coverage.reportPaths=coverage.xml`.

## pyproject.toml skeleton

```toml
[project]
name = "{{APP}}"
requires-python = ">=3.13"
dependencies = ["django>=6.0"]

[dependency-groups]
dev = ["django-stubs","mypy","poethepoet","pre-commit","pytest","pytest-cov","pytest-django","ruff","httpx"]

[tool.ruff]
target-version = "py313"
line-length = 88

[tool.ruff.lint]
select = ["ALL"]
preview = false
ignore = ["W191","E111","E114","E117","Q000","Q001","Q002","Q003",   # formatter conflicts
          "COM812","COM819","ISC001","ISC002",
          "D",        # docstring style is taste, not a defect class
          "ANN401"]   # redundant with mypy --strict

[tool.ruff.lint.per-file-ignores]
"manage.py" = ["ANN201","PLC0415","TRY003","EM101"]
"*/migrations/*.py" = ["ALL"]
"**/settings*.py" = ["E501","S105"]
"**/views.py" = ["ARG001"]
"**/tests.py" = ["S101","PLR2004","TC002"]
"**/tests/**.py" = ["S101","PLR2004","TC002"]

[tool.ruff.format]
quote-style = "double"

[tool.ruff.lint.mccabe]
max-complexity = 15   # cyclomatic — ruff has no cognitive-complexity rule (honest degradation)

[tool.mypy]
plugins = ["mypy_django_plugin.main"]
strict = true
exclude = ["migrations/"]

[[tool.mypy.overrides]]
module = "*.migrations.*"
ignore_errors = true

[tool.django-stubs]
django_settings_module = "config.settings"
strict_settings = true

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings"
addopts = "--cov=core --cov=config --cov-branch --cov-report=term-missing --cov-report=xml --cov-fail-under=85"
python_files = ["test_*.py","*_test.py","tests.py"]
markers = ["e2e: end-to-end tests against a live server (excluded from unit/coverage runs)"]

[tool.coverage.run]
branch = true
source = ["core","config"]
omit = ["*/migrations/*","manage.py","config/asgi.py","config/wsgi.py","*/tests.py","*/tests/*","*/test_*.py"]
```
