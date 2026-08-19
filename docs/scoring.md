# Scoring

Every number this tool reports is reproducible from counts it also shows you. That is
deliberate: a score you cannot check is a score you have to take on faith, and the
whole argument of this tool is that you should not have to.

## The one idea

**Same code, different outcome** is the only direct proof of flakiness available
without reading source.

If a test passed and failed at the same commit SHA, the code was byte-identical
between those two runs. Whatever varied, it was not the code. That is evidence.
Everything else in this document is inference built on top of it.

## The signals

### 1. Same-commit divergence — proof

```
divergent_commits  = commits where the test both passed and failed
observed_commits   = commits where the test ran more than once
divergence_rate    = divergent_commits ÷ observed_commits
```

The denominator counts only commits where the test ran **more than once**. A commit
with a single run cannot show divergence, so including it would dilute the rate with
non-evidence — the tool would look less confident precisely because it had less data,
rather than because the data was negative.

### 2. Runner-recorded retries — also proof

Maven Surefire writes `<flakyFailure>` when a test failed and then passed on retry.
`pytest-rerunfailures` writes `<rerunFailure>`. Either way the runner watched one test
produce two outcomes inside a single run, and reported it.

That is not inference. A retry at a commit makes that commit count as divergent even
if only one row was written for it.

### 3. Flip rate — inference

```
flip_rate = pass↔fail transitions ÷ (runs − 1)
```

Suggestive but weak, because a single pass→fail transition is far more likely to be a
regression than a flake. Weighted accordingly.

## Combining them

```
raw        = 0.7 · max(divergence_rate, retry_rate) + 0.3 · flip_rate
confidence = min(1, runs ÷ 10)
score      = raw · (0.5 + 0.5 · confidence)
```

### Why 0.7 and 0.3

Divergence is proof and flips are a hint. The exact split is a judgement, but the
ordering is not: any weighting that let flip rate dominate would let a regression
outrank a proven flake, which is the failure mode this tool exists to avoid.

### Why confidence never reaches zero

```
confidence_factor = 0.5 + 0.5 · min(1, runs ÷ 10)
```

The factor floors at 0.5 rather than 0. A test seen three times with strong evidence
still surfaces — it just cannot outrank the same evidence backed by two hundred runs.
A floor of zero would make every newly-discovered flake invisible, which is the
opposite of useful.

### Without commit SHAs

Falls back to flip rate alone, but capped:

```
raw = 0.85 · flip_rate
```

The cap exists because a test caught the original version scoring a perfectly
alternating history at **1.00** with no commit data — identical to a score backed by
proof. Both are probably flaky, but only one has evidence that the code was not the
variable. Without the cap the score stops carrying information about how much the
verdict can be trusted, which is the reason for having a score rather than a boolean.

## Verdicts

Exactly one applies. Check order matters: anything explainable as a real break is
reported as one, and `flaky` is checked last.

| Verdict | Condition | Meaning |
|---|---|---|
| `broken` | never passed | Usually an incomplete commit, never a flake |
| `regression` | trailing failures ≥ 3, no divergence at the newest commit, **and** the streak beats its own baseline | Consistent failure that used to pass |
| `fixed` | consecutive passes ≥ 10 | Was flaky, now stable |
| `flaky` | score ≥ threshold | Different outcomes for the same code |
| `stable` | everything else | |

### The streak-beats-chance condition

The third condition on `regression` is the one that needed measurement to discover.

A trailing run of failures plus no divergence at the newest commit sounds sufficient.
It is not. A test that fails 70% of the time produces a three-run streak roughly a
third of the time, so that streak is evidence of nothing at all.

```
baseline_rate = failure rate over history *before* the streak
regression requires  baseline_rate ^ streak_length ≤ 0.01
```

Worked through:

| Test | Streak | Baseline rate | Probability | Verdict |
|---|---:|---:|---:|---|
| Clean regression | 9 | 0.00 | 0 | `regression` ✓ |
| Regression with flaky history | 9 | 0.20 | 5×10⁻⁷ | `regression` ✓ |
| Flake at p = 0.7 | 3 | 0.70 | 0.34 | `flaky` ✓ |
| Flake at p = 0.9 | 19 | 0.90 | 0.14 | `flaky` ✓ |

The baseline is measured *before* the streak on purpose. Including the streak would
inflate the rate being compared against, making a genuine regression progressively
harder to detect the longer it went unfixed — precisely backwards.

Measured effect: known flakes misreported as regressions dropped from 7 of 37 to 2 of
37, with the false-alarm rate staying at zero. See [accuracy](accuracy.md).

## Order dependence

Not scored — diagnosed. And it requires **naming a polluter**, not just observing a
correlation with position.

That restriction came from measurement. Over 40 shuffled iterations of the demo suite,
the two strongest position signals were both *timing* flakes (t = 3.47), while the
genuinely order-dependent tests scored only t ≈ 2.3. Position tracks how late a test
runs, which tracks machine state: warmer caches, more threads, more garbage. Real, but
not shared-state pollution — and reporting it as such sends someone hunting a leaked
fixture that does not exist.

A predecessor is named only when it:

- ran immediately before the test at least 4 times,
- preceded a failure at least 90% of those times, and
- beats chance: `baseline_failure_rate ^ failures_after_it ≤ 0.05`.

Plus a guard that a test failing more than 75% of the time overall cannot blame anyone,
because it fails after everything.

The chance condition is the one that matters. Requiring only "fails 90% of the time
after X" flagged eight of ten demo tests with a reported confidence of 100%, because in
a shuffled ten-test suite a given predecessor precedes the victim only three or four
times, and a test that already fails 70% of the time will fail all four by chance about
a quarter of the time.

Same reasoning as the regression streak, applied to a different signal. Measured
result: precision and recall both 100% on ground truth.

## Root cause

Heuristic pattern matching over the **raw** failure message, and it deliberately never
influences the score. A guess about *why* a test is flaky must not change *whether* it
is called flaky.

Raw rather than normalized, because normalization replaces integers of three or more
digits with `<NUM>` — so `HTTP 503` becomes `HTTP <NUM>` and the network rule stops
firing. Normalization is for clustering only.

Categories: `timeout`, `race`, `order_dependence`, `network`, `resource`,
`time_dependence`, `randomness`, `assertion`. The matched terms are always shown, so a
wrong guess is visible rather than authoritative.

## Failure-message normalization

Used for clustering, not classification. Ordered substitutions, most specific first,
because several of these rules would otherwise eat each other's input:

| Pattern | Becomes |
|---|---|
| UUIDs | `<UUID>` |
| ISO timestamps, bare times | `<TIMESTAMP>`, `<TIME>` |
| URLs | `<URL>` |
| IP addresses | `<IP>` |
| Hex addresses | `<ADDR>` |
| Temp paths, then general paths | `<TMP>`, `<PATH>` |
| Source locations (`file.go:41`) | `file.go:<N>` |
| `host:port` | `host:<PORT>` |
| Durations | `<DURATION>` |
| Number and string lists | `[<NUMS>]`, `[<LIST>]` |
| Integers ≥ 3 digits | `<NUM>` |

Order is load-bearing and has explicit tests, because getting it wrong raises nothing —
it silently splits one bug into forty clusters. Two examples that were once wrong:
`127.0.0.1` became `<NUM>.0.0.1` before the IP rule was moved ahead of the integer
rule, and `store_test.go:41` was read as a hostname with a port until source locations
were handled first.

Short integers survive on purpose: `expected 2, got 3` stays readable, while
`port 54321` does not need to.

## Configuration

| Setting | Default | Effect |
|---|---:|---|
| `flake_threshold` | 0.15 | Score above which a test is called flaky |
| `quarantine_threshold` | 0.40 | Score above which quarantine is recommended |
| `confidence_runs` | 10 | Runs before a score is fully trusted |
| `fixed_run_streak` | 10 | Consecutive passes before `fixed` |

`quarantine_threshold` sits well above `flake_threshold` because naming a flake is
cheap and removing test coverage is not.
