"""Binomial reasoning, in one place.

Three questions in this tool are the same question with different signs:

- `comparison.py`: this branch failed more than the baseline. Did it cause that?
- `verification.py`: this test stopped failing. Is it fixed, or lucky?
- `flakiness.py` and `ordering.py`: is this streak, or this correlation, more than the
  test's own behaviour already explains?

All four are "beat chance". They lived as three separate ad-hoc calculations, which is
exactly the drift the structure steering warns about for score weights: a threshold in
two places is a threshold that will disagree with itself. The exact-binomial machinery
now lives here and the callers supply the sign.

Standard library only. `math.comb` and a bisection are enough for run counts a human
would ever record, and adding scipy to read test reports is not a trade this project
makes.

Everything here is deterministic: same inputs, same output, no `random`, no iteration
over a set. Analysis output has to be reproducible.
"""

from __future__ import annotations

import math

DEFAULT_ALPHA = 0.05
"""Default significance level for the confidence bounds.

Not a house threshold for verdicts -- callers set their own, and they differ on purpose
(`comparison.ALPHA` is 0.05, `flakiness.STREAK_CHANCE_THRESHOLD` is 0.01). This is only
the default used when a bound is requested without one.
"""

MAX_EXACT_TRIALS = 20000
"""Above this, tails use a normal approximation instead of an exact sum.

A speed guard, not a correctness one: the summation is done in log space so it stays
accurate at any size. It exists because the exact sum is linear in the number of
successes, and a tool that appears to hang gets killed rather than debugged.
"""

_BISECTION_STEPS = 60
"""Halvings used to invert the binomial CDF.

Fixed rather than convergence-based, so the result is bit-identical run to run. Sixty
halvings takes the interval well below float resolution.
"""


def cdf_at_most(successes: int, trials: int, rate: float) -> float:
    """P(X <= successes) for X ~ Binomial(trials, rate)."""
    if trials <= 0:
        return 1.0
    if rate <= 0.0:
        return 1.0
    if rate >= 1.0:
        return 1.0 if successes >= trials else 0.0
    if successes >= trials:
        return 1.0
    if successes < 0:
        return 0.0

    total = 0.0
    for count in range(successes + 1):
        total += math.exp(_log_pmf(count, trials, rate))
    return min(1.0, total)


def _log_pmf(successes: int, trials: int, rate: float) -> float:
    """Log of the binomial probability mass, computed via lgamma.

    Not `math.comb`. The direct form overflows: `comb(2000, 1000)` is a 600-digit integer
    and multiplying it by a float raises OverflowError, which took a test at 2000 trials
    to surface. Working in logs is stable at any size the caller can reach, and it removed
    the need to treat large inputs as a special case for correctness rather than speed.
    """
    return (
        math.lgamma(trials + 1)
        - math.lgamma(successes + 1)
        - math.lgamma(trials - successes + 1)
        + successes * math.log(rate)
        + (trials - successes) * math.log1p(-rate)
    )


def tail_at_least(successes: int, trials: int, rate: float) -> float:
    """P(X >= successes) for X ~ Binomial(trials, rate).

    Used when the question is "did we see *more* than expected": more failures on a
    branch than the baseline explains.
    """
    if successes <= 0:
        return 1.0
    if trials <= 0:
        return 1.0
    if successes > trials:
        return 0.0
    if trials > MAX_EXACT_TRIALS:
        return _normal_tail_upper(successes, trials, rate)
    return max(0.0, 1.0 - cdf_at_most(successes - 1, trials, rate))


def tail_at_most(successes: int, trials: int, rate: float) -> float:
    """P(X <= successes) for X ~ Binomial(trials, rate).

    The mirror of `tail_at_least`, for when the question is "did we see *fewer* than
    expected": a test that has stopped failing. Named separately from `cdf_at_most`
    because at call sites the two read very differently -- one is a probability being
    inverted, the other is evidence being weighed.
    """
    if trials <= 0:
        return 1.0
    if trials > MAX_EXACT_TRIALS:
        return 1.0 - _normal_tail_upper(successes + 1, trials, rate)
    return cdf_at_most(successes, trials, rate)


def upper_bound(successes: int, trials: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Highest rate consistent with the observation, at 1 - alpha confidence.

    The Clopper-Pearson upper limit. Use it when a clean observation must not be treated
    as proof of a zero rate: **no failures in 40 runs still admits a true rate near 7%**,
    which is the fact that stops a branch comparison from firing on luck.

    For zero successes this reduces to a closed form and reproduces the familiar rule of
    three.
    """
    if trials <= 0:
        return 1.0
    if successes >= trials:
        return 1.0
    if successes <= 0:
        return 1.0 - alpha ** (1.0 / trials)

    low, high = successes / trials, 1.0
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) / 2.0
        if cdf_at_most(successes, trials, middle) > alpha:
            low = middle
        else:
            high = middle
    return high


def lower_bound(successes: int, trials: int, alpha: float = DEFAULT_ALPHA) -> float:
    """Lowest rate consistent with the observation, at 1 - alpha confidence.

    The Clopper-Pearson lower limit, and the conservative choice when *claiming an
    improvement*. To argue a test is fixed, the old failure rate has to be assumed as low
    as its data allows: a lower old rate makes a clean streak less surprising, so it
    raises the bar for declaring the fix real.

    Using the observed rate instead would let 14 failures in 40 runs certify a fix after
    a handful of clean runs, which is how a "fixed" label ends up on a test that was
    merely having a good afternoon.
    """
    if trials <= 0:
        return 0.0
    if successes <= 0:
        return 0.0
    if successes >= trials:
        return alpha ** (1.0 / trials)

    low, high = 0.0, successes / trials
    for _ in range(_BISECTION_STEPS):
        middle = (low + high) / 2.0
        if tail_at_least(successes, trials, middle) > alpha:
            high = middle
        else:
            low = middle
    return low


def trials_needed(rate: float, alpha: float = DEFAULT_ALPHA) -> int:
    """Clean runs required before a clean streak beats `rate` at 1 - alpha.

    Answers the question a person actually has after fixing a flaky test: *how many times
    do I have to run this before I can believe it?* Solves
    `(1 - rate) ** n <= alpha` for n.

    A test that used to fail 35% of the time needs 8 clean runs. One that failed 2% of
    the time needs 149. That asymmetry is worth surfacing, because the rare flake is the
    one people declare fixed on three green runs.
    """
    if rate <= 0.0:
        return 0
    if rate >= 1.0:
        return 1
    return max(1, math.ceil(math.log(alpha) / math.log(1.0 - rate)))


def _normal_tail_upper(successes: int, trials: int, rate: float) -> float:
    """P(X >= successes), normal approximation with a continuity correction."""
    mean = trials * rate
    spread = math.sqrt(trials * rate * (1.0 - rate))
    if spread <= 0.0:
        return 1.0 if successes <= mean else 0.0
    z = (successes - 0.5 - mean) / spread
    return 0.5 * math.erfc(z / math.sqrt(2.0))


__all__ = [
    "DEFAULT_ALPHA",
    "MAX_EXACT_TRIALS",
    "cdf_at_most",
    "lower_bound",
    "tail_at_least",
    "tail_at_most",
    "trials_needed",
    "upper_bound",
]
