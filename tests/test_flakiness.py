"""Flakiness scoring and verdict assignment.

The verdict tests matter more than the score tests. Calling a real regression
"flaky" teaches the user to re-run instead of investigate, which is the habit this
tool exists to break, so those cases are asserted from several directions.
"""

from __future__ import annotations

from flaky_detective.analysis import analyze
from flaky_detective.analysis.flakiness import analyze_test
from flaky_detective.config import Config
from flaky_detective.models import Status, Verdict

from conftest import sequence


def score_for(pattern: str, commits: list[str] | None = None, **kwargs) -> float:
    return analyze_one_pattern(pattern, commits, **kwargs).score


def analyze_one_pattern(pattern: str, commits: list[str] | None = None, **kwargs):
    settings = Config(**kwargs)
    return analyze_test(
        "t.py::test_x",
        sequence("t.py::test_x", pattern, commits=commits),
        threshold=settings.flake_threshold,
        confidence_runs=settings.confidence_runs,
        fixed_run_streak=settings.fixed_run_streak,
    )


class TestFlipCounting:
    def test_alternating_history_is_all_flips(self) -> None:
        result = analyze_one_pattern(".F.F.F.F")
        assert result.flips == 7
        assert result.flip_rate == 1.0

    def test_stable_history_has_no_flips(self) -> None:
        assert analyze_one_pattern("........").flips == 0

    def test_single_transition(self) -> None:
        result = analyze_one_pattern("....FFFF")
        assert result.flips == 1

    def test_skips_are_excluded_from_evidence(self) -> None:
        """A skip says nothing about whether a test is flaky."""
        result = analyze_one_pattern(".ss.")
        assert result.runs == 2
        assert result.skips == 2
        assert result.flips == 0

    def test_single_run_cannot_flip(self) -> None:
        result = analyze_one_pattern("F")
        assert result.flips == 0
        assert result.flip_rate == 0.0


class TestSameCommitDivergence:
    def test_pass_and_fail_at_one_commit_is_divergence(self) -> None:
        result = analyze_one_pattern(".F", commits=["c1", "c1"])
        assert result.divergent_commits == 1
        assert result.observed_commits == 1
        assert result.divergence_rate == 1.0

    def test_commits_with_a_single_run_are_not_counted(self) -> None:
        """One run at a commit cannot show divergence, so it is not evidence."""
        result = analyze_one_pattern(".F", commits=["c1", "c2"])
        assert result.observed_commits == 0
        assert result.divergence_rate == 0.0

    def test_consistent_failures_at_one_commit_are_not_divergence(self) -> None:
        result = analyze_one_pattern("FF", commits=["c1", "c1"])
        assert result.divergent_commits == 0
        assert result.observed_commits == 1

    def test_partial_divergence(self) -> None:
        result = analyze_one_pattern(".F..", commits=["c1", "c1", "c2", "c2"])
        assert (result.divergent_commits, result.observed_commits) == (1, 2)
        assert result.divergence_rate == 0.5

    def test_a_retry_alone_proves_divergence(self) -> None:
        """The runner observed both outcomes inside one run."""
        result = analyze_one_pattern("R", commits=["c1"])
        assert result.retries == 1
        assert result.divergent_commits == 1


class TestScoring:
    def test_divergence_outranks_flips(self) -> None:
        """Same flip rate, but only one has same-commit proof."""
        with_proof = score_for(".F.F.F.F.F", commits=["c1"] * 10)
        without = score_for(".F.F.F.F.F")
        assert with_proof > without

    def test_score_is_bounded(self) -> None:
        assert 0.0 <= score_for(".F" * 20, commits=["c1"] * 40) <= 1.0

    def test_stable_scores_zero(self) -> None:
        assert score_for("." * 20) == 0.0

    def test_confidence_damps_small_samples(self) -> None:
        """Identical evidence, different sample size: more runs must score higher."""
        few = score_for(".F.F", commits=["c1"] * 4)
        many = score_for(".F" * 10, commits=["c1"] * 20)
        assert many > few

    def test_confidence_never_hides_a_new_flake(self) -> None:
        """The floor exists so a fresh flake still surfaces above the threshold."""
        result = analyze_one_pattern(".F.", commits=["c1", "c1", "c1"])
        assert result.score >= Config().flake_threshold

    def test_falls_back_to_flip_rate_without_commits(self) -> None:
        result = analyze_one_pattern(".F.F.F.F.F.F")
        assert result.observed_commits == 0
        assert result.score > 0


class TestVerdicts:
    def test_stable(self) -> None:
        assert analyze_one_pattern("." * 12).verdict is Verdict.STABLE

    def test_flaky(self) -> None:
        result = analyze_one_pattern(".F" * 6, commits=["c1"] * 12)
        assert result.verdict is Verdict.FLAKY

    def test_broken_when_it_never_passed(self) -> None:
        """Never passing is usually an incomplete commit, and never a flake."""
        assert analyze_one_pattern("F" * 12).verdict is Verdict.BROKEN

    def test_regression_with_commit_evidence(self) -> None:
        result = analyze_one_pattern("........FFFF", commits=["c1"] * 8 + ["c2"] * 4)
        assert result.verdict is Verdict.REGRESSION

    def test_regression_without_commit_evidence(self) -> None:
        assert analyze_one_pattern("........FFFF").verdict is Verdict.REGRESSION

    def test_unlucky_flake_is_not_a_regression(self) -> None:
        """A test that has flipped repeatedly and then failed three times is flaky.

        This case was a real bug: the trailing-failure rule alone reported it as a
        regression, which would send someone hunting a bad commit that does not
        exist.
        """
        result = analyze_one_pattern(".F.F.F.F.FFF")
        assert result.flips >= 8
        assert result.verdict is Verdict.FLAKY

    def test_divergence_at_the_newest_commit_beats_the_streak(self) -> None:
        """Still diverging at the latest commit means flakiness is the live cause."""
        result = analyze_one_pattern("....F.FFF", commits=["c1"] * 4 + ["c2"] * 5)
        assert result.verdict is Verdict.FLAKY

    def test_fixed_after_a_clean_streak(self) -> None:
        result = analyze_one_pattern(".F.F" + "." * 10)
        assert result.verdict is Verdict.FIXED
        assert result.consecutive_passes == 10

    def test_fixed_streak_is_configurable(self) -> None:
        result = analyze_one_pattern(".F" + "." * 4, fixed_run_streak=4)
        assert result.verdict is Verdict.FIXED

    def test_empty_history(self) -> None:
        result = analyze_test("t", [], threshold=0.15, confidence_runs=10, fixed_run_streak=10)
        assert result.verdict is Verdict.STABLE
        assert result.runs == 0


class TestEvidenceFields:
    def test_counts(self) -> None:
        result = analyze_one_pattern(".F.sF")
        assert (result.runs, result.passes, result.failures, result.skips) == (4, 2, 2, 1)

    def test_window(self) -> None:
        result = analyze_one_pattern("...")
        assert result.first_seen == "2026-08-01T00:00:00+00:00"
        assert result.last_seen == "2026-08-03T00:00:00+00:00"

    def test_last_status(self) -> None:
        assert analyze_one_pattern("..F").last_status is Status.FAILED

    def test_failure_rate_ignores_skips(self) -> None:
        result = analyze_one_pattern("Fs.")
        assert result.failure_rate == 0.5

    def test_signatures_are_ranked_by_frequency(self) -> None:
        from conftest import outcome

        outcomes = [
            outcome("t", Status.FAILED, run="r1", message="rare"),
            outcome("t", Status.FAILED, run="r2", message="common"),
            outcome("t", Status.FAILED, run="r3", message="common"),
            outcome("t", Status.PASSED, run="r4"),
        ]
        result = analyze_test(
            "t", outcomes, threshold=0.15, confidence_runs=10, fixed_run_streak=10
        )
        assert result.signatures[0] == "common"


class TestDeterminism:
    def test_repeated_analysis_gives_identical_output(self) -> None:
        outcomes = sequence("t.py::test_x", ".F.F.F", commits=["c1"] * 6)
        first = analyze(outcomes, Config())
        second = analyze(outcomes, Config())
        assert [t.test_id for t in first.tests] == [t.test_id for t in second.tests]
        assert [t.score for t in first.tests] == [t.score for t in second.tests]

    def test_equal_scores_break_ties_on_test_id(self) -> None:
        outcomes = sequence("t.py::zebra", "..") + sequence("t.py::alpha", "..")
        result = analyze(outcomes, Config())
        assert [t.test_id for t in result.tests] == ["t.py::alpha", "t.py::zebra"]


class TestReportLevel:
    def test_ignore_patterns_exclude_tests(self) -> None:
        outcomes = sequence("t.py::test_keep", ".F") + sequence("t.py::test_drop", ".F")
        result = analyze(outcomes, Config(ignore=("test_drop",)))
        assert [t.test_id for t in result.tests] == ["t.py::test_keep"]

    def test_commit_coverage_is_reported(self) -> None:
        outcomes = sequence("t.py::test_x", ".F", commits=["c1", "c1"])
        result = analyze(outcomes, Config())
        assert result.has_commit_data
        assert result.commit_coverage == 1.0

    def test_missing_commit_data_is_flagged(self) -> None:
        result = analyze(sequence("t.py::test_x", ".F"), Config())
        assert not result.has_commit_data
