"""Did this change introduce flakiness, or inherit it?

`triage` answers a question about one run: are these failures known flakes or new
breakage? This answers a question about a *branch*: is the flakiness here the change's
fault, or was it already in the suite?

That distinction is the whole point. A gate that blocks a merge because a test was
already flaky punishes whoever touched the file last, and a gate people resent is a
gate people route around. A gate that lets newly introduced flakiness through is how a
suite ends up at "always a bit red". So the two cases are separated, and only the
introduced ones block.

## Why a confidence bound rather than a rate comparison

The naive version compares failure rates: 0/40 on the baseline, 11/40 here, so the
change made it worse. That is wrong often enough to matter. A baseline of 0/40 does not
mean the true failure rate is zero; it means the rate is low. With a small enough
sample, "stable before, flaky now" happens by luck constantly, and a gate that fires on
luck is the false alarm this project exists not to raise.

So the baseline is given the benefit of the doubt twice over:

1. Take the **upper** confidence bound on its failure rate. Zero failures in 40 runs is
   consistent with a true rate up to about 7%, so 7% is what the change is measured
   against, not 0%.
2. Ask how likely this many failures would be *at that bound*. Only if that is
   improbable does the change get named.

A flake therefore has to clear a bar that the baseline's own uncertainty already sets,
which is the conservative direction: the cost of missing an introduced flake is a
slightly worse suite, and the cost of inventing one is a developer told their change
broke something it did not touch.

Same reasoning as the streak rule in `flakiness.py` and the polluter rule in
`ordering.py` -- beat chance, not just the observed number -- with an exact binomial
instead of a power, because here the baseline count is not always zero.

Pure, like the rest of `analysis/`: two analyses in, one comparison out.
"""

from __future__ import annotations

import math

from ..models import (
    MIN_BASELINE_RUNS,
    AnalysisReport,
    Change,
    ComparisonReport,
    TestAnalysis,
    TestComparison,
    Verdict,
)

ALPHA = 0.05
"""How improbable the new failures must be, under the baseline's upper bound.

The report-it-or-not gate. Deliberately looser than `STREAK_CHANCE_THRESHOLD` (0.01)
in `flakiness.py`, because that rule reclassifies a test on its own history where being
wrong is silent, and this one blocks a merge where being wrong is loud and gets argued
with. A looser gate here would produce those arguments; a much stricter one would let
real regressions through a PR check that people believe is watching.
"""

MIN_HEAD_RUNS = 5
"""New runs needed before a comparison is attempted at all.

Below this, `probability` is dominated by the sample size rather than the behaviour: two
failures in three runs is not evidence of anything, whatever the baseline looked like.
"""

MAX_EXACT_RUNS = 2000
"""Above this the exact binomial is skipped for a normal approximation.

Nobody hunts 2000 iterations, so this is a guard against a pathological input rather
than a routine path, and it exists because `math.comb` on huge n is slow enough to make
a CI step look hung.
"""


def compare(
    baseline: AnalysisReport,
    head: AnalysisReport,
    *,
    baseline_label: str | None = None,
    head_label: str | None = None,
) -> ComparisonReport:
    """Compare two analysed histories and report what the newer one introduced.

    Both arguments are full analyses, so every verdict here is the same verdict the CLI
    and the dashboard would show for those runs. This module decides only what *changed*;
    it does not re-score anything, which is what keeps it from drifting away from
    `analyze()`.
    """
    by_id = {test.test_id: test for test in baseline.tests}

    entries = [
        _compare_one(by_id.get(test.test_id), test)
        for test in head.tests
        if test.runs > 0 or test.retries > 0
    ]

    # Blocking changes first, then by how much evidence backs them, then test_id so the
    # order is stable when scores tie -- the same tiebreaker rule as `analyze`.
    entries.sort(key=lambda e: (not e.blocks, _severity(e.change), e.probability, e.test_id))

    return ComparisonReport(
        entries=tuple(entries),
        baseline_runs=baseline.total_runs,
        head_runs=head.total_runs,
        baseline_label=baseline_label,
        head_label=head_label,
        baseline_tests=len(baseline.tests),
        head_tests=len(head.tests),
    )


_ORDER = {
    Change.NEW_BREAK: 0,
    Change.NEW_FLAKE: 1,
    Change.WORSE: 2,
    Change.KNOWN_FLAKE: 3,
    Change.IMPROVED: 4,
    Change.UNPROVEN: 5,
    Change.UNCHANGED: 6,
}


def _severity(change: Change) -> int:
    return _ORDER[change]


def _compare_one(baseline: TestAnalysis | None, head: TestAnalysis) -> TestComparison:
    """Classify one test's change. The order of the checks is the policy."""
    failed_here = head.failures > 0 or head.retries > 0

    if not failed_here:
        # Clean now. The only interesting version of that is "and it used to be flaky".
        if baseline is not None and baseline.verdict is Verdict.FLAKY:
            return _entry(
                baseline,
                head,
                Change.IMPROVED,
                explanation=(
                    f"Was flaky on the baseline ({baseline.failures}/{baseline.runs} failed, "
                    f"score {baseline.score:.2f}) and passed {head.runs}/{head.runs} runs here. "
                    "Consistent with a fix; confirm with `flaky verify`."
                ),
            )
        return _entry(baseline, head, Change.UNCHANGED)

    if head.runs < MIN_HEAD_RUNS:
        return _entry(
            baseline,
            head,
            Change.UNPROVEN,
            explanation=(
                f"Failed here, but only {head.runs} "
                f"{'run' if head.runs == 1 else 'runs'} were recorded. "
                f"At least {MIN_HEAD_RUNS} are needed before this can be attributed to the "
                "change rather than to chance."
            ),
        )

    if baseline is None:
        return _new_test(head)

    if baseline.runs < MIN_BASELINE_RUNS:
        return _entry(
            baseline,
            head,
            Change.UNPROVEN,
            explanation=(
                f"Failed {head.failures}/{head.runs} here, but the baseline has only "
                f"{baseline.runs} recorded "
                f"{'run' if baseline.runs == 1 else 'runs'}, which is not enough to say the "
                "test was stable before. Record more history on the base branch."
            ),
        )

    bound = _upper_bound(baseline.failures, baseline.runs)
    probability = _tail_at_least(head.failures, head.runs, bound)
    significant = probability <= ALPHA
    was_flaky = baseline.verdict is Verdict.FLAKY

    if was_flaky:
        if significant and head.failure_rate > baseline.failure_rate:
            return _entry(
                baseline,
                head,
                Change.WORSE,
                bound=bound,
                probability=probability,
                explanation=(
                    f"Already flaky on the baseline at {baseline.failure_rate:.0%} "
                    f"({baseline.failures}/{baseline.runs}), and {head.failure_rate:.0%} here "
                    f"({head.failures}/{head.runs}). Reported, but not blocking: a "
                    "pre-existing flake is not this change's debt."
                ),
            )
        return _entry(
            baseline,
            head,
            Change.KNOWN_FLAKE,
            bound=bound,
            probability=probability,
            explanation=(
                f"Flaky on the baseline too ({baseline.failures}/{baseline.runs} failed, "
                f"score {baseline.score:.2f}). Pre-existing, so it does not block this change."
            ),
        )

    if head.failure_rate <= baseline.failure_rate:
        # No worse than before. "Nothing changed" and "we cannot tell" are different
        # answers, and reporting this as unproven would put a test that failed
        # identically on both sides into a list of things needing investigation.
        return _entry(
            baseline,
            head,
            Change.UNCHANGED,
            bound=bound,
            probability=probability,
            explanation=(
                f"{baseline.failures}/{baseline.runs} failed on the baseline and "
                f"{head.failures}/{head.runs} here. Not worse, so nothing was introduced."
            ),
        )

    if not significant:
        return _entry(
            baseline,
            head,
            Change.UNPROVEN,
            bound=bound,
            probability=probability,
            explanation=_unproven_explanation(baseline, head, bound, probability),
        )

    if head.verdict in (Verdict.REGRESSION, Verdict.BROKEN):
        return _entry(
            baseline,
            head,
            Change.NEW_BREAK,
            bound=bound,
            probability=probability,
            explanation=(
                f"Passed {baseline.passes}/{baseline.runs} on the baseline and fails "
                f"{head.failures}/{head.runs} here, consistently rather than intermittently. "
                "This is breakage, not flakiness: re-running will not help."
            ),
        )

    if head.verdict is Verdict.FLAKY:
        return _entry(
            baseline,
            head,
            Change.NEW_FLAKE,
            bound=bound,
            probability=probability,
            explanation=_new_flake_explanation(baseline, head, bound, probability),
        )

    return _entry(
        baseline,
        head,
        Change.UNPROVEN,
        bound=bound,
        probability=probability,
        explanation=(
            f"Failed {head.failures}/{head.runs} here, more than the baseline explains, but "
            f"scored {head.score:.2f}, below the flake threshold. Worth a look; not enough to "
            "name."
        ),
    )


def _new_test(head: TestAnalysis) -> TestComparison:
    """A test that does not exist on the baseline.

    Arriving flaky counts as introducing flakiness. There is no prior history to
    compare against, so the statistical test does not apply and the verdict carries the
    argument on its own -- which it can, because a verdict of FLAKY already required
    same-commit divergence or a repeated flip.
    """
    if head.verdict in (Verdict.REGRESSION, Verdict.BROKEN):
        return _entry(
            None,
            head,
            Change.NEW_BREAK,
            explanation=(
                f"New test, failing {head.failures}/{head.runs} runs consistently. It has "
                "never passed in recorded history."
            ),
        )
    if head.verdict is Verdict.FLAKY:
        return _entry(
            None,
            head,
            Change.NEW_FLAKE,
            probability=0.0,
            explanation=(
                f"New test, already flaky: {head.failures}/{head.runs} runs failed, score "
                f"{head.score:.2f}. It arrived with the flakiness, so there is no baseline to "
                "compare against and none needed."
            ),
        )
    return _entry(
        None,
        head,
        Change.UNPROVEN,
        explanation=(
            f"New test, failed {head.failures}/{head.runs} runs, scoring {head.score:.2f} -- "
            "below the flake threshold. Not enough to call."
        ),
    )


def _unproven_explanation(
    baseline: TestAnalysis, head: TestAnalysis, bound: float, probability: float
) -> str:
    """Say which kind of "cannot tell" this is, and what would settle it.

    Two very different situations land here and they deserve different sentences. One is
    noise: a test failed slightly more often, and that happens. The other is a test that
    is *demonstrably* flaky in the new runs -- it passed and failed at one commit -- where
    the only thing missing is enough baseline history to rule out that it was always like
    this. The second is worth someone's attention and the first is not, so the text has to
    tell them apart rather than emitting one hedge for both.
    """
    proven_here = head.divergent_commits > 0 or head.retries > 0
    remedy = (
        f"Hunt more iterations on the baseline to settle it: {baseline.runs} runs bound the "
        f"old failure rate only to {bound:.0%}, and this change shows {head.failure_rate:.0%}."
    )

    if proven_here:
        return (
            f"Flaky here by direct evidence -- it passed and failed at the same commit "
            f"({head.divergent_commits}/{head.observed_commits} commits), scoring "
            f"{head.score:.2f} -- and the baseline recorded "
            f"{baseline.failures}/{baseline.runs} failures. But the difference in rate is "
            f"within what chance explains (p={probability:.2f}), so it cannot be attributed "
            f"to this change. {remedy}"
        )

    return (
        f"Failed {head.failures}/{head.runs} here against "
        f"{baseline.failures}/{baseline.runs} on the baseline, which is within what chance "
        f"explains (p={probability:.2f}). No same-commit divergence was recorded either, so "
        f"there is nothing to attribute. {remedy}"
    )


def _new_flake_explanation(
    baseline: TestAnalysis, head: TestAnalysis, bound: float, probability: float
) -> str:
    lines = [
        f"Stable on the baseline ({baseline.passes}/{baseline.runs} passed) and flaky here "
        f"({head.failures}/{head.runs} failed, score {head.score:.2f}).",
    ]

    if head.divergent_commits:
        lines.append(
            f"Proven, not inferred: it passed and failed at the same commit in "
            f"{head.divergent_commits} of {head.observed_commits} commits, so the code was "
            "identical between a pass and a fail."
        )
    elif head.retries:
        lines.append(
            f"The runner recorded {head.retries} "
            f"{'retry' if head.retries == 1 else 'retries'}: it watched this test fail and "
            "then pass inside one run."
        )
    else:
        lines.append(
            "No same-commit divergence was recorded, so this rests on flip rate, which is "
            "the weaker signal."
        )

    lines.append(
        f"The baseline's runs are consistent with a failure rate up to {bound:.0%}; even at "
        f"that rate, this many failures here has probability {probability:.3f}."
    )

    if head.order and head.order.likely_polluter:
        lines.append(f"Fails after {head.order.likely_polluter}.")

    return " ".join(lines)


def _entry(
    baseline: TestAnalysis | None,
    head: TestAnalysis,
    change: Change,
    *,
    bound: float = 0.0,
    probability: float = 1.0,
    explanation: str = "",
) -> TestComparison:
    return TestComparison(
        test_id=head.test_id,
        name=head.name,
        change=change,
        baseline=baseline,
        head=head,
        baseline_rate_bound=round(bound, 4),
        probability=round(probability, 6),
        explanation=explanation,
    )


def _upper_bound(failures: int, runs: int, alpha: float = ALPHA) -> float:
    """Highest failure rate consistent with `failures` in `runs`, at 1 - alpha.

    The Clopper-Pearson upper limit, found by bisection rather than pulled from a
    library, because adding scipy to read test reports is not a trade this project makes.

    For zero failures it reduces to a closed form: the largest rate under which a clean
    run of `runs` still has probability alpha. That is the familiar rule of three -- no
    failures in 40 runs means the true rate could still be about 7%.
    """
    if runs <= 0:
        return 1.0
    if failures >= runs:
        return 1.0
    if failures == 0:
        return 1.0 - alpha ** (1.0 / runs)

    low, high = failures / runs, 1.0
    # 60 halvings takes the interval below float resolution; a fixed count keeps this
    # deterministic, which analysis output has to be.
    for _ in range(60):
        middle = (low + high) / 2.0
        if _cdf_at_most(failures, runs, middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def _cdf_at_most(successes: int, trials: int, rate: float) -> float:
    """P(X <= successes) for X ~ Binomial(trials, rate)."""
    if rate <= 0.0:
        return 1.0
    if rate >= 1.0:
        return 1.0 if successes >= trials else 0.0

    total = 0.0
    for count in range(min(successes, trials) + 1):
        total += math.comb(trials, count) * rate**count * (1.0 - rate) ** (trials - count)
    return min(1.0, total)


def _tail_at_least(successes: int, trials: int, rate: float) -> float:
    """P(X >= successes) for X ~ Binomial(trials, rate)."""
    if successes <= 0:
        return 1.0
    if trials <= 0:
        return 1.0
    if successes > trials:
        return 0.0
    if trials > MAX_EXACT_RUNS:
        return _normal_tail(successes, trials, rate)
    return max(0.0, 1.0 - _cdf_at_most(successes - 1, trials, rate))


def _normal_tail(successes: int, trials: int, rate: float) -> float:
    """Normal approximation, used only for implausibly large run counts."""
    mean = trials * rate
    spread = math.sqrt(trials * rate * (1.0 - rate))
    if spread <= 0.0:
        return 1.0 if successes <= mean else 0.0
    # Continuity correction, then the complementary error function.
    z = (successes - 0.5 - mean) / spread
    return 0.5 * math.erfc(z / math.sqrt(2.0))
