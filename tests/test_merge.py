"""Merging history across machines and CI shards.

The correctness argument rests on `run_uid` being a content hash, which makes merging
a set union: idempotent, and independent of the order sources are merged in. Those two
properties are what make sharded CI work, so they are asserted directly rather than
assumed from the hash.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flaky_detective.analysis import analyze
from flaky_detective.config import Config
from flaky_detective.models import Status
from flaky_detective.models import TestOutcome as Outcome
from flaky_detective.models import TestRun as Run
from flaky_detective.storage import SCHEMA_VERSION, Storage, StorageError


def build_run(
    uid: str, *, commit: str = "c1", failed: bool = True, started: str = "2026-08-01"
) -> Run:
    return Run(
        run_uid=uid,
        started_at=f"{started}T00:00:00+00:00",
        outcomes=(
            Outcome(
                test_id="t.py::test_a",
                name="test_a",
                status=Status.FAILED if failed else Status.PASSED,
                message="boom" if failed else None,
                signature="boom" if failed else None,
                position=0,
            ),
            Outcome(test_id="t.py::test_b", name="test_b", status=Status.PASSED, position=1),
        ),
        commit_sha=commit,
        branch="main",
        runner="pytest",
    )


def populate(path: Path, uids: list[str], *, commit: str = "c1") -> None:
    with Storage(path) as store:
        for index, uid in enumerate(uids):
            store.add_run(
                build_run(
                    uid,
                    commit=commit,
                    failed=index % 2 == 0,
                    started=f"2026-08-{index + 1:02d}",
                )
            )


def fingerprint(path: Path):
    """The analysis a database produces, as a comparable value."""
    with Storage(path) as store:
        report = analyze(store.outcomes(), Config())
        runs = store.run_count()
        results = store.result_count()
    return runs, results, tuple((t.test_id, t.score, str(t.verdict)) for t in report.tests)


class TestBasicMerge:
    def test_copies_missing_runs(self, tmp_path: Path) -> None:
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["r1", "r2"])
        populate(source, ["r3", "r4"])

        with Storage(target) as store:
            outcome = store.merge_from(source)
            assert store.run_count() == 4

        assert outcome.runs_added == 2
        assert outcome.runs_skipped == 0
        assert outcome.results_added == 4

    def test_copies_results_with_remapped_run_ids(self, tmp_path: Path) -> None:
        """Row ids are per-database, so the foreign key has to be re-pointed."""
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["r1"])
        populate(source, ["r9"])

        with Storage(target) as store:
            store.merge_from(source)
            outcomes = store.outcomes()

        assert len(outcomes) == 4
        # Every result must still resolve to its own run's metadata.
        assert {o.run_uid for o in outcomes} == {"r1", "r9"}
        for outcome in outcomes:
            assert outcome.commit_sha == "c1"
            assert outcome.started_at

    def test_preserves_every_field(self, tmp_path: Path) -> None:
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["r1"])

        with Storage(source) as store:
            store.add_run(
                Run(
                    run_uid="rich",
                    started_at="2026-09-09T10:00:00+00:00",
                    outcomes=(
                        Outcome(
                            test_id="t.py::test_x",
                            name="test_x",
                            status=Status.ERROR,
                            suite="suite",
                            duration=2.5,
                            message="msg",
                            detail="detail",
                            signature="sig",
                            position=7,
                            retried=True,
                        ),
                    ),
                    commit_sha="deadbeef",
                    branch="topic",
                    runner="jest",
                    iteration=4,
                    seed="99",
                )
            )

        with Storage(target) as store:
            store.merge_from(source)
            merged = next(o for o in store.outcomes() if o.test_id == "t.py::test_x")

        assert merged.status is Status.ERROR
        assert merged.suite == "suite"
        assert merged.duration == 2.5
        assert merged.detail == "detail"
        assert merged.position == 7
        assert merged.retried is True
        assert merged.commit_sha == "deadbeef"
        assert merged.branch == "topic"
        assert merged.iteration == 4


class TestIdempotency:
    def test_merging_twice_changes_nothing(self, tmp_path: Path) -> None:
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["r1", "r2"])
        populate(source, ["r3"])

        with Storage(target) as store:
            store.merge_from(source)
        after_first = fingerprint(target)

        with Storage(target) as store:
            second = store.merge_from(source)
        after_second = fingerprint(target)

        assert second.runs_added == 0
        assert second.runs_skipped == 1
        assert after_first == after_second

    def test_overlapping_sources_collapse(self, tmp_path: Path) -> None:
        """Two machines that ingested the same artifact must not double-count it."""
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["shared", "only-a"])
        populate(source, ["shared", "only-b"])

        with Storage(target) as store:
            outcome = store.merge_from(source)
            assert store.run_count() == 3

        assert outcome.runs_added == 1
        assert outcome.runs_skipped == 1

    def test_merging_a_subset_is_a_no_op(self, tmp_path: Path) -> None:
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["r1", "r2", "r3"])
        populate(source, ["r1", "r2"])

        before = fingerprint(target)
        with Storage(target) as store:
            outcome = store.merge_from(source)
        assert outcome.runs_added == 0
        assert fingerprint(target) == before


class TestOrderIndependence:
    def test_a_into_b_equals_b_into_a(self, tmp_path: Path) -> None:
        """The property that makes sharded CI trustworthy."""
        left, right = tmp_path / "left.db", tmp_path / "right.db"
        populate(left, ["l1", "l2"], commit="c1")
        populate(right, ["r1", "r2"], commit="c1")

        ab, ba = tmp_path / "ab.db", tmp_path / "ba.db"
        import shutil

        shutil.copy(left, ab)
        shutil.copy(right, ba)

        with Storage(ab) as store:
            store.merge_from(right)
        with Storage(ba) as store:
            store.merge_from(left)

        assert fingerprint(ab) == fingerprint(ba)

    def test_three_way_merge_in_any_order(self, tmp_path: Path) -> None:
        import itertools
        import shutil

        sources = []
        for index in range(3):
            path = tmp_path / f"s{index}.db"
            populate(path, [f"s{index}-r1", f"s{index}-r2"])
            sources.append(path)

        fingerprints = set()
        for order_index, order in enumerate(itertools.permutations(sources)):
            target = tmp_path / f"merged{order_index}.db"
            shutil.copy(order[0], target)
            with Storage(target) as store:
                for source in order[1:]:
                    store.merge_from(source)
            fingerprints.add(fingerprint(target))

        assert len(fingerprints) == 1, "merge order changed the result"


class TestRefusals:
    def test_missing_source(self, tmp_path: Path) -> None:
        target = tmp_path / "a.db"
        populate(target, ["r1"])
        with Storage(target) as store, pytest.raises(StorageError, match="No database"):
            store.merge_from(tmp_path / "absent.db")

    def test_merging_into_itself(self, tmp_path: Path) -> None:
        target = tmp_path / "a.db"
        populate(target, ["r1"])
        with Storage(target) as store, pytest.raises(StorageError, match="into itself"):
            store.merge_from(target)

    def test_newer_schema_is_refused(self, tmp_path: Path) -> None:
        """Silently misreading a future database would corrupt the history."""
        target, source = tmp_path / "a.db", tmp_path / "b.db"
        populate(target, ["r1"])
        populate(source, ["r2"])

        with Storage(source) as store:
            store._conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION + 5),),
            )
            store._conn.commit()

        with Storage(target) as store, pytest.raises(StorageError, match="Upgrade"):
            store.merge_from(source)

    def test_not_a_database(self, tmp_path: Path) -> None:
        target = tmp_path / "a.db"
        populate(target, ["r1"])
        junk = tmp_path / "junk.db"
        junk.write_text("this is not sqlite")

        with Storage(target) as store, pytest.raises(StorageError):
            store.merge_from(junk)

    def test_target_is_unchanged_after_a_refusal(self, tmp_path: Path) -> None:
        target = tmp_path / "a.db"
        populate(target, ["r1"])
        before = fingerprint(target)

        with Storage(target) as store, pytest.raises(StorageError):
            store.merge_from(tmp_path / "absent.db")

        assert fingerprint(target) == before


class TestMergeImprovesAnalysis:
    def test_pooling_reveals_divergence_neither_source_could_see(self, tmp_path: Path) -> None:
        """The reason merging matters at all.

        One machine only ever saw the test pass; another only ever saw it fail. Neither
        can call it flaky. Pooled, the same commit shows both outcomes, which is proof.
        """
        passing, failing = tmp_path / "pass.db", tmp_path / "fail.db"

        with Storage(passing) as store:
            for index in range(4):
                store.add_run(build_run(f"p{index}", failed=False, started=f"2026-08-0{index + 1}"))
        with Storage(failing) as store:
            for index in range(4):
                store.add_run(build_run(f"f{index}", failed=True, started=f"2026-08-1{index}"))

        with Storage(passing) as store:
            report = analyze(store.outcomes(), Config())
            alone = next(t for t in report.tests if t.test_id == "t.py::test_a")
        assert alone.divergent_commits == 0

        with Storage(passing) as store:
            store.merge_from(failing)
            report = analyze(store.outcomes(), Config())
            pooled = next(t for t in report.tests if t.test_id == "t.py::test_a")

        assert pooled.divergent_commits == 1
        assert pooled.score > alone.score
