"""Quarantine list, expiry, and runner-native exports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flaky_detective.analysis import analyze
from flaky_detective.config import Config
from flaky_detective.models import Verdict
from flaky_detective.quarantine import (
    EXPORT_FORMATS,
    Quarantine,
    export,
    recommend,
    verify,
)

from conftest import sequence

NOW = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Quarantine:
    return Quarantine(tmp_path / "q.json")


class TestPersistence:
    def test_missing_file_starts_empty(self, tmp_path: Path) -> None:
        assert len(Quarantine(tmp_path / "absent.json")) == 0

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "q.json"
        first = Quarantine(path)
        first.add("t.py::test_a", reason="timeout", score=0.7, now=NOW)
        first.save()

        second = Quarantine(path)
        assert len(second) == 1
        entry = second.get("t.py::test_a")
        assert entry is not None
        assert entry.reason == "timeout"
        assert entry.score == 0.7

    def test_malformed_file_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "q.json"
        path.write_text("{not json")
        with pytest.raises(ValueError, match="Could not read quarantine"):
            Quarantine(path)

    def test_entries_without_a_test_id_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "q.json"
        path.write_text('{"quarantined": [{"reason": "orphan"}]}')
        assert len(Quarantine(path)) == 0

    def test_saved_file_is_readable_json(self, store: Quarantine) -> None:
        import json

        store.add("t.py::test_a", reason="x", now=NOW)
        store.save()
        payload = json.loads(store.path.read_text())
        assert payload["schema_version"] == 1
        assert payload["quarantined"][0]["test_id"] == "t.py::test_a"


class TestExpiry:
    def test_every_entry_gets_a_deadline(self, store: Quarantine) -> None:
        """Quarantine without expiry is deletion with extra steps."""
        entry = store.add("t", reason="x", days=14, now=NOW)
        assert entry.expires_at
        assert entry.expires_at > entry.added_at

    def test_not_expired_before_the_deadline(self, store: Quarantine) -> None:
        entry = store.add("t", reason="x", days=14, now=NOW)
        assert not entry.is_expired(NOW + timedelta(days=13))

    def test_expired_after_the_deadline(self, store: Quarantine) -> None:
        entry = store.add("t", reason="x", days=14, now=NOW)
        assert entry.is_expired(NOW + timedelta(days=15))

    def test_days_remaining(self, store: Quarantine) -> None:
        entry = store.add("t", reason="x", days=14, now=NOW)
        assert entry.days_remaining(NOW + timedelta(days=4)) == 10

    def test_days_remaining_never_negative(self, store: Quarantine) -> None:
        entry = store.add("t", reason="x", days=1, now=NOW)
        assert entry.days_remaining(NOW + timedelta(days=99)) == 0

    def test_unparseable_expiry_counts_as_expired(self, tmp_path: Path) -> None:
        """Failing open here would make quarantine permanent."""
        path = tmp_path / "q.json"
        path.write_text('{"quarantined": [{"test_id": "t", "expires_at": "soon"}]}')
        entry = Quarantine(path).get("t")
        assert entry is not None
        assert entry.is_expired()

    def test_active_and_expired_partition(self, store: Quarantine) -> None:
        store.add("old", reason="x", days=1, now=NOW)
        store.add("new", reason="x", days=30, now=NOW)
        later = NOW + timedelta(days=5)
        assert [e.test_id for e in store.expired(later)] == ["old"]
        assert [e.test_id for e in store.active(later)] == ["new"]

    def test_renew_extends_the_deadline(self, store: Quarantine) -> None:
        store.add("t", reason="x", days=1, now=NOW)
        assert store.renew("t", days=30, now=NOW)
        assert not store.get("t").is_expired(NOW + timedelta(days=10))  # type: ignore[union-attr]

    def test_renew_unknown_test(self, store: Quarantine) -> None:
        assert store.renew("absent") is False


class TestMutation:
    def test_contains(self, store: Quarantine) -> None:
        store.add("t", reason="x", now=NOW)
        assert "t" in store
        assert "other" not in store

    def test_remove(self, store: Quarantine) -> None:
        store.add("t", reason="x", now=NOW)
        assert store.remove("t") is True
        assert len(store) == 0

    def test_remove_unknown(self, store: Quarantine) -> None:
        assert store.remove("absent") is False

    def test_adding_twice_refreshes_rather_than_duplicates(self, store: Quarantine) -> None:
        store.add("t", reason="first", now=NOW)
        store.add("t", reason="second", now=NOW)
        assert len(store) == 1
        assert store.get("t").reason == "second"  # type: ignore[union-attr]

    def test_entries_are_ranked_by_score(self, store: Quarantine) -> None:
        store.add("low", reason="x", score=0.2, now=NOW)
        store.add("high", reason="x", score=0.9, now=NOW)
        assert [e.test_id for e in store.entries] == ["high", "low"]


class TestRecommend:
    def test_only_above_the_quarantine_threshold(self) -> None:
        settings = Config(flake_threshold=0.1, quarantine_threshold=0.6)

        # Every run at one commit, alternating: divergence is maximal, so this is
        # about as proven as flakiness gets.
        outcomes = sequence("t.py::very_flaky", ".F" * 8, commits=["c1"] * 16)

        # Spread across five commits with one lone failure: only one of the five
        # diverges, so the rate stays low and the score stays below the bar.
        spread = [c for c in ("c1", "c2", "c3", "c4", "c5") for _ in range(2)]
        outcomes += sequence("t.py::mildly_flaky", "......F...", commits=spread)

        recommended = [t.test_id for t in recommend(analyze(outcomes, settings), settings)]
        assert "t.py::very_flaky" in recommended
        assert "t.py::mildly_flaky" not in recommended

    def test_regressions_are_never_recommended(self) -> None:
        """Quarantining a real failure is how bugs reach production.

        The commits matter: the failures have to sit on a *later* commit than the
        passes. All twelve runs on one commit would be same-commit divergence, which
        is the definition of a flake, not a regression.
        """
        settings = Config(quarantine_threshold=0.0)
        outcomes = sequence("t.py::regressed", "........FFFF", commits=["c1"] * 8 + ["c2"] * 4)
        result = analyze(outcomes, settings)
        assert result.tests[0].verdict is Verdict.REGRESSION
        assert recommend(result, settings) == []

    def test_broken_tests_are_never_recommended(self) -> None:
        settings = Config(quarantine_threshold=0.0)
        outcomes = sequence("t.py::broken", "F" * 12, commits=["c1"] * 12)
        assert recommend(analyze(outcomes, settings), settings) == []

    def test_sorted_by_score(self) -> None:
        settings = Config(quarantine_threshold=0.1)
        outcomes = sequence("t.py::a", ".F" * 8, commits=["c1"] * 16)
        outcomes += sequence("t.py::b", ".F" * 3 + "." * 10, commits=["c1"] * 16)
        scores = [t.score for t in recommend(analyze(outcomes, settings), settings)]
        assert scores == sorted(scores, reverse=True)


class TestVerify:
    def test_still_flaky_entries_are_reported(self, store: Quarantine) -> None:
        store.add("t.py::test_x", reason="x", days=1, now=NOW)
        outcomes = sequence("t.py::test_x", ".F" * 8, commits=["c1"] * 16)
        result = verify(store, analyze(outcomes, Config()), now=NOW + timedelta(days=5))
        assert [e.test_id for e in result.still_flaky] == ["t.py::test_x"]

    def test_now_stable_entries_are_releasable(self, store: Quarantine) -> None:
        store.add("t.py::test_x", reason="x", days=1, now=NOW)
        outcomes = sequence("t.py::test_x", "." * 20, commits=["c1"] * 20)
        result = verify(store, analyze(outcomes, Config()), now=NOW + timedelta(days=5))
        assert [e.test_id for e in result.releasable] == ["t.py::test_x"]

    def test_entries_with_no_recent_runs_are_unknown(self, store: Quarantine) -> None:
        """The trap: a quarantined test stops running, so it stops proving anything."""
        store.add("t.py::never_ran", reason="x", days=1, now=NOW)
        outcomes = sequence("t.py::something_else", "..", commits=["c1"] * 2)
        result = verify(store, analyze(outcomes, Config()), now=NOW + timedelta(days=5))
        assert [e.test_id for e in result.unknown] == ["t.py::never_ran"]

    def test_unexpired_entries_are_not_checked(self, store: Quarantine) -> None:
        store.add("t.py::test_x", reason="x", days=30, now=NOW)
        outcomes = sequence("t.py::test_x", "." * 20, commits=["c1"] * 20)
        result = verify(store, analyze(outcomes, Config()), now=NOW + timedelta(days=1))
        assert result.checked == 0


class TestExports:
    @pytest.fixture
    def populated(self, store: Quarantine) -> Quarantine:
        store.add("tests/test_a.py::test_one", reason="timeout", score=0.8, now=NOW)
        store.add("tests/test_b.py::TestCls::test_two", reason="race", score=0.6, now=NOW)
        return store

    @pytest.mark.parametrize("fmt", EXPORT_FORMATS)
    def test_every_format_produces_output(self, populated: Quarantine, fmt: str) -> None:
        assert export(populated.entries, fmt, now=NOW).strip()

    def test_unknown_format_is_an_error(self, populated: Quarantine) -> None:
        with pytest.raises(ValueError, match="Unknown export format"):
            export(populated.entries, "toml")

    def test_expired_entries_are_excluded(self, populated: Quarantine) -> None:
        """An expired quarantine must stop suppressing the test."""
        output = export(populated.entries, "list", now=NOW + timedelta(days=90))
        assert output.strip() == ""

    def test_list_format(self, populated: Quarantine) -> None:
        lines = export(populated.entries, "list", now=NOW).strip().splitlines()
        assert lines == [
            "tests/test_a.py::test_one",
            "tests/test_b.py::TestCls::test_two",
        ]

    def test_pytest_deselect_format(self, populated: Quarantine) -> None:
        output = export(populated.entries, "pytest-deselect", now=NOW)
        assert '--deselect "tests/test_a.py::test_one"' in output

    def test_pytest_conftest_is_valid_python(self, populated: Quarantine) -> None:
        """A generated file that does not compile is worse than none."""
        source = export(populated.entries, "pytest-conftest", now=NOW)
        compile(source, "conftest_quarantine.py", "exec")
        assert "pytest_collection_modifyitems" in source
        assert "tests/test_a.py::test_one" in source

    def test_pytest_conftest_compiles_when_empty(self, store: Quarantine) -> None:
        compile(export(store.entries, "pytest-conftest"), "empty.py", "exec")

    def test_jest_export_states_its_limitation(self, populated: Quarantine) -> None:
        """Jest cannot exclude by test name, and the export must say so."""
        output = export(populated.entries, "jest", now=NOW)
        assert "testNamePattern only selects" in output
        assert "tests/test_a.py::test_one" in output

    def test_json_export_is_parseable(self, populated: Quarantine) -> None:
        import json

        assert len(json.loads(export(populated.entries, "json", now=NOW))) == 2
