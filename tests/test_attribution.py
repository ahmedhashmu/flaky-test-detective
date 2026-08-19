"""Flakiness attribution.

Most of these tests are about the cases where there is no answer. Naming a commit the
data does not implicate is how someone ends up reverting an innocent change, so each
kind of "don't know" gets its own assertion.
"""

from __future__ import annotations

import pytest

from flaky_detective.analysis.attribution import blame, build_timeline
from flaky_detective.models import Attribution, Status
from flaky_detective.models import TestOutcome as Outcome

TEST_ID = "t.py::test_x"


def o(status: Status, commit: str | None, run: int, *, retried: bool = False) -> Outcome:
    return Outcome(
        test_id=TEST_ID,
        name="test_x",
        status=status,
        message="boom" if status.is_failure or retried else None,
        signature="boom" if status.is_failure or retried else None,
        position=0,
        retried=retried,
        run_uid=f"r{run}",
        commit_sha=commit,
        started_at=f"2026-08-{run + 1:02d}T00:00:00+00:00",
    )


P = Status.PASSED
F = Status.FAILED


class TestIntroduced:
    def test_finds_the_first_diverging_commit(self) -> None:
        result = blame(
            TEST_ID,
            [
                o(P, "c1", 0),
                o(P, "c1", 1),
                o(P, "c2", 2),
                o(P, "c2", 3),
                o(P, "c3", 4),
                o(F, "c3", 5),
                o(F, "c4", 6),
                o(P, "c4", 7),
            ],
        )
        assert result.attribution is Attribution.INTRODUCED
        assert result.commit_sha == "c3"
        assert result.previous_clean_sha == "c2"
        assert result.is_actionable

    def test_names_the_last_clean_commit_not_the_first(self) -> None:
        """The suspect range should be as narrow as the data allows."""
        result = blame(
            TEST_ID,
            [
                o(P, "c1", 0),
                o(P, "c1", 1),
                o(P, "c2", 2),
                o(P, "c2", 3),
                o(P, "c3", 4),
                o(P, "c3", 5),
                o(P, "c4", 6),
                o(F, "c4", 7),
            ],
        )
        assert result.commit_sha == "c4"
        assert result.previous_clean_sha == "c3"

    def test_a_retry_counts_as_divergence(self) -> None:
        """The runner watched one test do both inside a single run."""
        result = blame(
            TEST_ID,
            [o(P, "c1", 0), o(P, "c1", 1), o(P, "c2", 2, retried=True)],
        )
        assert result.attribution is Attribution.INTRODUCED
        assert result.commit_sha == "c2"

    def test_explanation_mentions_both_commits(self) -> None:
        result = blame(
            TEST_ID,
            [o(P, "c1", 0), o(P, "c1", 1), o(P, "c2", 2), o(F, "c2", 3)],
        )
        assert "c2" in result.explanation
        assert "c1" in result.explanation


class TestUnknowable:
    """Each of these must refuse to name a commit."""

    def test_divergence_at_the_earliest_commit(self) -> None:
        result = blame(TEST_ID, [o(P, "c1", 0), o(F, "c1", 1), o(P, "c2", 2), o(F, "c2", 3)])
        assert result.attribution is Attribution.PREDATES_HISTORY
        assert not result.is_actionable
        assert "before this window" in result.explanation

    def test_one_run_per_commit(self) -> None:
        """Divergence could not have been observed anywhere."""
        result = blame(TEST_ID, [o(P, "c1", 0), o(F, "c2", 1), o(P, "c3", 2)])
        assert result.attribution is Attribution.TOO_SPARSE
        assert result.observable_commits == 0
        assert "more than once" in result.explanation

    def test_no_divergence_anywhere(self) -> None:
        result = blame(TEST_ID, [o(P, "c1", 0), o(P, "c1", 1), o(F, "c2", 2), o(F, "c2", 3)])
        assert result.attribution is Attribution.NO_DIVERGENCE
        assert result.commit_sha is None

    def test_no_commit_data(self) -> None:
        result = blame(TEST_ID, [o(P, None, 0), o(F, None, 1)])
        assert result.attribution is Attribution.NO_COMMIT_DATA
        assert "--commit" in result.explanation

    def test_empty_history(self) -> None:
        assert blame(TEST_ID, []).attribution is Attribution.NO_COMMIT_DATA

    def test_earlier_commits_that_ran_once_do_not_count_as_clean(self) -> None:
        """A single run proves nothing, so it cannot serve as the clean baseline.

        Treating it as clean would let the tool blame a commit on the strength of one
        passing run beforehand, which is not evidence of anything.
        """
        result = blame(TEST_ID, [o(P, "c1", 0), o(P, "c2", 1), o(P, "c3", 2), o(F, "c3", 3)])
        assert result.attribution is Attribution.PREDATES_HISTORY


class TestTimeline:
    def test_groups_by_commit_in_first_seen_order(self) -> None:
        timeline = build_timeline([o(P, "c2", 0), o(F, "c2", 1), o(P, "c1", 2), o(P, "c1", 3)])
        assert [w.commit_sha for w in timeline] == ["c2", "c1"]

    def test_counts_passes_and_failures(self) -> None:
        window = build_timeline([o(P, "c1", 0), o(F, "c1", 1), o(F, "c1", 2)])[0]
        assert (window.runs, window.passes, window.failures) == (3, 1, 2)

    def test_diverged_flag(self) -> None:
        diverged = build_timeline([o(P, "c1", 0), o(F, "c1", 1)])[0]
        clean = build_timeline([o(P, "c2", 0), o(P, "c2", 1)])[0]
        assert diverged.diverged
        assert not clean.diverged

    def test_observable_requires_more_than_one_run(self) -> None:
        single = build_timeline([o(P, "c1", 0)])[0]
        double = build_timeline([o(P, "c2", 0), o(P, "c2", 1)])[0]
        assert not single.observable
        assert double.observable

    def test_skips_are_excluded(self) -> None:
        """A skip says nothing about whether a test is flaky."""
        timeline = build_timeline(
            [
                o(P, "c1", 0),
                Outcome(TEST_ID, "test_x", Status.SKIPPED, commit_sha="c1", run_uid="r1"),
            ]
        )
        assert timeline[0].runs == 1

    def test_outcomes_without_a_commit_are_excluded(self) -> None:
        timeline = build_timeline([o(P, None, 0), o(P, "c1", 1), o(F, "c1", 2)])
        assert [w.commit_sha for w in timeline] == ["c1"]

    def test_retry_inflates_runs_and_both_counts(self) -> None:
        """One row, but two observed outcomes."""
        window = build_timeline([o(P, "c1", 0, retried=True)])[0]
        assert window.runs == 2
        assert window.passes == 1
        assert window.failures == 1
        assert window.diverged


class TestExplanations:
    @pytest.mark.parametrize("attribution", list(Attribution))
    def test_every_case_explains_itself(self, attribution: Attribution) -> None:
        from flaky_detective.models import BlameResult

        result = BlameResult(test_id=TEST_ID, attribution=attribution, commit_sha="c1")
        assert len(result.explanation) > 40
