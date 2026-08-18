"""Quarantine: taking a flaky test out of the way, on a deadline.

Quarantine is a tourniquet, not a cure. Every entry therefore carries an expiry
date, because quarantine without expiry is deletion with extra steps and everyone
knows it. When an entry expires the tool re-checks it against current history and
says whether it can be released.

Exports are runner-native and meant to be pasted directly into a command or config.
Where that is not possible for a runner, this module says so instead of emitting
something that looks like a working command but is not.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Config
from .models import AnalysisReport, TestAnalysis, Verdict

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    """One quarantined test."""

    test_id: str
    reason: str
    score: float
    added_at: str
    expires_at: str
    runner: str | None = None
    added_by: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        try:
            deadline = datetime.fromisoformat(self.expires_at)
        except ValueError:
            # An unparseable expiry is treated as expired rather than as
            # permanent. Failing open here would make quarantine forever.
            return True
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return moment >= deadline

    def days_remaining(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        try:
            deadline = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return 0
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return max(0, (deadline - moment).days)


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    """Result of re-checking expired quarantine entries against fresh history."""

    releasable: tuple[QuarantineEntry, ...] = ()
    still_flaky: tuple[QuarantineEntry, ...] = ()
    unknown: tuple[QuarantineEntry, ...] = ()
    """Expired entries with no recent runs. Usually means the test is still
    quarantined and so has stopped producing evidence, which is the trap this
    command exists to catch."""

    @property
    def checked(self) -> int:
        return len(self.releasable) + len(self.still_flaky) + len(self.unknown)


class Quarantine:
    """A JSON-backed quarantine list.

    Plain JSON so it can be committed, reviewed in a pull request, and argued about
    in a code review, which is exactly the friction quarantine decisions deserve.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, QuarantineEntry] = {}
        self.load()

    # -- persistence ----------------------------------------------------------

    def load(self) -> None:
        if not self.path.is_file():
            self._entries = {}
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read quarantine file {self.path}: {exc}") from exc

        items = raw.get("quarantined", []) if isinstance(raw, dict) else raw
        entries: dict[str, QuarantineEntry] = {}
        for item in items:
            if not isinstance(item, dict) or "test_id" not in item:
                continue
            entries[str(item["test_id"])] = QuarantineEntry(
                test_id=str(item["test_id"]),
                reason=str(item.get("reason", "")),
                score=float(item.get("score", 0.0)),
                added_at=str(item.get("added_at", "")),
                expires_at=str(item.get("expires_at", "")),
                runner=item.get("runner"),
                added_by=item.get("added_by"),
            )
        self._entries = entries

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "quarantined": [asdict(e) for e in self.sorted_entries()],
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # -- queries --------------------------------------------------------------

    def sorted_entries(self) -> list[QuarantineEntry]:
        return sorted(self._entries.values(), key=lambda e: (-e.score, e.test_id))

    @property
    def entries(self) -> list[QuarantineEntry]:
        return self.sorted_entries()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, test_id: str) -> bool:
        return test_id in self._entries

    def get(self, test_id: str) -> QuarantineEntry | None:
        return self._entries.get(test_id)

    def active(self, now: datetime | None = None) -> list[QuarantineEntry]:
        return [e for e in self.sorted_entries() if not e.is_expired(now)]

    def expired(self, now: datetime | None = None) -> list[QuarantineEntry]:
        return [e for e in self.sorted_entries() if e.is_expired(now)]

    # -- mutation -------------------------------------------------------------

    def add(
        self,
        test_id: str,
        *,
        reason: str,
        score: float = 0.0,
        days: int = 14,
        runner: str | None = None,
        added_by: str | None = None,
        now: datetime | None = None,
    ) -> QuarantineEntry:
        """Add or refresh an entry, returning what was stored."""
        moment = now or datetime.now(UTC)
        entry = QuarantineEntry(
            test_id=test_id,
            reason=reason,
            score=round(score, 4),
            added_at=moment.isoformat(timespec="seconds"),
            expires_at=(moment + timedelta(days=days)).isoformat(timespec="seconds"),
            runner=runner,
            added_by=added_by,
        )
        self._entries[test_id] = entry
        return entry

    def renew(self, test_id: str, *, days: int = 14, now: datetime | None = None) -> bool:
        existing = self._entries.get(test_id)
        if existing is None:
            return False
        moment = now or datetime.now(UTC)
        self._entries[test_id] = replace(
            existing,
            expires_at=(moment + timedelta(days=days)).isoformat(timespec="seconds"),
        )
        return True

    def remove(self, test_id: str) -> bool:
        return self._entries.pop(test_id, None) is not None


def recommend(report: AnalysisReport, config: Config | None = None) -> list[TestAnalysis]:
    """Tests whose score justifies removing them from the suite.

    The bar is higher than the flake threshold on purpose: naming a flake is cheap,
    removing coverage is not. Regressions and broken tests are never recommended,
    because quarantining a real failure is how bugs reach production.
    """
    settings = config or Config()
    candidates = [
        t
        for t in report.tests
        if t.verdict is Verdict.FLAKY and t.score >= settings.quarantine_threshold
    ]
    return sorted(candidates, key=lambda t: (-t.score, t.test_id))


def verify(
    quarantine: Quarantine,
    report: AnalysisReport,
    *,
    config: Config | None = None,
    now: datetime | None = None,
) -> VerifyOutcome:
    """Re-check expired entries against current history.

    Note the trap this exists to catch: a quarantined test usually stops running,
    so it stops producing evidence, so it can never prove itself stable. Those
    entries land in `unknown`, and the honest answer is to un-quarantine them and
    watch, not to leave them buried.
    """
    settings = config or Config()
    by_id = {t.test_id: t for t in report.tests}

    releasable: list[QuarantineEntry] = []
    still_flaky: list[QuarantineEntry] = []
    unknown: list[QuarantineEntry] = []

    for entry in quarantine.expired(now):
        current = by_id.get(entry.test_id)
        if current is None or current.runs == 0:
            unknown.append(entry)
        elif current.verdict is Verdict.FLAKY and current.score >= settings.flake_threshold:
            still_flaky.append(entry)
        else:
            releasable.append(entry)

    return VerifyOutcome(
        releasable=tuple(releasable),
        still_flaky=tuple(still_flaky),
        unknown=tuple(unknown),
    )


# -- exporters ----------------------------------------------------------------

EXPORT_FORMATS = (
    "pytest-deselect",
    "pytest-conftest",
    "jest",
    "list",
    "json",
)


def export(
    entries: list[QuarantineEntry], fmt: str = "list", *, now: datetime | None = None
) -> str:
    """Render the quarantine list in a runner-native form."""
    active = [e for e in entries if not e.is_expired(now)]

    if fmt == "list":
        return "".join(f"{e.test_id}\n" for e in active)
    if fmt == "json":
        return json.dumps([asdict(e) for e in active], indent=2) + "\n"
    if fmt == "pytest-deselect":
        return _pytest_deselect(active)
    if fmt == "pytest-conftest":
        return _pytest_conftest(active)
    if fmt == "jest":
        return _jest(active)

    raise ValueError(f"Unknown export format {fmt!r}. Available: {', '.join(EXPORT_FORMATS)}")


def _pytest_deselect(entries: list[QuarantineEntry]) -> str:
    """Flags for a pytest invocation.

    Works because test ids are reconstructed as real pytest nodeids at ingest.
    """
    if not entries:
        return "# No active quarantine entries.\n"

    args = " ".join(f'--deselect "{e.test_id}"' for e in entries)
    return f"# Quarantined flaky tests. Paste after your pytest command:\n{args}\n"


def _pytest_conftest(entries: list[QuarantineEntry]) -> str:
    """A conftest.py fragment that skips quarantined tests by nodeid.

    Preferred over `--deselect` for CI, because it keeps the tests visible in the
    report as skipped-with-a-reason rather than silently vanishing. A quarantined
    test nobody can see is a quarantined test nobody will fix.
    """
    listed = "\n".join(f"    {e.test_id!r}," for e in entries)
    body = listed if listed else "    # No active quarantine entries."

    return f'''"""Generated by flaky-test-detective. Do not edit by hand.

Regenerate with:
    flaky quarantine export --format pytest-conftest > conftest_quarantine.py

Skipped rather than deselected on purpose: a quarantined test still appears in the
report, with a reason, so it stays visible enough to get fixed.
"""

import pytest

QUARANTINED = {{
{body}
}}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.nodeid in QUARANTINED:
            item.add_marker(
                pytest.mark.skip(reason="quarantined: known flaky (flaky-test-detective)")
            )
'''


def _jest(entries: list[QuarantineEntry]) -> str:
    """Jest test names, with an honest note about what Jest can and cannot do.

    jest-junit's default output records the describe path, not the file path, so
    there is nothing to build `testPathIgnorePatterns` from. Jest also has no
    negative form of `testNamePattern`. Emitting a config snippet here would look
    like a working feature and would not be one, so the names are listed and the
    limitation stated.
    """
    if not entries:
        return "// No active quarantine entries.\n"

    names = "\n".join(f"//   {e.test_id}" for e in entries)
    return f"""// Quarantined flaky tests, as reported by jest-junit:
{names}
//
// Jest has no flag that skips a list of test names: testNamePattern only selects,
// it does not exclude, and testPathIgnorePatterns needs file paths, which
// jest-junit does not record by default.
//
// Two options that do work:
//   1. Mark them in source with test.skip, referencing this list.
//   2. Re-run jest-junit with addFileAttribute=true so file paths are recorded,
//      then exclude whole files with testPathIgnorePatterns.
"""
