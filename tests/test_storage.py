"""SQLite persistence.

Idempotency gets the most attention: CI retries and local experimentation both
re-present the same artifact, and double-counting would silently corrupt every rate
the tool computes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Aliased so pytest does not try to collect the dataclasses as test classes.
from flaky_detective.models import Status
from flaky_detective.models import TestOutcome as Outcome
from flaky_detective.models import TestRun as Run
from flaky_detective.storage import SCHEMA_VERSION, Storage, StorageError, add_runs


def build_run(uid: str, *, commit: str | None = "c1", failed: int = 1, branch: str = "main") -> Run:
    outcomes = [
        Outcome(
            test_id=f"t.py::test_{index}",
            name=f"test_{index}",
            status=Status.FAILED if index < failed else Status.PASSED,
            message="boom" if index < failed else None,
            signature="boom" if index < failed else None,
            position=index,
        )
        for index in range(4)
    ]
    return Run(
        run_uid=uid,
        started_at="2026-08-01T00:00:00+00:00",
        outcomes=tuple(outcomes),
        commit_sha=commit,
        branch=branch,
        runner="pytest",
    )


@pytest.fixture
def store(tmp_path: Path):
    with Storage(tmp_path / "test.db") as storage:
        yield storage


class TestSchema:
    def test_creates_the_file_and_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "deeper" / "flaky.db"
        with Storage(path):
            pass
        assert path.is_file()

    def test_records_the_schema_version(self, store: Storage) -> None:
        row = store._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert int(row["value"]) == SCHEMA_VERSION

    def test_refuses_a_newer_schema(self, tmp_path: Path) -> None:
        """Reading a future database would silently misinterpret it."""
        path = tmp_path / "future.db"
        with Storage(path) as storage:
            storage._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION + 1),),
            )
            storage._conn.commit()

        with pytest.raises(StorageError, match="Upgrade"):
            Storage(path)

    def test_reopening_is_safe(self, tmp_path: Path) -> None:
        path = tmp_path / "reopen.db"
        with Storage(path) as first:
            first.add_run(build_run("a"))
        with Storage(path) as second:
            assert second.run_count() == 1


class TestIdempotency:
    def test_first_insert_reports_new(self, store: Storage) -> None:
        _, inserted = store.add_run(build_run("uid-1"))
        assert inserted is True

    def test_duplicate_is_skipped(self, store: Storage) -> None:
        store.add_run(build_run("uid-1"))
        _, inserted = store.add_run(build_run("uid-1"))
        assert inserted is False

    def test_duplicate_does_not_double_count_results(self, store: Storage) -> None:
        store.add_run(build_run("uid-1"))
        store.add_run(build_run("uid-1"))
        assert store.run_count() == 1
        assert store.result_count() == 4

    def test_duplicate_returns_the_original_id(self, store: Storage) -> None:
        first_id, _ = store.add_run(build_run("uid-1"))
        second_id, _ = store.add_run(build_run("uid-1"))
        assert first_id == second_id

    def test_has_run(self, store: Storage) -> None:
        assert not store.has_run("uid-1")
        store.add_run(build_run("uid-1"))
        assert store.has_run("uid-1")


class TestReads:
    def test_round_trips_every_field(self, store: Storage) -> None:
        run = Run(
            run_uid="uid",
            started_at="2026-08-01T00:00:00+00:00",
            outcomes=(
                Outcome(
                    test_id="t.py::test_a",
                    name="test_a",
                    status=Status.ERROR,
                    suite="suite",
                    duration=1.5,
                    message="msg",
                    detail="detail",
                    signature="sig",
                    position=7,
                    retried=True,
                ),
            ),
            commit_sha="deadbeef",
            branch="topic",
            runner="pytest",
            iteration=3,
        )
        store.add_run(run)

        stored = store.outcomes()[0]
        assert stored.test_id == "t.py::test_a"
        assert stored.status is Status.ERROR
        assert stored.suite == "suite"
        assert stored.duration == 1.5
        assert stored.message == "msg"
        assert stored.detail == "detail"
        assert stored.signature == "sig"
        assert stored.position == 7
        assert stored.retried is True
        assert stored.commit_sha == "deadbeef"
        assert stored.branch == "topic"
        assert stored.iteration == 3

    def test_outcomes_are_chronological(self, store: Storage) -> None:
        """Flip counting depends on this ordering."""
        for index, uid in enumerate(["c", "a", "b"]):
            run = build_run(uid)
            store.add_run(
                Run(
                    run_uid=uid,
                    started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                    outcomes=run.outcomes,
                    commit_sha="c1",
                )
            )
        stamps = [o.started_at for o in store.outcomes()]
        assert stamps == sorted(stamps)

    def test_filter_by_branch(self, store: Storage) -> None:
        store.add_run(build_run("a", branch="main"))
        store.add_run(build_run("b", branch="topic"))
        assert {o.branch for o in store.outcomes(branch="topic")} == {"topic"}

    def test_filter_since(self, store: Storage) -> None:
        store.add_run(build_run("a"))
        assert store.outcomes(since="2026-09-01T00:00:00+00:00") == []
        assert store.outcomes(since="2026-07-01T00:00:00+00:00")

    def test_limit_runs_takes_the_most_recent(self, store: Storage) -> None:
        for index in range(5):
            base = build_run(f"uid{index}")
            store.add_run(
                Run(
                    run_uid=f"uid{index}",
                    started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                    outcomes=base.outcomes,
                )
            )
        recent = store.outcomes(limit_runs=2)
        assert {o.run_uid for o in recent} == {"uid3", "uid4"}

    def test_outcomes_for_one_test(self, store: Storage) -> None:
        store.add_run(build_run("a"))
        found = store.outcomes_for_test("t.py::test_0")
        assert len(found) == 1
        assert found[0].test_id == "t.py::test_0"

    def test_find_test_ids_by_fragment(self, store: Storage) -> None:
        store.add_run(build_run("a"))
        assert "t.py::test_2" in store.find_test_ids("test_2")

    def test_find_test_ids_is_a_safe_query(self, store: Storage) -> None:
        """Test ids come from user-supplied XML, so this must be parameterized."""
        store.add_run(build_run("a"))
        assert store.find_test_ids("'; DROP TABLE results; --") == []
        assert store.result_count() == 4


class TestStats:
    def test_summary(self, store: Storage) -> None:
        store.add_run(build_run("a", commit="c1"))
        store.add_run(build_run("b", commit="c2"))
        stats = store.stats()
        assert stats.runs == 2
        assert stats.results == 8
        assert stats.tests == 4
        assert stats.commits == 2
        assert stats.failures == 2
        assert stats.runners == {"pytest": 2}

    def test_empty_database(self, store: Storage) -> None:
        stats = store.stats()
        assert stats.runs == 0
        assert stats.results == 0

    def test_recent_runs(self, store: Storage) -> None:
        store.add_run(build_run("a"))
        recent = store.recent_runs()
        assert recent[0].run_uid == "a"
        assert recent[0].runner == "pytest"

    def test_recent_runs_are_newest_first(self, store: Storage) -> None:
        for index, uid in enumerate(["old", "middle", "new"]):
            base = build_run(uid)
            store.add_run(
                Run(
                    run_uid=uid,
                    started_at=f"2026-08-{index + 1:02d}T00:00:00+00:00",
                    outcomes=base.outcomes,
                )
            )
        assert [record.run_uid for record in store.recent_runs()] == ["new", "middle", "old"]


class TestMaintenance:
    def test_purge_removes_old_runs_and_cascades(self, store: Storage) -> None:
        store.add_run(build_run("a"))
        removed = store.purge("2026-09-01T00:00:00+00:00")
        assert removed == 1
        assert store.run_count() == 0
        assert store.result_count() == 0

    def test_add_runs_counts_new_and_duplicate(self, store: Storage) -> None:
        added, skipped = add_runs(store, [build_run("a"), build_run("b"), build_run("a")])
        assert (added, skipped) == (2, 1)
