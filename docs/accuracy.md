# Measured accuracy

Every flaky-test tool claims to find flaky tests. Almost none of them tell you how
often they are wrong.

This one does, because it has to: the whole argument for the tool is that it can be
trusted not to cry wolf, and refusing to quantify how often it cries wolf would be a
hole in that argument. Reproduce any figure here with `flaky benchmark --seed <n>`.

## Method

`flaky benchmark` generates test histories whose correct classification is known by
construction, runs the **real** analysis pipeline over them, and compares.

Six ground-truth labels are generated, spanning the range that matters:

| Label | Generated as | Should be called |
|---|---|---|
| `flaky` | fails with probability *p*, independently per run, for *p* ∈ {0.05, 0.15, 0.3, 0.5, 0.7, 0.9} | `flaky` |
| `stable` | always passes | `stable` |
| `broken` | always fails | `broken` |
| `regression` | passes until commit *k*, fails after — half of them with flaky history before the break | `regression` |
| `fixed` | flaky early, then a clean streak | `fixed` |
| `order_dependent` | fails if and only if a named polluter ran earlier, at a distance of 1, 2, 3, 5 or 8 tests | `flaky`, cause `order_dependence` |

The hard cases are in there deliberately. A generator that only produced alternating
pass/fail would score 100% and prove nothing.

Flakes that never actually failed inside the window are marked **undetectable** and
excluded rather than counted as misses. No detector can find a flake that left no
evidence, and counting those would measure the tool against information it was never
given.

## Headline

107 labelled tests, 30 runs each, full commit coverage, seed 1234.

| Metric | Value |
|---|---|
| **False alarm rate** — a real break reported as flaky | **0.0%** (0 of 16) |
| **Missed break rate** — a flake reported as a break | 5.4% (2 of 37) |
| Overall accuracy | 93.5% |

The first row is the one that matters most. Reporting a regression as flaky teaches the
user to re-run instead of investigate, which is precisely the habit this tool exists to
break. It is measured separately so it cannot be averaged into a comfortable aggregate.

Across six seeds at 30 runs, the false alarm rate is **0.0% every time**:

| Seed | Accuracy | False alarm | Missed break |
|---|---:|---:|---:|
| 1 | 91.6% | 0.0% | 2.7% |
| 7 | 94.4% | 0.0% | 0.0% |
| 42 | 93.4% | 0.0% | 0.0% |
| 99 | 96.2% | 0.0% | 2.8% |
| 1234 | 93.5% | 0.0% | 5.4% |
| 2026 | 90.6% | 0.0% | 2.8% |

## Per label

| Label | Support | Precision | Recall | F1 |
|---|------:|----------:|-------:|---:|
| `broken` | 8 | 1.000 | 1.000 | 1.000 |
| `stable` | 48 | 0.980 | 1.000 | 0.990 |
| `flaky` | 37 | 1.000 | 0.811 | 0.896 |
| `regression` | 8 | 0.800 | 1.000 | 0.889 |
| `fixed` | 6 | 0.600 | 1.000 | 0.750 |

Precision and recall per label rather than a single accuracy number, because the
classes are unbalanced: a population that is 45% stable can score 45% accuracy by
calling everything stable, and that figure would be meaningless.

**`flaky` precision is 1.000.** Nothing was called flaky that was not. Recall of 0.811
means about a fifth of genuine flakes were labelled something else — see the confusion
matrix for what.

**`fixed` precision of 0.600 is the weakest number here**, and it is a genuine
limitation rather than a bug. See below.

## Confusion matrix

| truth \ said | broken | fixed | flaky | regression | stable |
|---|---|---|---|---|---|
| **broken** | 8 | · | · | · | · |
| **fixed** | · | 6 | · | · | · |
| **flaky** | · | 4 | 30 | 2 | 1 |
| **regression** | · | · | · | 8 | · |
| **stable** | · | · | · | · | 48 |

The whole `broken`, `regression` and `stable` rows are clean: no real break was ever
called flaky, and no stable test was ever accused of anything.

Every error is in the `flaky` row, and each is explainable:

- **4 flakes called `fixed`.** All are low-rate flakes (*p* = 0.05–0.15) that failed
  once or twice early and then passed 20+ times running. On that evidence "fixed" is a
  defensible reading; it is simply wrong, because they will flake again. Any
  history-based approach has this problem — a rare flake and a fixed flake look
  identical until the rare one fails again.
- **2 flakes called `regression`.** High-rate flakes (*p* = 0.7–0.9) whose recent runs
  happened to all fail. Down from 7 before the streak-beats-chance rule
  ([ADR-0006](adr/0006-streak-beats-chance.md)).
- **1 flake called `stable`.** A *p* = 0.05 flake that failed once in 30 runs, scoring
  below the flake threshold. Working as configured.

## Order dependence

Polluters are generated at distances of 1, 2, 3, 5 and 8 tests from their victim, cycled
across the population. **8 is beyond the detector's default search window on purpose**: a
benchmark whose hardest case sits inside the implementation's reach cannot report a limit.

| Metric | Value |
|---|---|
| Diagnosed as order-dependent | 8 of 8 (recall 100%) |
| Polluter named | 7 of 8 |
| Correct polluter, of those named | 7 of 7 (precision 1.000) |

The one not named is the distance-8 case. The detector declines rather than blaming the
nearest bystander, which is the behaviour worth having: precision is **1.000 at every
search window**, and across 300 generated tests with no polluter at all, none was ever
given one.

### How far back to look

`flaky benchmark --sweep window`, three seeds:

| Search window | Polluter named | Precision | False alarm | Accuracy |
|---|---:|---:|---:|---:|
| 1 | 6/24 | 1.000 | 0.0% | 94.7% |
| 2 | 12/24 | 1.000 | 0.0% | 94.7% |
| 3 | 18/24 | 1.000 | 0.0% | 94.7% |
| **6** (default) | **21/24** | **1.000** | 0.0% | 94.7% |
| 8 | 24/24 | 1.000 | 0.0% | 94.7% |
| 12 | 24/24 | 1.000 | 0.0% | 94.7% |

Naming rate up 3.5× from the old adjacency-only search, at no cost to accuracy, false
alarms or missed breaks. The default is 6 rather than 8 because 8 is exactly the
generator's hardest distance, and choosing it would be tuning to the fixture — and because
on **real** repositories the wider window buys nothing at all. That negative result, and
what is actually blocking diagnosis there, is in
[ADR-0014](adr/0014-search-a-window-for-the-polluter.md) and
[`docs/real-world.md`](real-world.md).

Three fixture bugs had to be fixed before any of the above could be measured, and all
three produced *plausible* numbers: spacer tests confounded with the polluter (2/8 at every
window), the victim pinned to one position so detection correctly declined every time
(0/8), and a scorer dividing precision by the wrong denominator so an honest refusal to
attribute counted the same as a wrong attribution (0.875 while every name given was
correct).

This is the second time the harness has been the thing at fault. The
first generator gave every order-dependent group the same handful of positions and
shared filler test ids, so multiple tests occupied one position per run. Predecessor
computation sorts by position, and with ties that ordering is arbitrary, so the
reported polluter precision was 0.000 while the detector was working correctly.

Worth stating plainly: the harness was measuring its own bug. That is an argument for
testing the harness as carefully as the thing it measures, which is what
[`tests/test_benchmark.py`](../tests/test_benchmark.py) now does.

## How much history is needed

| Runs recorded | Flaky recall | Flaky precision | False alarm rate | Accuracy |
|---|---:|---:|---:|---:|
| 5 | 0.733 | 0.846 | **12.5%** | 84.0% |
| 10 | 0.931 | 0.871 | 0.0% | 91.9% |
| 20 | 0.771 | 1.000 | 0.0% | 92.4% |
| 30 | 0.811 | 1.000 | 0.0% | 93.5% |
| 50 | 0.816 | 1.000 | 0.0% | 93.5% |

**Use at least 10 runs.** Below that the tool is meaningfully less trustworthy, and at
5 runs it misreports a real break as flaky one time in eight. Publishing that number
rather than burying it is the point of having a benchmark.

That 12.5% is already an improvement: before the required streak was made proportional
to available history, the false alarm rate at 5 runs was **50%**. See
[ADR-0006](adr/0006-streak-beats-chance.md).

The non-monotonic recall between 10 and 20 runs is an artefact worth explaining rather
than smoothing over: `fixed` requires 10 consecutive passes, which is impossible in a
10-run window, so low-rate flakes that *would* be labelled `fixed` stay `flaky` and
recall looks better than it is.

## How much commit data is needed

| Commit coverage | Flaky recall | Flaky precision | False alarm rate | Accuracy |
|---|---:|---:|---:|---:|
| 0% | 0.865 | 0.889 | **25.0%** | 91.6% |
| 25% | 0.405 | 1.000 | 0.0% | 79.4% |
| 50% | 0.622 | 1.000 | 0.0% | 86.9% |
| 100% | 0.811 | 1.000 | 0.0% | 93.5% |

This table is the central design claim of the whole tool, checked against a
measurement. Same-commit divergence is supposed to be the load-bearing signal, and
here is what happens without it: the false alarm rate goes from 0% to **25%**.

So the advice to run inside a git repository, or pass `--commit`, is not
box-ticking — without commit SHAs the tool misclassifies a real break as flaky a
quarter of the time, and it says so in every output format when commit data is missing.

The dip at 25% coverage is instructive. Partial coverage is worse for recall than none
at all, because scoring switches to the divergence-weighted formula while having too
few observable commits to populate it. Precision stays at 1.000 throughout, so nothing
is falsely accused; the tool simply finds less.

## What this does not prove

Stated plainly, because a page of favourable numbers deserves its caveats in the same
place rather than in a footnote.

- **Synthetic data is not real data.** The generator models failures as independent
  draws. Real flakiness clusters: a loaded CI box makes every timing-sensitive test
  fail together. So these figures measure the scoring rules against their own model of
  the world.
- **It cannot measure what it does not generate.** Infrastructure flakes, flakes that
  only appear under parallelism, and flakes whose test id changes between runs are all
  absent from the population.
- **Class balance is chosen, not observed.** 45% stable, 35% flaky. A real suite is
  overwhelmingly stable, which would raise accuracy and leave precision and recall
  roughly unchanged — which is exactly why per-label figures are the ones reported.
- **No comparison against other tools.** Benchmarking someone else's tool fairly
  requires understanding their tuning, and getting that wrong would be worse than not
  doing it.

It is still a great deal more than the evidence this project had before the harness
existed, which was one hand-built demo suite of sixteen tests inspected by eye — and
thresholds tuned against that same suite, a sample size of one.

## Reproducing

```sh
flaky benchmark                          # the headline table
flaky benchmark --seed 99                # a different population
flaky benchmark --runs 10                # shorter history
flaky benchmark --coverage 0.0           # no commit SHAs
flaky benchmark --sweep runs             # the history table above
flaky benchmark --sweep coverage         # the coverage table above
flaky benchmark --format json            # machine-readable
```

Runs in about two seconds. Assertions on these numbers are part of the test suite
([`tests/test_benchmark.py`](../tests/test_benchmark.py)), with bounds set looser than
the current figures so that a real degradation fails the build without a one-percent
drift doing so.
