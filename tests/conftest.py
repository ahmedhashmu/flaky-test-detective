"""Shared test fixtures and builders."""

from __future__ import annotations

from pathlib import Path

import pytest

from flaky_detective.models import Status, TestOutcome

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def outcome(
    test_id: str,
    status: Status,
    *,
    run: str = "r0",
    commit: str | None = None,
    position: int = 0,
    message: str | None = None,
    started_at: str | None = None,
    retried: bool = False,
    name: str | None = None,
) -> TestOutcome:
    """Build one outcome.

    Analysis is pure by design, so tests construct data directly instead of going
    through a parser and a database.
    """
    return TestOutcome(
        test_id=test_id,
        name=name or test_id.rsplit("::", 1)[-1],
        status=status,
        message=message,
        signature=message,
        position=position,
        retried=retried,
        run_uid=run,
        commit_sha=commit,
        started_at=started_at or "2026-08-01T00:00:00+00:00",
    )


def sequence(
    test_id: str,
    pattern: str,
    *,
    commits: list[str] | None = None,
    message: str = "AssertionError: boom",
    position: int = 0,
) -> list[TestOutcome]:
    """Build a chronological run of outcomes from a compact pattern string.

    `pattern` uses one character per run: `.` passed, `F` failed, `E` error,
    `s` skipped, `R` passed-after-retry. Reading `"..FF.F"` as a history is far
    easier than reading six constructor calls.
    """
    codes = {
        ".": (Status.PASSED, False),
        "F": (Status.FAILED, False),
        "E": (Status.ERROR, False),
        "s": (Status.SKIPPED, False),
        "R": (Status.PASSED, True),
    }

    built: list[TestOutcome] = []
    for index, char in enumerate(pattern):
        status, retried = codes[char]
        built.append(
            outcome(
                test_id,
                status,
                run=f"run-{index}",
                commit=commits[index] if commits else None,
                position=position,
                message=message if (status.is_failure or retried) else None,
                started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                retried=retried,
            )
        )
    return built
