# entry-claim-audit

Offline static CLI that checks Python packaging entry-point claims against source on disk. It checks `[project.scripts]` and `[project.gui-scripts]` targets for a module, symbol, and callability, and checks documented command names. It never executes project code, accesses the network, or reads .env contents. Reports contain rule names, paths, script names and targets, not unrelated file contents.

Requires Python 3.9 or later. Runtime dependencies: none.

## Usage

```sh
pip install .
entry-claim-audit .
entry-claim-audit /path/to/project --json
entry-claim-audit . --fail-on HIGH
entry-claim-audit . --ignore script-undocumented,script-target-not-callable
entry-claim-audit . --verbose
entry-claim-audit --version
python3 -m entry_claim_audit .
```

The path defaults to `.`. Exit codes: 0 PASS, 2 FAIL, 1 usage/input error. Default failure threshold is MEDIUM; `--fail-on HIGH` still prints MEDIUM findings but does not fail for them. `--ignore RULE,...` drops those rules entirely, for example when scripts are documented elsewhere or a data-object target is intentional. `--verbose` adds verified scripts to human output.

Human output has one `SEVERITY rule file:line message` line per finding followed by PASS/FAIL. Metadata findings use `pyproject.toml:0`. JSON contains `status`, `findings` (severity, rule, file, line, message) sorted by rule/file/line, and `verified_scripts` (script, table, target).

For example, `mytool = "pkg.cli:main"` passes if `pkg/cli.py` defines `def main()` and README mentions `mytool`. Renaming that file without updating the target yields `script-target-module-missing` and exit 2.

## Full rule table

| Rule | Severity | Trigger and report |
| --- | --- | --- |
| `script-target-malformed` | HIGH | Target has no colon, an empty module/object, internal whitespace, or invalid dotted identifiers. Surrounding whitespace is stripped. Reports script, table and raw target; no further target checks for that script. |
| `script-target-module-missing` | HIGH | Target module does not resolve to a source file in the supported layouts. Reports script, table and module. |
| `script-target-symbol-missing` | HIGH | Resolved module lacks the first object segment. Reports script, table, module and missing symbol. |
| `script-target-attr-missing` | HIGH | First object is a class but the next segment is absent from its directly defined methods. Reports script, table and missing method. Chains beyond that method, or attributes of non-class objects, are unchecked. |
| `script-target-not-callable` | MEDIUM | First object is a plain data assignment. Functions, async functions, classes, imports, lambdas and call-result assignments pass. Only the first object segment is checked. |
| `documented-command-missing` | HIGH | First token of a fenced or inline code command equals the project name (case/dash/underscore normalized), but is not an exactly declared script name. Reports doc file:line, token and declared names. Arbitrary other commands are ignored; skipped without a project name. |
| `script-name-duplicate` | MEDIUM | Same name is declared in both scripts and gui-scripts. |
| `script-undocumented` | MEDIUM | Script name never occurs as a whole word anywhere in scanned docs, including prose. Skipped when there is neither README.md nor docs markdown. |

Verified targets are not findings. Both script tables receive the same checks. No pyproject.toml, no project table, or no script tables yields PASS with zero findings.

## Static interpretation

Modules resolve in this order: `src/pkg/mod.py`, `src/pkg/mod/__init__.py`, `pkg/mod.py`, `pkg/mod/__init__.py`. Single-segment modules and package `__init__.py` targets are supported. Namespace parent directories are allowed. The directories tests, .venv, __pycache__, dist, .git and .egg-info are not module evidence.

Only the resolved module is parsed. Top-level functions, async functions, classes, Name assignment targets (including annotations), and imported names count as symbols. Re-exports count without importing or following them. Direct class methods include `__init__`. Assignments to lambda/call expressions count as callable or unknown; all other assigned values, including names and attributes, count as data. Definitions nested inside functions or conditional blocks are not collected.

Docs scanned: top-level `*.md` including README.md, and recursive `docs/**/*.md`. Command claims come only from fenced code and inline code spans. Undocumented checks use full text. TOML uses stdlib tomllib on Python 3.11+, with a minimal quoted key/value regex fallback for script tables on older Python (and a simple project-name lookup).

## Differentiation

- **readme-claim-audit** audits pip install names against pyproject and docs flags, links, versions and make targets. This checks whether installed commands resolve to modules, symbols and callables; the rules do not overlap.
- **api-claim-audit** audits symbols promised in docs code examples. This checks symbols promised in packaging metadata, using similar AST machinery with a different promise source; the rules do not overlap.
- **dep-claim-audit** audits dependency declarations against imports. This audits entry-point declarations against source.
- **pip-check, pipdeptree and importlib.metadata checks** inspect an installed environment at runtime. This tool is static and offline and works on a repository that has never been installed.
- **twine check** validates package metadata for upload; it does not resolve entry-point targets to source symbols.

## Honest limitations

Static scanning misses dynamic symbols created by importlib, module `__getattr__` hooks, exec or eval. An existing symbol still counts even if a failing top-level import prevents actual import. Imported/re-exported names and call-result assignments have unknown runtime callability and pass. Decorators and runtime behavior are not evaluated.

Attribute chains resolve only one class-method step. Further attributes and non-class attributes remain unchecked. setup.cfg and setup.py entry_points are not parsed. The documented-command rule catches only the project's own name, not arbitrary mistyped command names. It examines the first whitespace-separated code token, so shell prompts or wrappers can hide commands. script-undocumented is heuristic: a script documented only outside README/top-level markdown/docs is flagged; use `--ignore script-undocumented`.

The fallback parser supports simple single-line quoted declarations, not full TOML syntax. Invalid TOML (with tomllib), unreadable files and invalid Python syntax produce input-error exit 1. The tool establishes static evidence, not that a built distribution includes the module or that a command will execute successfully.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

MIT licensed.
