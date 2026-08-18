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
            if root in {
                "flaky_detective",
                "analysis",
                "report",
                "ingest",
                "models",
                "normalize",
                "storage",
                "config",
                "runner",
                "quarantine",
                "environment",
                "cli",
            }:
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
