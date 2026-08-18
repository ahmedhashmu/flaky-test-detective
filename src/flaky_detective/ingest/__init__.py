"""Ingest orchestration: path expansion, parsing, and storage.

A single bad artifact must never lose a whole batch. Every parse failure is
collected and reported; the batch continues.

Adding a new result format means adding a module here and registering it in
PARSERS. Nothing downstream changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from ..models import IngestResult, TestRun
from ..storage import Storage
from . import junit
from .junit import ParseError

XML_SUFFIXES = frozenset({".xml"})

PARSERS = {"junit": junit.parse_file}
"""Format name -> parse callable. Registry for future formats (TAP, Allure)."""

_SKIP_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}
)


def expand_paths(paths: Sequence[str | Path]) -> list[Path]:
    """Resolve files, directories, and glob patterns to a sorted list of files.

    Sorted so that a batch ingest is deterministic, which matters because run
    ordering feeds flip detection when reports lack timestamps.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    for entry in paths:
        for candidate in _expand_one(Path(entry), str(entry)):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)

    return sorted(found, key=lambda p: str(p))


def _expand_one(path: Path, raw: str) -> Iterator[Path]:
    if any(ch in raw for ch in "*?[") and not path.exists():
        base = Path(raw).anchor or "."
        pattern = raw[len(base):] if base != "." else raw
        yield from (p for p in Path(base).glob(pattern) if p.is_file())
        return

    if path.is_dir():
        yield from _walk(path)
        return

    if path.is_file():
        yield path


def _walk(directory: Path) -> Iterator[Path]:
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            if child.name in _SKIP_DIRECTORIES:
                continue
            yield from _walk(child)
        elif child.is_file() and child.suffix.lower() in XML_SUFFIXES:
            yield child


def parse_paths(
    paths: Iterable[Path],
    *,
    commit_sha: str | None = None,
    branch: str | None = None,
    ci_run_id: str | None = None,
) -> tuple[list[TestRun], list[tuple[str, str]]]:
    """Parse many reports. Returns (runs, failures) where failures are (path, reason)."""
    runs: list[TestRun] = []
    failures: list[tuple[str, str]] = []

    for path in paths:
        try:
            runs.append(
                junit.parse_file(
                    path,
                    commit_sha=commit_sha,
                    branch=branch,
                    ci_run_id=ci_run_id,
                )
            )
        except ParseError as exc:
            failures.append((str(path), str(exc)))

    return runs, failures


def ingest_paths(
    store: Storage,
    paths: Sequence[str | Path],
    *,
    commit_sha: str | None = None,
    branch: str | None = None,
    ci_run_id: str | None = None,
) -> IngestResult:
    """Parse and store every report found under the given paths."""
    files = expand_paths(paths)
    if not files:
        return IngestResult(failures=(("(no input)", "no XML files matched"),))

    runs, failures = parse_paths(
        files, commit_sha=commit_sha, branch=branch, ci_run_id=ci_run_id
    )

    added = skipped = results = 0
    for run in runs:
        _, inserted = store.add_run(run)
        if inserted:
            added += 1
            results += run.total
        else:
            skipped += 1

    return IngestResult(
        runs_added=added,
        runs_skipped=skipped,
        results_added=results,
        failures=tuple(failures),
    )


def ingest_run(store: Storage, run: TestRun) -> bool:
    """Store a single already-parsed run. Returns whether it was new."""
    _, inserted = store.add_run(run)
    return inserted


__all__ = [
    "PARSERS",
    "ParseError",
    "expand_paths",
    "ingest_paths",
    "ingest_run",
    "junit",
    "parse_paths",
]
