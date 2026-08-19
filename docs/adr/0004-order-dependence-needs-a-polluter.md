# 0004 — Order dependence requires naming a polluter

**Status:** Accepted, after two superseded attempts

This record keeps all three versions, because the two failures are more instructive than
the final rule.

## Context

An order-dependent test fails because another test left state behind. It is worth
detecting separately from other flakes for one practical reason: the usual reflex, adding
a retry, does not fix it. The test is not racing anything; it is reading state that
already exists, and retrying inside the same process fails again.

The original requirement (FR3.4) specified correlating outcome with the test's position
in the run. A test that fails when it runs late is presumably being polluted by
something earlier.

## Attempt 1 — position separation over pooled spread

```
separation = |mean(pos | fail) − mean(pos | pass)| / stdev(all positions)
```

Flag above 1.0, with at least 3 observations per side.

**Failed.** Dividing by pooled spread ignores sample size, so a wide gap from five noisy
points outranked a narrow gap from fifty consistent ones. Run against the demo suite it
labelled a purely random test order-dependent at 1.1σ and named an innocent predecessor.

## Attempt 2 — a sample-size-aware t-statistic

Divide by the standard error of the difference of means instead, gate at t ≥ 2.5, and
make predecessor correlation an independent trigger on the reasoning that a polluter can
sit anywhere earlier and so may produce little position effect.

```
se = sqrt(var(pos | pass)/n_pass + var(pos | fail)/n_fail)
t  = |mean(pos | fail) − mean(pos | pass)| / se
```

**Failed differently.** This flagged **eight of ten** demo tests, each with a reported
polluter confidence of 100%.

The reason is worth internalising: in a shuffled ten-test suite, a given predecessor
precedes the victim only three or four times. A test that already fails 70% of the time
will fail all four of those by chance about a quarter of the time. "Fails 90% of the time
after X" is not evidence when the test fails 90% of the time after everything.

## The measurement that settled it

Forty shuffled iterations of the demo suite, position statistics per test, against known
behaviour:

| Test | t | separation | What it actually is |
|---|---:|---:|---|
| `test_append_order_is_stable` | 3.47 | 1.06 | thread race |
| `test_worker_finishes_within_deadline` | 3.47 | 0.89 | timing |
| `test_expects_clean_registry` | 2.33 | 0.71 | **order dependent** |
| `test_counts_registered_sessions` | 2.27 | 0.70 | **order dependent** |
| `test_token_still_valid_at_check_time` | 1.25 | 0.40 | timing |
| others | < 1.1 | < 0.4 | random / network / race |

The two strongest position signals are both *timing* flakes. The genuinely
order-dependent tests rank below them.

The explanation is mundane once seen: position correlates with how late in the run a test
executes, which correlates with machine state — warmer caches, more threads created, more
garbage to collect. That is a real cause of flakiness, but it is not shared-state
pollution, and labelling it `order_dependence` sends someone hunting a leaked fixture
that does not exist.

**Position is not a valid trigger for this diagnosis.** The proxy the requirement
specified measures the wrong thing.

## Decision

Order dependence requires **naming a polluter**. The immediately preceding test must:

- have run before this test at least 4 times,
- precede a failure at least 90% of those times, and
- **beat chance**: `baseline_failure_rate ^ failures_after_it ≤ 0.05`.

Plus a guard that a test failing more than 75% of the time overall cannot blame anyone.

Position separation is still computed and reported as supporting detail, because it is
the interpretable number to show a human, but it cannot trigger the verdict. It is
suppressed from output below 0.5σ, where printing it would only invite doubt about a
sound conclusion.

## Consequences

**Precision over recall, deliberately.** Telling someone a test is order-dependent sends
them looking for leaked state; a false positive costs an afternoon. Measured on ground
truth: precision and recall both 100%.

**Only the immediate predecessor is checked.** A polluter running several tests earlier
is missed. Checking every earlier test for every candidate is quadratic in suite size,
and the cheap version catches the common case because suites usually shuffle within a
file or class. Documented as a limitation rather than hidden.

**Order randomization becomes a requirement, not a nicety.** Without a shuffling runner
there is no variation to correlate, so `flaky hunt` probes for the capability and says
so loudly when it is unavailable instead of running N identical iterations.

## Postscript

The benchmark later reported polluter precision of 0.000 for this rule. That turned out
to be a bug in the *benchmark* — overlapping positions in generated data made
"immediately before" arbitrary — not in the detector. Recorded in
[0007](0007-measure-our-own-accuracy.md), because measuring your own measuring
instrument is part of the job.
