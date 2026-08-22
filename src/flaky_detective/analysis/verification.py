"""Did the fix work?

Detection and diagnosis are the first half of the job. The second half is the one that
actually removes flakiness from a suite, and it is the half most tools skip: after
someone changes the code, deciding whether the test is *actually* fixed or merely
quiet.

Getting this wrong is expensive in a specific way. Declaring a flake fixed after three
green runs puts it back in the trusted set, so the next time it fails, the failure looks
like new breakage. The tool has then manufactured exactly the confusion it exists to
remove.

## Three things have to hold, not one

**The streak has to beat the old rate.** A test that failed 35% of the time needs 8
clean runs to clear a 5% bar; one that failed 2% of the time needs 149. People declare
the second fixed after three green runs, and they are wrong to. `statistics.trials_needed`
turns that into a number the tool can state up front, so "run it more" comes with a
count.

The old rate is taken at its **lower** confidence bound, which is the conservative
direction for claiming an improvement: a lower assumed old rate makes a clean streak
less surprising and so raises the bar. Using the observed rate would let a handful of
green runs certify a fix.

**The failing conditions have to have been exercised.** This is the check that makes the
rest worth anything. If a test only failed when it ran after `test_registers_session`,
and the polluter happened to precede it twice in fifty runs, then fifty green runs prove
almost nothing -- the situation that used to fail was barely attempted. Reporting that as
a fix would be the worst kind of false negative, because it is delivered with a
confident number attached.

**Nothing else may have broken.** A fix that makes one test stable by leaking state into
another has moved the problem. The whole-suite comparison is passed in and any newly
introduced flakiness counts against the verification, not as a footnote.

Pure, like the rest of `analysis/`. The caller re-runs the suite, splits the history, and
counts polluter exposures; this weighs what came back.
"""

from __future__ import annotations

from ..models import (
    ComparisonReport,
    FixOutcome,
    FixVerification,
    TestAnalysis,
    Verdict,
)
from .statistics import lower_bound, tail_at_most, trials_needed

ALPHA = 0.05
"""How improbable the clean streak must be under the old failure rate.

The same level `comparison.py` uses, deliberately. A fix verified at one standard and a
regression flagged at another would make the pair inconsistent: a change could be
reported as introducing flakiness and then as fixing it on the same evidence.
"""

MIN_EXPOSURES = 5
"""Times the old polluter must have preceded the test before a clean run counts.

Below this the streak is not evidence about the fix, because the sequence that used to
fail was barely attempted. Five is the same floor `ordering.py` needs before a polluter
correlation means anything, kept identical so the tool cannot detect a polluter on
evidence it then refuses to verify against.
"""

MIN_AFTER_RUNS = 5
"""New runs below which no claim is made in either direction."""


def verify_fix(
    before: TestAnalysis,
    after: TestAnalysis,
    *,
    polluter_exposures: int | None = None,
    collateral: ComparisonReport | None = None,
    alpha: float = ALPHA,
) -> FixVerification:
    """Weigh a candidate fix against the history that preceded it.

    `polluter_exposures` is how many times the polluter named in `before` ran ahead of
    this test during the new runs. Passed in because counting it needs the raw outcomes
    and this module does not take them. `None` means the question does not apply -- the
    test was never diagnosed as order dependent.

    `collateral` is a comparison of the whole suite across the same split, used to catch
    a fix that moved the problem instead of removing it.
    """
    old_rate = lower_bound(before.failures, before.runs, alpha)
    required = trials_needed(old_rate, alpha)
    probability = tail_at_most(after.failures, after.runs, old_rate)
    still_failing = after.failures > 0 or after.retries > 0

    collateral_damage = tuple(
        entry.test_id
        for entry in (collateral.blocking if collateral else ())
        if entry.test_id != before.test_id
    )

    outcome, explanation = _decide(
        before=before,
        after=after,
        old_rate=old_rate,
        required=required,
        probability=probability,
        still_failing=still_failing,
        polluter_exposures=polluter_exposures,
        collateral_damage=collateral_damage,
        alpha=alpha,
    )

    return FixVerification(
        test_id=before.test_id,
        outcome=outcome,
        before=before,
        after=after,
        old_rate_bound=round(old_rate, 4),
        probability=round(probability, 6),
        runs_needed=required,
        polluter=before.order.likely_polluter if before.order else None,
        polluter_exposures=polluter_exposures,
        exposures_needed=MIN_EXPOSURES,
        collateral=collateral_damage,
        explanation=explanation,
    )


def _decide(
    *,
    before: TestAnalysis,
    after: TestAnalysis,
    old_rate: float,
    required: int,
    probability: float,
    still_failing: bool,
    polluter_exposures: int | None,
    collateral_damage: tuple[str, ...],
    alpha: float,
) -> tuple[FixOutcome, str]:
    """The order of these checks is the policy, and it is deliberately unflattering.

    Every way of *not* being able to claim a fix is tested before the claim is allowed.
    """
    if before.failures == 0 and before.retries == 0:
        return (
            FixOutcome.INCONCLUSIVE,
            f"There is nothing to verify: the recorded history before this shows "
            f"{before.passes}/{before.runs} passing and no failures. Point --since or "
            "--after-commit at a window that contains the failures you fixed.",
        )

    if after.runs < MIN_AFTER_RUNS:
        return (
            FixOutcome.INCONCLUSIVE,
            f"Only {after.runs} {'run' if after.runs == 1 else 'runs'} recorded after the "
            f"change. At least {MIN_AFTER_RUNS} are needed before a streak means anything, "
            f"and at this test's old failure rate you need {required}.",
        )

    if still_failing:
        return (
            FixOutcome.NOT_FIXED,
            f"Still failing: {after.failures}/{after.runs} runs after the change, against "
            f"{before.failures}/{before.runs} before. "
            + (
                "The rate is lower, which may mean the fix helped without being complete."
                if after.failure_rate < before.failure_rate
                else "No improvement in failure rate."
            ),
        )

    # From here the new runs are clean. Everything below is a reason that is not enough.
    if collateral_damage:
        listed = ", ".join(collateral_damage[:3])
        more = f" and {len(collateral_damage) - 3} more" if len(collateral_damage) > 3 else ""
        return (
            FixOutcome.INCONCLUSIVE,
            f"{after.runs}/{after.runs} clean for this test, but the same change introduced "
            f"flakiness or breakage elsewhere: {listed}{more}. A fix that moves the problem "
            "has not removed it.",
        )

    if polluter_exposures is not None and polluter_exposures < MIN_EXPOSURES:
        polluter = before.order.likely_polluter if before.order else "the polluter"
        return (
            FixOutcome.INCONCLUSIVE,
            f"{after.runs}/{after.runs} clean, but this test only ever failed when it ran "
            f"after {polluter}, and that happened just {polluter_exposures} "
            f"{'time' if polluter_exposures == 1 else 'times'} in the new runs. The "
            f"situation that used to fail was barely attempted, so the clean streak is not "
            f"evidence about the fix. Re-run with shuffling until it has been exercised at "
            f"least {MIN_EXPOSURES} times.",
        )

    if after.runs < required:
        return (
            FixOutcome.INCONCLUSIVE,
            f"{after.runs}/{after.runs} clean, which is encouraging and not yet enough. At "
            f"the old failure rate of {old_rate:.0%} a clean streak this short happens "
            f"{_as_percent(probability)} of the time by chance; {required} clean runs are "
            f"needed to rule that out.",
        )

    return FixOutcome.FIXED, _fixed_explanation(
        before=before,
        after=after,
        old_rate=old_rate,
        probability=probability,
        polluter_exposures=polluter_exposures,
        alpha=alpha,
    )


def _fixed_explanation(
    *,
    before: TestAnalysis,
    after: TestAnalysis,
    old_rate: float,
    probability: float,
    polluter_exposures: int | None,
    alpha: float,
) -> str:
    parts = [
        f"Was failing {before.failures}/{before.runs} runs ({before.failure_rate:.0%}, score "
        f"{before.score:.2f}); now {after.runs}/{after.runs} clean.",
        f"At its old rate a streak that clean happens {_as_percent(probability)} of the "
        f"time by chance, below the {alpha:.0%} bar.",
    ]

    if before.verdict is Verdict.FLAKY and before.divergent_commits:
        parts.append(
            f"The old flakiness was proven, not guessed: it passed and failed at the same "
            f"commit in {before.divergent_commits} of {before.observed_commits} commits."
        )

    if polluter_exposures is not None and before.order and before.order.likely_polluter:
        parts.append(
            f"The failing sequence was exercised: it ran after "
            f"{before.order.likely_polluter} {polluter_exposures} times and passed every "
            "time."
        )

    return " ".join(parts)


def _as_percent(probability: float) -> str:
    """Format a probability without rounding a real number down to nothing.

    `f"{8.3e-05:.1%}"` is "0.0%", which reads as a rounding artefact rather than as the
    strongest evidence the tool can offer. Small values become an explicit bound instead.
    """
    if probability <= 0.0:
        return "under 0.01%"
    if probability < 0.0001:
        return "under 0.01%"
    if probability < 0.001:
        return "under 0.1%"
    return f"{probability:.1%}"


def count_exposures(
    test_id: str,
    polluter: str,
    outcomes: list,
    ordering: dict[tuple[str, str], tuple[tuple[str, int], ...]],
) -> int:
    """How many of these runs ran `polluter` ahead of `test_id`, inside the window.

    Lives here rather than in `ordering.py` because it answers a verification question,
    not a detection one, and takes the index the caller already built for the analysis.

    Deliberately the same notion of "ahead of" that detection uses. If detection can name a
    polluter four tests back while verification only counts adjacency, every such fix
    becomes permanently unverifiable for a reason that is an artefact of the mismatch.
    """
    return sum(
        1
        for outcome in outcomes
        if outcome.test_id == test_id
        and any(
            candidate == polluter
            for candidate, _ in ordering.get((outcome.run_uid or "", test_id), ())
        )
    )


__all__ = ["ALPHA", "MIN_AFTER_RUNS", "MIN_EXPOSURES", "count_exposures", "verify_fix"]
