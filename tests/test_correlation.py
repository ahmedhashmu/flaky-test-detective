"""Tests for environment correlation.

The failure modes worth guarding are all about restraint:

- one machine means one value per dimension, which explains nothing;
- a test that is flaky at the same rate everywhere must not be given an environment;
- a dimension with a distinct value per run carries no grouping information;
- two dimensions that split the runs identically must be reported as indistinguishable,
  not as two independent causes.

An association is measured, so it is allowed to be reported as evidence. It is still not a
mechanism, and nothing here may word it as one.
"""

from __future__ import annotations

import pytest

from flaky_detective.analysis.correlation import (
    MAX_VALUES_PER_DIMENSION,
    MIN_LIFT,
    MIN_RUNS_PER_VALUE,
    detect_environment_association,
)
from flaky_detective.models import Status

from conftest import outcome


def runs(pattern: str, labels_per_run: list[dict[str, str]], *, test_id: str = "t.py::a") -> list:
    """One outcome per character, each run carrying its own labels.

    `pattern` uses `.` for pass and `F` for fail, matching the conftest helper.
    """
    assert len(pattern) == len(labels_per_run)
    return [
        outcome(
            test_id,
            Status.PASSED if char == "." else Status.FAILED,
            run=f"r{index}",
            commit="c1",
            message=None if char == "." else "AssertionError: boom",
        )
        for index, char in enumerate(pattern)
    ]


def labels_for(labels_per_run: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {f"r{index}": values for index, values in enumerate(labels_per_run)}


def split(arch_a: str, count_a: int, arch_b: str, count_b: int) -> list[dict[str, str]]:
    return [{"arch": arch_a}] * count_a + [{"arch": arch_b}] * count_b


class TestFindsRealAssociations:
    def test_a_test_failing_mostly_on_one_arch_is_reported(self) -> None:
        # 2/20 on x86, 16/20 on arm.
        pattern = "." * 18 + "FF" + "F" * 16 + "...."
        label_list = split("x86_64", 20, "arm64", 20)

        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))

        assert found
        strongest = found[0]
        assert strongest.dimension == "arch"
        assert strongest.value == "arm64"
        assert strongest.failures == 16
        assert strongest.runs == 20
        assert strongest.other_failures == 2
        assert strongest.lift > MIN_LIFT
        assert strongest.probability < 0.05

    def test_the_counts_behind_the_claim_are_carried(self) -> None:
        pattern = "." * 18 + "FF" + "F" * 16 + "...."
        label_list = split("x86_64", 20, "arm64", 20)
        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))

        association = found[0]
        assert association.failure_rate == pytest.approx(16 / 20)
        assert association.other_rate == pytest.approx(2 / 20)
        assert association.summary == "arch=arm64"
        assert association.values_considered >= 2

    def test_strongest_association_comes_first(self) -> None:
        label_list = [
            {"arch": "arm64", "os": "linux"} if index >= 20 else {"arch": "x86_64", "os": "linux"}
            for index in range(40)
        ]
        pattern = "." * 18 + "FF" + "F" * 16 + "...."
        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        # `os` has one value throughout, so it is not testable and must not appear.
        assert [a.dimension for a in found] == ["arch"]


class TestRestraint:
    def test_a_single_environment_explains_nothing(self) -> None:
        """The normal case for a database built on one laptop."""
        label_list = [{"arch": "arm64", "os": "darwin"}] * 30
        pattern = (".F" * 15)[:30]
        assert (
            detect_environment_association(runs(pattern, label_list), labels_for(label_list)) == ()
        )

    def test_a_test_equally_flaky_everywhere_gets_no_association(self) -> None:
        """The control. Same failure rate on both arches, so nothing to say."""
        label_list = split("x86_64", 20, "arm64", 20)
        pattern = ("..F." * 5) + ("..F." * 5)
        assert (
            detect_environment_association(runs(pattern, label_list), labels_for(label_list)) == ()
        )

    def test_a_test_that_never_fails_gets_no_association(self) -> None:
        label_list = split("x86_64", 20, "arm64", 20)
        assert (
            detect_environment_association(runs("." * 40, label_list), labels_for(label_list)) == ()
        )

    def test_a_test_that_always_fails_gets_no_association(self) -> None:
        """Broken everywhere is not environment-dependent."""
        label_list = split("x86_64", 20, "arm64", 20)
        assert (
            detect_environment_association(runs("F" * 40, label_list), labels_for(label_list)) == ()
        )

    def test_a_value_with_too_few_runs_is_not_testable(self) -> None:
        thin = MIN_RUNS_PER_VALUE - 1
        label_list = split("x86_64", 30, "arm64", thin)
        pattern = "." * 30 + "F" * thin
        assert (
            detect_environment_association(runs(pattern, label_list), labels_for(label_list)) == ()
        )

    def test_a_dimension_with_a_value_per_run_is_ignored(self) -> None:
        """A run id recorded as a label carries no grouping information.

        Testing every value would spend the whole multiplicity budget on noise.
        """
        count = MAX_VALUES_PER_DIMENSION + 4
        label_list = [{"run_id": f"unique-{index}"} for index in range(count)]
        pattern = ("F" * (count // 2)) + ("." * (count - count // 2))
        assert (
            detect_environment_association(runs(pattern, label_list), labels_for(label_list)) == ()
        )

    def test_no_labels_means_no_answer(self) -> None:
        label_list = split("x86_64", 20, "arm64", 20)
        assert detect_environment_association(runs("." * 20 + "F" * 20, label_list), {}) == ()

    def test_a_small_difference_is_not_reported(self) -> None:
        """Detectable and useless: 10% against 8% tells nobody where to look."""
        label_list = split("x86_64", 25, "arm64", 25)
        pattern = ("." * 23 + "FF") + ("." * 22 + "FFF")
        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        assert all(a.lift >= MIN_LIFT for a in found)


class TestConfounding:
    def test_dimensions_splitting_the_runs_identically_are_named_together(self) -> None:
        """Every ARM runner having two CPUs makes the two dimensions one finding.

        Reporting them as separate causes would send someone hunting a CPU-count bug.
        """
        label_list = [
            {"arch": "arm64", "cpus": "2"} if index >= 20 else {"arch": "x86_64", "cpus": "4"}
            for index in range(40)
        ]
        pattern = "." * 18 + "FF" + "F" * 16 + "...."

        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        summaries = {a.summary for a in found}
        assert summaries == {"arch=arm64", "cpus=2"}
        for association in found:
            assert association.is_confounded
            assert association.covaries_with

    def test_independent_dimensions_are_not_marked_confounded(self) -> None:
        # arch splits at 20; python alternates, so the two do not coincide.
        label_list = [
            {
                "arch": "arm64" if index >= 20 else "x86_64",
                "python": "3.11" if index % 2 == 0 else "3.12",
            }
            for index in range(40)
        ]
        pattern = "." * 18 + "FF" + "F" * 16 + "...."

        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        arch = next(a for a in found if a.dimension == "arch")
        assert not arch.is_confounded

    def test_a_lone_association_is_never_confounded(self) -> None:
        label_list = split("x86_64", 20, "arm64", 20)
        pattern = "." * 18 + "FF" + "F" * 16 + "...."
        found = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        assert len(found) == 1
        assert not found[0].is_confounded


class TestDeterminism:
    def test_the_same_history_gives_the_same_answer(self) -> None:
        label_list = split("x86_64", 20, "arm64", 20)
        pattern = "." * 18 + "FF" + "F" * 16 + "...."
        first = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        second = detect_environment_association(runs(pattern, label_list), labels_for(label_list))
        assert first == second

    def test_retries_count_as_failures(self) -> None:
        """A runner-recorded retry is the runner stating the test flaked."""
        label_list = split("x86_64", 20, "arm64", 20)
        outcomes = runs("." * 40, label_list)
        retried = [
            outcome(
                "t.py::a",
                Status.PASSED,
                run=f"r{index}",
                commit="c1",
                retried=True,
                message="AssertionError: boom",
            )
            if index >= 24
            else outcomes[index]
            for index in range(40)
        ]
        found = detect_environment_association(retried, labels_for(label_list))
        assert found
        assert found[0].value == "arm64"
