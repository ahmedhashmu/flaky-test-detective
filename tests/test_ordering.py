"""Order-dependence detection.

Precision matters more than recall here. Telling someone a test is order dependent
sends them hunting for leaked state, so a false positive costs an afternoon. The
negative cases below are therefore as load-bearing as the positive ones, and
several of them exist because an earlier version of this detector got them wrong.
"""

from __future__ import annotations

import random

from flaky_detective.analysis.ordering import (
    build_ordering_index,
    build_predecessor_index,
    detect_order_dependence,
)
from flaky_detective.models import Status

from conftest import outcome


def run_layout(run: str, layout: list[tuple[str, Status]]) -> list[outcome]:  # type: ignore[valid-type]
    """Build one run from an ordered list of (test_id, status)."""
    return [
        outcome(
            test_id,
            status,
            run=run,
            position=position,
            commit="c1",
            message="AssertionError: boom" if status.is_failure else None,
        )
        for position, (test_id, status) in enumerate(layout)
    ]


def polluted_suite(runs: int = 12, victim_fails_after_polluter: bool = True):
    """A suite where the victim fails exactly when it runs after the polluter."""
    outcomes = []
    for index in range(runs):
        polluter_first = index % 2 == 0
        victim_status = (
            Status.FAILED if (polluter_first and victim_fails_after_polluter) else Status.PASSED
        )

        if polluter_first:
            layout = [
                ("a", Status.PASSED),
                ("b", Status.PASSED),
                ("polluter", Status.PASSED),
                ("victim", victim_status),
                ("c", Status.PASSED),
            ]
        else:
            layout = [
                ("a", Status.PASSED),
                ("victim", victim_status),
                ("b", Status.PASSED),
                ("c", Status.PASSED),
                ("polluter", Status.PASSED),
            ]
        outcomes.extend(run_layout(f"r{index}", layout))
    return outcomes


class TestPredecessorIndex:
    def test_maps_each_test_to_the_one_before_it(self) -> None:
        outcomes = run_layout("r0", [("a", Status.PASSED), ("b", Status.PASSED)])
        index = build_predecessor_index(outcomes)
        assert index[("r0", "b")] == "a"

    def test_first_test_has_no_predecessor(self) -> None:
        outcomes = run_layout("r0", [("a", Status.PASSED), ("b", Status.PASSED)])
        assert ("r0", "a") not in build_predecessor_index(outcomes)

    def test_runs_are_kept_separate(self) -> None:
        outcomes = run_layout("r0", [("a", Status.PASSED), ("b", Status.PASSED)])
        outcomes += run_layout("r1", [("z", Status.PASSED), ("b", Status.PASSED)])
        index = build_predecessor_index(outcomes)
        assert index[("r0", "b")] == "a"
        assert index[("r1", "b")] == "z"

    def test_ignores_outcomes_without_a_position(self) -> None:
        outcomes = [outcome("a", Status.PASSED, run="r0", position=None)]  # type: ignore[arg-type]
        assert build_predecessor_index(outcomes) == {}


class TestPositiveDetection:
    def test_names_the_polluter(self) -> None:
        outcomes = polluted_suite()
        index = build_ordering_index(outcomes)
        victim = [o for o in outcomes if o.test_id == "victim"]

        evidence = detect_order_dependence(victim, index)
        assert evidence is not None
        assert evidence.likely_polluter == "polluter"
        assert evidence.polluter_failure_share == 1.0

    def test_reports_position_means(self) -> None:
        outcomes = polluted_suite()
        victim = [o for o in outcomes if o.test_id == "victim"]
        evidence = detect_order_dependence(victim, build_ordering_index(outcomes))
        assert evidence is not None
        assert evidence.mean_position_on_fail == 3.0
        assert evidence.mean_position_on_pass == 1.0


class TestNegativeDetection:
    """Each of these was a false positive at some point in development."""

    def test_no_evidence_without_a_predecessor_index(self) -> None:
        outcomes = polluted_suite()
        victim = [o for o in outcomes if o.test_id == "victim"]
        assert detect_order_dependence(victim, None) is None

    def test_constant_position_cannot_explain_anything(self) -> None:
        """A suite that never reorders gives position zero explanatory power."""
        outcomes = []
        for index in range(12):
            status = Status.FAILED if index % 2 else Status.PASSED
            outcomes.extend(run_layout(f"r{index}", [("a", Status.PASSED), ("victim", status)]))
        victim = [o for o in outcomes if o.test_id == "victim"]
        assert detect_order_dependence(victim, build_ordering_index(outcomes)) is None

    def test_too_few_observations(self) -> None:
        outcomes = polluted_suite(runs=4)
        victim = [o for o in outcomes if o.test_id == "victim"]
        assert detect_order_dependence(victim, build_ordering_index(outcomes)) is None

    def test_randomly_failing_test_is_not_order_dependent(self) -> None:
        """The original detector flagged exactly this case.

        The victim fails on a coin flip, independent of position, while the suite is
        shuffled. Any correlation between position and outcome is noise.
        """
        rng = random.Random(1234)
        names = ["a", "b", "c", "d", "victim", "e", "f"]
        outcomes = []
        for index in range(30):
            order = names[:]
            rng.shuffle(order)
            layout = [
                (
                    name,
                    Status.FAILED if (name == "victim" and rng.random() < 0.5) else Status.PASSED,
                )
                for name in order
            ]
            outcomes.extend(run_layout(f"r{index}", layout))

        victim = [o for o in outcomes if o.test_id == "victim"]
        assert detect_order_dependence(victim, build_ordering_index(outcomes)) is None

    def test_mostly_failing_test_cannot_blame_a_neighbour(self) -> None:
        """A test that fails four runs in five fails after everything.

        This is the second false positive: with a shuffled suite, whichever test
        happens to precede it will show a high failure share purely by chance.
        """
        rng = random.Random(99)
        names = ["a", "b", "c", "victim", "d", "e"]
        outcomes = []
        for index in range(30):
            order = names[:]
            rng.shuffle(order)
            layout = [
                (
                    name,
                    Status.FAILED if (name == "victim" and rng.random() < 0.8) else Status.PASSED,
                )
                for name in order
            ]
            outcomes.extend(run_layout(f"r{index}", layout))

        victim = [o for o in outcomes if o.test_id == "victim"]
        assert detect_order_dependence(victim, build_ordering_index(outcomes)) is None

    def test_always_passing_test(self) -> None:
        outcomes = polluted_suite(victim_fails_after_polluter=False)
        victim = [o for o in outcomes if o.test_id == "victim"]
        assert detect_order_dependence(victim, build_ordering_index(outcomes)) is None


class TestOrderingIndex:
    def test_records_candidates_nearest_first(self) -> None:
        outcomes = run_layout(
            "r0",
            [
                ("a", Status.PASSED),
                ("b", Status.PASSED),
                ("c", Status.PASSED),
                ("d", Status.PASSED),
            ],
        )
        index = build_ordering_index(outcomes, window=3)
        assert index[("r0", "d")] == (("c", 1), ("b", 2), ("a", 3))

    def test_window_bounds_how_far_back_it_looks(self) -> None:
        outcomes = run_layout(
            "r0",
            [("a", Status.PASSED), ("b", Status.PASSED), ("c", Status.PASSED)],
        )
        assert build_ordering_index(outcomes, window=1)[("r0", "c")] == (("b", 1),)
        assert [c for c, _ in build_ordering_index(outcomes, window=2)[("r0", "c")]] == ["b", "a"]

    def test_a_window_below_one_finds_nothing(self) -> None:
        outcomes = run_layout("r0", [("a", Status.PASSED), ("b", Status.PASSED)])
        assert build_ordering_index(outcomes, window=0) == {}

    def test_runs_stay_separate(self) -> None:
        outcomes = run_layout("r0", [("a", Status.PASSED), ("victim", Status.PASSED)])
        outcomes += run_layout("r1", [("z", Status.PASSED), ("victim", Status.PASSED)])
        index = build_ordering_index(outcomes)
        assert index[("r0", "victim")] == (("a", 1),)
        assert index[("r1", "victim")] == (("z", 1),)


class TestPolluterAtADistance:
    """The case ADR-0004 could not see and ADR-0014 measured.

    A polluter two tests back is invisible to a distance-1 search, and naming it must not
    come at the cost of naming a bystander.
    """

    @staticmethod
    def suite_with_gap(runs: int = 16, gap: int = 2) -> list:
        """Victim fails exactly when the polluter ran `gap` tests earlier.

        Spacers run before the victim in both layouts, so only the polluter's presence
        differs -- otherwise a spacer correlates perfectly and no detector could tell them
        apart. That mistake was made in the generator first; see ADR-0014.
        """
        outcomes = []
        spacers = [f"spacer{i}" for i in range(gap - 1)]

        for index in range(runs):
            run = f"r{index}"
            polluted = index % 2 == 0
            slot = 0
            layout = []

            if polluted:
                layout.append(("lead", slot, Status.PASSED))
                slot += 1
                layout.append(("polluter", slot, Status.PASSED))
                for spacer in spacers:
                    slot += 1
                    layout.append((spacer, slot, Status.PASSED))
                layout.append(("victim", slot + 1, Status.FAILED))
            else:
                layout.append(("lead", slot, Status.PASSED))
                for spacer in spacers:
                    slot += 1
                    layout.append((spacer, slot, Status.PASSED))
                layout.append(("victim", slot + 1, Status.PASSED))
                layout.append(("polluter", slot + 3, Status.PASSED))

            for test_id, position, status in layout:
                outcomes.append(
                    outcome(
                        test_id,
                        status,
                        run=run,
                        commit="c1",
                        position=position,
                        message="AssertionError: state leaked" if status is Status.FAILED else None,
                    )
                )
        return outcomes

    def test_a_distance_one_search_cannot_see_it(self) -> None:
        outcomes = self.suite_with_gap(gap=2)
        victim = [o for o in outcomes if o.test_id == "victim"]
        evidence = detect_order_dependence(victim, build_ordering_index(outcomes, window=1))
        # Declines rather than blaming the spacer that was adjacent.
        assert evidence is None or evidence.likely_polluter != "spacer0"

    def test_a_wider_search_names_the_real_polluter(self) -> None:
        outcomes = self.suite_with_gap(gap=2)
        victim = [o for o in outcomes if o.test_id == "victim"]
        evidence = detect_order_dependence(victim, build_ordering_index(outcomes, window=6))

        assert evidence is not None
        assert evidence.likely_polluter == "polluter"
        assert evidence.polluter_distance == 2.0
        assert evidence.polluter_lift > 1.0
        assert evidence.polluter_observations >= 4

    def test_it_reports_how_many_candidates_it_weighed(self) -> None:
        """The multiplicity correction's denominator, which a reader is entitled to."""
        outcomes = self.suite_with_gap(gap=3)
        victim = [o for o in outcomes if o.test_id == "victim"]
        evidence = detect_order_dependence(victim, build_ordering_index(outcomes, window=6))

        assert evidence is not None
        assert evidence.candidates_considered >= 1

    def test_a_test_appearing_twice_in_one_window_counts_once(self) -> None:
        """Otherwise a single run would inflate a candidate's association."""
        outcomes = []
        for index in range(12):
            run = f"r{index}"
            for position, (test_id, status) in enumerate(
                [
                    ("repeat", Status.PASSED),
                    ("repeat", Status.PASSED),
                    ("victim", Status.FAILED if index % 2 == 0 else Status.PASSED),
                ]
            ):
                outcomes.append(outcome(test_id, status, run=run, commit="c1", position=position))

        victim = [o for o in outcomes if o.test_id == "victim"]
        evidence = detect_order_dependence(victim, build_ordering_index(outcomes, window=4))
        if evidence is not None:
            assert evidence.polluter_observations <= 12
