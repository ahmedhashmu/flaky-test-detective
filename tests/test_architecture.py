"""Enforce the dependency rules from .kiro/steering/structure.md.

Those rules are what keep the analysis testable without a database and the
reporters honest. Written down in prose they decay on the first hurried change, so
they are checked here instead.

The dependency direction is one way:

    cli -> report -> analysis -> storage -> models

with `runner` beside `ingest` as a producer that feeds the same pipeline.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import ClassVar

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "flaky_detective"


def imports_of(path: Path) -> set[str]:
    """Every module named by an import in one file, absolute or relative.

    Relative imports are resolved to a dotted name rooted at the package so that
    `from ..storage import Storage` is comparable with `import sqlite3`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    found.add(node.module)
                    found.update(f"{node.module}.{a.name}" for a in node.names)
            else:
                # A relative import: `.` is this module's package, `..` its parent.
                base = path.relative_to(PACKAGE).parts[:-1]
                trimmed = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join(trimmed)
                target = f"{prefix}.{node.module}" if node.module else prefix
                target = target.lstrip(".")

                if target:
                    found.add(target)
                    found.update(f"{target}.{alias.name}" for alias in node.names)
                else:
                    # `from . import x` in a root module. Whether `x` is a submodule
                    # or a plain name cannot be told apart without importing, and it
                    # does not matter: a relative import is always internal, so it is
                    # recorded under the package name.
                    found.update(f"flaky_detective.{alias.name}" for alias in node.names)

    return {name for name in found if name}


def _internal_names() -> set[str]:
    """Every name that refers to something inside this package.

    Derived from the package layout rather than hardcoded, because a hardcoded list
    goes stale the moment a module is added -- which is exactly what happened when
    `benchmark/` landed and this test failed for the wrong reason.
    """
    names = {"flaky_detective"}
    names |= {path.stem for path in PACKAGE.glob("*.py")}
    names |= {
        path.name
        for path in PACKAGE.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    }
    return names


def modules_in(subpackage: str) -> list[Path]:
    root = PACKAGE / subpackage if subpackage else PACKAGE
    return sorted(p for p in root.glob("*.py"))


def all_modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


class TestAnalysisIsPure:
    """`analysis` takes lists of outcomes and returns conclusions.

    If it reached for a database, every scoring test would need fixtures and a
    filesystem, and the scoring rules would stop being cheap to verify.
    """

    @pytest.mark.parametrize("module", modules_in("analysis"), ids=lambda p: p.name)
    def test_does_not_touch_sqlite(self, module: Path) -> None:
        assert not {"sqlite3"} & imports_of(module)

    @pytest.mark.parametrize("module", modules_in("analysis"), ids=lambda p: p.name)
    def test_does_not_import_storage(self, module: Path) -> None:
        offenders = {name for name in imports_of(module) if "storage" in name}
        assert not offenders, f"{module.name} imports {offenders}"

    @pytest.mark.parametrize("module", modules_in("analysis"), ids=lambda p: p.name)
    def test_does_not_read_the_filesystem(self, module: Path) -> None:
        offenders = {name for name in imports_of(module) if name in {"os", "pathlib", "shutil"}}
        assert not offenders, f"{module.name} imports {offenders}"


class TestReportOnlyFormats:
    """Reporters format; they do not compute.

    A derived number calculated inside one reporter is a number the other three
    will eventually disagree with.
    """

    @pytest.mark.parametrize("module", modules_in("report"), ids=lambda p: p.name)
    def test_does_not_import_storage(self, module: Path) -> None:
        offenders = {name for name in imports_of(module) if "storage" in name}
        assert not offenders, f"{module.name} imports {offenders}"

    @pytest.mark.parametrize("module", modules_in("report"), ids=lambda p: p.name)
    def test_does_not_touch_sqlite(self, module: Path) -> None:
        assert not {"sqlite3"} & imports_of(module)

    @pytest.mark.parametrize("module", modules_in("report"), ids=lambda p: p.name)
    def test_does_not_import_the_analysis_engine(self, module: Path) -> None:
        """Reporters receive an AnalysisReport; they must not produce one."""
        offenders = {
            name
            for name in imports_of(module)
            if name.startswith("analysis") and "classify" not in name
        }
        assert not offenders, f"{module.name} imports {offenders}"


class TestWebIsAPresentationLayer:
    """`web` serializes an analysis; it does not produce one or query for one.

    The dashboard's whole claim is that it cannot show a verdict the terminal would
    not. That holds only while every number it renders came from `analysis`, so the
    same rule the reporters live under applies here, with one difference: `web` is
    allowed to import `storage`, because something has to open the database. It is
    not allowed to talk to SQLite itself.
    """

    @pytest.mark.parametrize("module", modules_in("web"), ids=lambda p: p.name)
    def test_does_not_touch_sqlite(self, module: Path) -> None:
        assert not {"sqlite3"} & imports_of(module)

    @pytest.mark.parametrize("module", modules_in("web"), ids=lambda p: p.name)
    def test_defines_no_scoring_constants(self, module: Path) -> None:
        """Weights and thresholds live in `analysis`, in one place, or they drift.

        A penalty ceiling copied into the payload builder would let the dashboard
        and the CLI disagree about the same suite, which is the one failure this
        layer is designed to make impossible.
        """
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        offenders = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
            and re.search(r"WEIGHT|PENALTY|THRESHOLD|SATURATION", target.id)
        ]
        assert not offenders, f"{module.name} defines {offenders}"

    @pytest.mark.parametrize(
        "subpackage", ["analysis", "report", "ingest", "benchmark", ""], ids=lambda s: s or "root"
    )
    def test_nothing_upstream_imports_web(self, subpackage: str) -> None:
        """One-way direction. `cli` is the only module allowed to reach for it."""
        for module in modules_in(subpackage):
            if module.name == "cli.py":
                continue
            # Both spellings: `from ..web import api` resolves to "web", while
            # `from . import web` in a root module resolves to the fully dotted name.
            offenders = {
                name
                for name in imports_of(module)
                if name.split(".")[0] == "web" or name == "flaky_detective.web"
            }
            assert not offenders, f"{module.name} imports {offenders}"


class TestModelsAndNormalizeAreLeaves:
    """These two import nothing from the package, which is what lets everything
    else import them without a cycle."""

    @pytest.mark.parametrize("name", ["models.py", "normalize.py"])
    def test_no_internal_imports(self, name: str) -> None:
        internal = {
            found
            for found in imports_of(PACKAGE / name)
            if found.split(".")[0]
            in {"analysis", "report", "ingest", "storage", "runner", "cli", "config", "quarantine"}
        }
        assert not internal, f"{name} imports {internal}"


class TestRuntimeDependencies:
    """Runtime dependencies stay at typer and rich.

    Every extra dependency is a reason for someone not to install a tool that
    solves a side-concern. Parsing, storage and hashing use the standard library.
    """

    ALLOWED_THIRD_PARTY: ClassVar[frozenset[str]] = frozenset({"typer", "rich", "click"})

    @pytest.mark.parametrize("module", all_modules(), ids=lambda p: str(p.name))
    def test_no_unexpected_third_party_imports(self, module: Path) -> None:
        offenders = set()
        for name in imports_of(module):
            root = name.split(".")[0]
            if root in self.ALLOWED_THIRD_PARTY:
                continue
            if root in sys.stdlib_module_names:
                continue
            if root in _internal_names():
                continue
            offenders.add(root)

        assert not offenders, f"{module.name} imports unexpected third party: {offenders}"

    def test_parsing_uses_the_standard_library(self) -> None:
        found = imports_of(PACKAGE / "ingest" / "junit.py")
        assert any("xml.etree" in name for name in found)
        assert not {"lxml", "defusedxml"} & {n.split(".")[0] for n in found}


class TestXmlSafety:
    """Reports arrive as CI artifacts, so they are untrusted input."""

    def test_no_unsafe_xml_parsers(self) -> None:
        for module in all_modules():
            found = imports_of(module)
            assert "xml.dom.minidom" not in found, f"{module.name} uses minidom"
            assert "xml.sax" not in found, f"{module.name} uses sax"

    def test_entity_declarations_are_refused(self) -> None:
        """The guard itself is tested in test_junit.py; this checks it still exists."""
        source = (PACKAGE / "ingest" / "junit.py").read_text(encoding="utf-8")
        assert "_ENTITY_DECLARATION" in source
        assert "refusing to parse" in source


class TestCliStaysThin:
    def test_no_scoring_constants_in_the_cli(self) -> None:
        """Weights live in analysis/flakiness.py, in one place, or they drift."""
        source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
        for forbidden in ("DIVERGENCE_WEIGHT", "FLIP_WEIGHT", "CONFIDENCE_FLOOR"):
            assert forbidden not in source

    def test_exit_codes_are_defined_once(self) -> None:
        source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
        for name in ("EXIT_OK", "EXIT_FLAKY", "EXIT_REGRESSION", "EXIT_USAGE"):
            assert f"{name} = " in source


class TestDocumentation:
    def test_every_module_has_a_docstring(self) -> None:
        """Steering requires one: a module nobody can summarize is a design smell."""
        missing = []
        for module in all_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            if ast.get_docstring(tree) is None:
                missing.append(module.name)
        assert not missing, f"missing module docstrings: {missing}"

    def test_no_todo_comments(self) -> None:
        """Steering rule: either do it, or write it in the spec."""
        offenders = []
        for module in all_modules():
            for number, line in enumerate(module.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") and ("TODO" in stripped or "FIXME" in stripped):
                    offenders.append(f"{module.name}:{number}")
        assert not offenders, f"TODO/FIXME left in committed code: {offenders}"


class TestKiroHooks:
    """Validate .kiro/hooks/*.json against the documented v1 hook schema.

    A hook with a misplaced field does not raise anything; it simply never fires,
    and a hook that never fires is indistinguishable from one that was never
    written. These files are part of the project's record of how it was built, so
    they are checked rather than assumed.

    The schema was confirmed against https://kiro.dev/docs/hooks.md. `timeout` is a
    hook-level field, not part of `action` -- which is where it originally was in
    all three of these files.
    """

    HOOKS_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent / ".kiro" / "hooks"

    VALID_TRIGGERS: ClassVar[frozenset[str]] = frozenset(
        {
            "SessionStart",
            "Stop",
            "UserPromptSubmit",
            "PreTaskExec",
            "PostTaskExec",
            "PreToolUse",
            "PostToolUse",
            "PostFileCreate",
            "PostFileSave",
            "PostFileDelete",
        }
    )
    HOOK_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"name", "description", "trigger", "matcher", "timeout", "action", "enabled", "confirm"}
    )
    ACTION_FIELDS: ClassVar[frozenset[str]] = frozenset({"type", "command", "prompt"})

    # Triggers whose matcher is documented as ignored. Setting one would imply a
    # filter that does not exist.
    MATCHERLESS: ClassVar[frozenset[str]] = frozenset(
        {"SessionStart", "Stop", "PreTaskExec", "PostTaskExec"}
    )

    def hook_files(self) -> list[Path]:
        return sorted(self.HOOKS_DIR.glob("*.json"))

    def test_hooks_exist(self) -> None:
        assert self.hook_files(), "no hook files found"

    def test_every_expected_behaviour_is_present(self) -> None:
        names = {
            hook["name"]
            for path in self.hook_files()
            for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]
        }
        assert names == {
            "Lint and format check on save",
            "Architecture guard",
            "Accuracy guard",
            "Test after spec task",
        }

    def test_files_are_valid_json(self) -> None:
        for path in self.hook_files():
            json.loads(path.read_text(encoding="utf-8"))

    def test_schema_version(self) -> None:
        for path in self.hook_files():
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data.get("version") == "v1", path.name
            assert set(data) == {"version", "hooks"}, path.name
            assert isinstance(data["hooks"], list) and data["hooks"], path.name

    def test_required_fields_and_valid_triggers(self) -> None:
        for path in self.hook_files():
            for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]:
                assert not set(hook) - self.HOOK_FIELDS, f"{path.name}: unknown fields"
                for required in ("name", "trigger", "action"):
                    assert required in hook, f"{path.name}: missing {required}"
                assert hook["trigger"] in self.VALID_TRIGGERS, f"{path.name}: {hook['trigger']}"

    def test_timeout_is_a_hook_level_field(self) -> None:
        """Regression test. `timeout` inside `action` is silently ignored."""
        for path in self.hook_files():
            for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]:
                action = hook["action"]
                assert "timeout" not in action, f"{path.name}: timeout nested inside action"
                assert not set(action) - self.ACTION_FIELDS, f"{path.name}: unknown action fields"

    def test_actions_carry_what_their_type_requires(self) -> None:
        for path in self.hook_files():
            for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]:
                action = hook["action"]
                assert action["type"] in {"command", "agent"}, path.name
                if action["type"] == "command":
                    assert action.get("command"), f"{path.name}: no command"
                else:
                    assert action.get("prompt"), f"{path.name}: no prompt"

    def test_matchers_are_valid_regexes(self) -> None:
        for path in self.hook_files():
            for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]:
                if "matcher" in hook:
                    re.compile(hook["matcher"])

    def test_no_matcher_on_triggers_that_ignore_it(self) -> None:
        for path in self.hook_files():
            for hook in json.loads(path.read_text(encoding="utf-8"))["hooks"]:
                if hook["trigger"] in self.MATCHERLESS:
                    assert "matcher" not in hook, f"{path.name}: {hook['trigger']} ignores matcher"

    @pytest.mark.parametrize(
        ("hook_file", "path", "should_match"),
        [
            ("lint-on-save", "src/flaky_detective/cli.py", True),
            ("lint-on-save", "tests/test_cli.py", True),
            ("lint-on-save", "README.md", False),
            ("architecture-guard", "src/flaky_detective/analysis/flakiness.py", True),
            ("architecture-guard", "src/flaky_detective/report/console.py", True),
            ("architecture-guard", "src/flaky_detective/web/api.py", True),
            ("architecture-guard", "src/flaky_detective/storage.py", False),
            ("architecture-guard", "tests/test_flakiness.py", False),
            # The compiled bundle lives under web/ but is build output, not source.
            ("architecture-guard", "src/flaky_detective/web/static/assets/index-abc.js", False),
        ],
    )
    def test_matchers_select_the_intended_files(
        self, hook_file: str, path: str, should_match: bool
    ) -> None:
        hook = json.loads((self.HOOKS_DIR / f"{hook_file}.json").read_text(encoding="utf-8"))
        matcher = hook["hooks"][0]["matcher"]
        assert (re.search(matcher, path) is not None) is should_match


class TestPackagingMetadata:
    """Guard the install paths the README documents.

    Dev dependencies live in two places for a reason: `uv sync` reads
    `[dependency-groups]` (PEP 735), while plain `pip install -e ".[dev]"` reads
    `[project.optional-dependencies]`. pip could not install dependency groups at
    all before 25.1, and the README's test instructions have to work with the pip
    people already have.

    Two places is one place too many to maintain by hand, so the group references
    the extra rather than repeating it, and that arrangement is asserted here.
    """

    @staticmethod
    def pyproject() -> dict:
        import tomllib

        path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with path.open("rb") as handle:
            return tomllib.load(handle)

    def test_dev_extra_exists(self) -> None:
        """`pip install -e ".[dev]"` must actually provide the dev tools."""
        extras = self.pyproject()["project"]["optional-dependencies"]
        assert "dev" in extras
        names = " ".join(extras["dev"])
        for tool in ("pytest", "ruff", "mypy", "pytest-randomly"):
            assert tool in names, f"{tool} missing from the dev extra"

    def test_dev_group_references_the_extra_rather_than_duplicating_it(self) -> None:
        """One list of pins, so the two install paths cannot drift apart."""
        group = self.pyproject()["dependency-groups"]["dev"]
        assert group == ["flaky-test-detective[dev]"], (
            "the dev dependency group should reference the dev extra, not repeat it"
        )

    def test_runtime_dependencies_stay_at_two(self) -> None:
        deps = self.pyproject()["project"]["dependencies"]
        roots = sorted(re.split(r"[<>=!~\[]", d)[0].strip() for d in deps)
        assert roots == ["rich", "typer"], f"runtime dependencies drifted: {roots}"

    def test_supported_python_versions_are_declared(self) -> None:
        project = self.pyproject()["project"]
        assert project["requires-python"] == ">=3.11"
        classifiers = " ".join(project["classifiers"])
        for version in ("3.11", "3.12", "3.13", "3.14"):
            assert version in classifiers, f"Python {version} missing from classifiers"

    def test_ci_matrix_covers_every_declared_version(self) -> None:
        """A version claimed in the classifiers but untested is a claim, not a fact."""
        workflow = (
            Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")
        match = re.search(r"python:\s*\[([^\]]+)\]", workflow)
        assert match, "could not find the python matrix in ci.yml"
        tested = {v.strip().strip('"').strip("'") for v in match.group(1).split(",")}
        assert tested == {"3.11", "3.12", "3.13", "3.14"}, f"matrix is {sorted(tested)}"
