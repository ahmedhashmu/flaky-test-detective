# 0006 — A failure streak must beat the test's own baseline

**Status:** Accepted

## Context

Distinguishing `regression` from `flaky` decides whether CI exits 2 or 1, so it drives
behaviour rather than just wording. The rule went through three versions, and the last
two changes were each driven by a measurement rather than an opinion.

### Version 1

Three consecutive trailing failures means regression.

**Broke immediately.** A genuinely flaky test that happened to fail its last three
iterations was reported as a regression, which would send someone hunting a bad commit
that does not exist.

### Version 2

Add a condition: with commit SHAs, check whether the newest commit still shows a pass
(direct proof of current flakiness). Without them, require `flips ≤ 2`, since a
regression flips once and a flake flips repeatedly.

Better, and it survived the demo suite. Then the accuracy benchmark measured it against
107 tests with known labels:

**7 of 37 known flakes were reported as regressions.** One of them had 18 flips and
divergence at 10 of 15 commits — overwhelming proof of flakiness, discarded because the
last three runs happened to fail.

## The insight

The question being asked was wrong. "Is it failing now" is not the same as "is it failing
*more than its own history explains*".

A test that fails 70% of the time produces a three-run streak roughly a third of the
time. That streak is evidence of nothing. A test that has never failed and then fails
three times running has done something new.

## Decision

Add a third condition to `regression`: the streak must be improbable under the test's own
baseline failure rate.

```
baseline_rate = failure rate over history *before* the streak began
regression requires  baseline_rate ^ streak_length ≤ 0.01
```

The baseline is measured before the streak deliberately. Including it would inflate the
rate being compared against, making a genuine regression progressively harder to detect
the longer it went unfixed — exactly backwards.

Worked through:

| Case | Streak | Baseline | Probability | Verdict |
|---|---:|---:|---:|---|
| Clean regression | 9 | 0.00 | 0 | `regression` ✓ |
| Regression with flaky history | 9 | 0.20 | 5×10⁻⁷ | `regression` ✓ |
| Flake at *p* = 0.7 | 3 | 0.70 | 0.34 | `flaky` ✓ |
| Flake at *p* = 0.9 | 19 | 0.90 | 0.14 | `flaky` ✓ |

This is the same "beat chance" reasoning as the polluter test in
[0004](0004-order-dependence-needs-a-polluter.md), applied to a different signal. Two
independent problems, one idea.

## Measured effect

| Metric | Before | After |
|---|---:|---:|
| Missed break rate | 18.9% (7/37) | **5.4% (2/37)** |
| False alarm rate | 0.0% | **0.0%** |
| Flaky recall | 0.703 | **0.811** |
| Regression precision | 0.533 | **0.800** |
| Accuracy | 89.7% | **93.5%** |

The false alarm rate staying at zero is the important part. The fix bought recall without
trading away the property the tool exists to guarantee.

## Follow-up: the streak requirement must scale with history

Sweeping over run count exposed a second problem in the same rule. At **5 runs** the
false alarm rate was **50%**.

The cause was a hard floor of three trailing failures. In a five-run window, a textbook
regression pattern of `...FF` has only two, failed the check, and fell through to
`flaky`.

```
required_streak = max(2, min(3, runs // 3))
```

Never below two: one failure is not a streak, and the probability test needs at least two
observations to mean anything. The chance condition still has to agree, so a short streak
from a genuinely flaky test is not promoted on length alone.

| Runs | False alarm before | After |
|---|---:|---:|
| 5 | 50.0% | **12.5%** |
| 10 | 0.0% | 0.0% |
| 30 | 0.0% | 0.0% |

The residual 12.5% at five runs is an information limit rather than a bug — `.F.FF` is
genuinely ambiguous with that little data — so the documented advice is to use at least
10 runs, and that number now comes from a measurement.

## Consequences

**Two thresholds to justify.** `STREAK_CHANCE_THRESHOLD = 0.01` and the scaling rule are
both arbitrary in the same way any significance level is. They are defended by the tables
above rather than by argument, and `flaky benchmark` re-derives them on demand.

**A high-rate flake with a long unlucky streak is still misreported.** 2 of 37. Reducing
that further would require trading the false alarm rate, which is the wrong direction:
better to send someone to investigate a flake than to teach them to re-run a real break.
