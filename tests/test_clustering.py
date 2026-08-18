"""Failure clustering by normalized signature."""

from __future__ import annotations

from flaky_detective.analysis.clustering import cluster_failures

# Aliased so pytest does not try to collect the dataclass as a test class.
from flaky_detective.models import Status
from flaky_detective.models import TestOutcome as Outcome

from conftest import outcome


def failing(test_id: str, signature: str, run: str = "r0") -> Outcome:
    return Outcome(
        test_id=test_id,
        name=test_id,
        status=Status.FAILED,
        message=signature,
        signature=signature,
        run_uid=run,
        position=0,
    )


class TestGrouping:
    def test_one_cause_across_several_tests_is_one_cluster(self) -> None:
        outcomes = [
            failing("t1", "ConnectionRefused: <IP>:<PORT>"),
            failing("t2", "ConnectionRefused: <IP>:<PORT>"),
            failing("t3", "ConnectionRefused: <IP>:<PORT>"),
        ]
        clusters = cluster_failures(outcomes)
        assert len(clusters) == 1
        assert clusters[0].test_count == 3
        assert clusters[0].failure_count == 3

    def test_distinct_signatures_stay_separate(self) -> None:
        clusters = cluster_failures([failing("t1", "sig a"), failing("t2", "sig b")])
        assert len(clusters) == 2

    def test_repeated_failures_of_one_test_count_once_per_test(self) -> None:
        outcomes = [failing("t1", "sig", run=f"r{i}") for i in range(5)]
        clusters = cluster_failures(outcomes)
        assert clusters[0].test_count == 1
        assert clusters[0].failure_count == 5

    def test_passes_are_ignored(self) -> None:
        assert cluster_failures([outcome("t1", Status.PASSED)]) == ()

    def test_outcomes_without_a_signature_are_ignored(self) -> None:
        bare = Outcome(test_id="t", name="t", status=Status.FAILED, message="x")
        assert cluster_failures([bare]) == ()

    def test_retried_passes_are_included(self) -> None:
        """The runner observed a real failure; its message is real evidence."""
        retried = Outcome(
            test_id="t",
            name="t",
            status=Status.PASSED,
            retried=True,
            message="Timed out",
            signature="Timed out",
        )
        clusters = cluster_failures([retried])
        assert len(clusters) == 1


class TestRanking:
    def test_breadth_outranks_depth(self) -> None:
        """A cause hitting 3 tests once beats one hitting a single test 10 times.

        The single-test case is already visible in the per-test ranking, so ranking
        clusters by breadth surfaces something the rest of the report does not.
        """
        outcomes = [
            failing("a", "wide"),
            failing("b", "wide"),
            failing("c", "wide"),
            *[failing("d", "deep", run=f"r{i}") for i in range(10)],
        ]
        clusters = cluster_failures(outcomes)
        assert clusters[0].signature == "wide"

    def test_ties_break_on_signature_for_stable_output(self) -> None:
        outcomes = [failing("t1", "zebra"), failing("t2", "alpha")]
        clusters = cluster_failures(outcomes)
        assert [c.signature for c in clusters] == ["alpha", "zebra"]

    def test_min_size_filter(self) -> None:
        outcomes = [failing("a", "wide"), failing("b", "wide"), failing("c", "narrow")]
        clusters = cluster_failures(outcomes, min_size=2)
        assert len(clusters) == 1
        assert clusters[0].signature == "wide"


class TestClusterContents:
    def test_test_ids_are_sorted_and_deduplicated(self) -> None:
        outcomes = [
            failing("z", "sig", run="r0"),
            failing("a", "sig", run="r0"),
            failing("a", "sig", run="r1"),
        ]
        assert cluster_failures(outcomes)[0].test_ids == ("a", "z")

    def test_cluster_carries_a_cause(self) -> None:
        clusters = cluster_failures([failing("t", "connection refused to db:5432")])
        assert clusters[0].cause is not None
        assert str(clusters[0].cause.cause) == "network"

    def test_representative_message_is_kept(self) -> None:
        clusters = cluster_failures([failing("t", "the message")])
        assert clusters[0].representative_message == "the message"
