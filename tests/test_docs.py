"""The documentation has to stay true, so the checks on it are enforced rather than manual.

A broken link in a README is the cheapest possible way to look careless, and somebody
following one is the worst moment to discover it. The same argument the steering files make
about architecture rules applies here: a convention that is only checked by remembering to
check it decays under time pressure.

Three claims are guarded:

- every relative link resolves to a real path
- every in-page anchor matches a real heading
- the ADR index lists every ADR, and every ADR is listed

The published *figures* are checked elsewhere and more directly: CI recomputes the
real-world validation and fails if any of them drift, which is a stronger check than
anything a docs test could do.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
ANCHOR_LINK = re.compile(r"\[([^\]]*)\]\((#[^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)

SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", ".hypothesis", "__pycache__"}


def markdown_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts)
    )


def slug(heading: str) -> str:
    """Reproduce github-slugger closely enough to check our own anchors.

    Note the whitespace rule: each whitespace character becomes its own hyphen, so a
    heading containing an em dash slugs to a double hyphen once the dash is stripped.
    Collapsing runs of whitespace here would report a false failure on every such
    heading -- which it did, on the first version of this check.
    """
    text = heading.strip().lower()
    text = text.replace("`", "")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


def test_there_are_markdown_files_to_check() -> None:
    """Guards the guard: a glob that matched nothing would pass everything below."""
    files = markdown_files()
    assert len(files) > 10
    assert ROOT / "README.md" in files


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(path: Path) -> None:
    broken: list[str] = []
    for label, target in LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = target.split("#", 1)[0]
        if not file_part:
            continue
        if not (path.parent / file_part).resolve().exists():
            broken.append(f"[{label}]({target})")

    assert not broken, f"{path.relative_to(ROOT)} has broken links: {broken}"


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_anchors_match_a_heading(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    headings = {slug(title) for title in HEADING.findall(text)}

    broken = [
        f"[{label}]({target})"
        for label, target in ANCHOR_LINK.findall(text)
        if target[1:] not in headings
    ]
    assert not broken, f"{path.relative_to(ROOT)} links to missing anchors: {broken}"


class TestDecisionRecordIndex:
    """The ADR index is the entry point to the reasoning, so it must be complete.

    Records are how this project explains the rules it got wrong first. An ADR missing
    from the index is an argument nobody will find.
    """

    @staticmethod
    def _records() -> list[Path]:
        return sorted(p for p in (ROOT / "docs" / "adr").glob("*.md") if p.name != "README.md")

    def test_every_record_is_listed(self) -> None:
        index = (ROOT / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
        missing = [p.name for p in self._records() if p.name not in index]
        assert not missing, f"not listed in docs/adr/README.md: {missing}"

    def test_numbering_has_no_gaps(self) -> None:
        numbers = sorted(int(p.name.split("-", 1)[0]) for p in self._records())
        assert numbers == list(range(1, len(numbers) + 1)), f"gap in ADR numbering: {numbers}"

    def test_every_record_states_a_status(self) -> None:
        for record in self._records():
            text = record.read_text(encoding="utf-8")
            assert "**Status:**" in text, f"{record.name} does not state a status"


class TestSpecsAreDescribed:
    """Each spec round has the three documents the workflow produces."""

    @staticmethod
    def _specs() -> list[Path]:
        return sorted(p for p in (ROOT / ".kiro" / "specs").iterdir() if p.is_dir())

    def test_each_spec_has_requirements_design_and_tasks(self) -> None:
        for spec in self._specs():
            for name in ("requirements.md", "design.md", "tasks.md"):
                assert (spec / name).is_file(), f"{spec.name} is missing {name}"

    def test_the_readme_links_every_spec(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        missing = [s.name for s in self._specs() if f".kiro/specs/{s.name}/" not in readme]
        assert not missing, f"specs not linked from the README: {missing}"
