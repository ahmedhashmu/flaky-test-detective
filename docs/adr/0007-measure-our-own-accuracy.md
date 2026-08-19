# 0007 — Measure accuracy against ground truth

**Status:** Accepted

## Context

The product steering for this project states that the tool's credibility "rests on never
crying wolf".

After the first implementation round, all evidence for that claim was: one hand-built demo
suite of sixteen tests, inspected by eye. The thresholds in `analysis/flakiness.py` and
`analysis/ordering.py` had been tuned by looking at that same suite — a sample size of
one.

That is not sufficient for a tool whose entire argument is trustworthiness. A flaky-test
detector that cannot state its own false-positive rate is asking for exactly the blind
trust it exists to replace.

## Decision

Build `flaky benchmark`: generate test histories whose correct classification is known by
construction, run the **real** analysis pipeline over them, and report precision and
recall per verdict.

Four commitments make it meaningful rather than decorative:

1. **Generate the hard cases.** A generator producing only alternating pass/fail would
   score 100% and prove nothing. So the population spans failure rates from 0.05 (nearly
   undetectable) to 0.9 (nearly indistinguishable from broken), includes regressions both
   with and without flaky history, and includes runs with no commit SHA.
2. **Two metrics get their own headline.** The rate at which a real break is called flaky,
   and the reverse. Averages hide precisely what matters here.
3. **Publish the weak numbers.** A benchmark reporting only favourable results is
   marketing.
4. **Mark the impossible cases.** A flake that never failed inside the window left no
   evidence; counting it as a miss would measure the tool against information it never
   had.

## Consequences

**Two real defects found immediately.** Both are recorded in
[0006](0006-streak-beats-chance.md): 7 of 37 known flakes reported as regressions, and a
50% false alarm rate at five runs of history. Neither was visible on the demo suite.

**Threshold changes now require a number.** Both fixes in this round are justified by a
before/after table rather than by argument.

**A design claim became a measurement.** Same-commit divergence was *asserted* to be the
load-bearing signal. Sweeping commit coverage shows the false alarm rate going from 0% to
25% without it. The claim was right, and now it is evidenced.

**Documented limits are now numeric.** "Use at least 10 runs" comes from a table, not a
guess.

### The harness had a bug, and that is the important lesson

The benchmark initially reported polluter precision of **0.000** — every order-dependent
test diagnosed correctly, none naming the right culprit.

The detector was fine. The *generator* gave every order-dependent group the same handful
of positions and shared filler test ids, so several tests occupied one position within a
run. Predecessor computation sorts by position, and with ties that ordering is arbitrary.

**The harness was measuring its own bug.** Had the number been merely mediocre rather than
absurd, it might have been accepted and the detector "fixed" to chase it — making the real
code worse to satisfy a broken measurement.

Two consequences: the generator is now tested as carefully as the scorer
([`tests/test_benchmark.py`](../../tests/test_benchmark.py) asserts positions are unique
per run, among other things), and a suspiciously extreme number is now treated as a
reason to check the instrument first.

## What it does not prove

Recorded here as well as in [accuracy.md](../accuracy.md), because a caveat only in the
marketing copy is not a caveat.

- Synthetic data models failures as independent draws. Real flakiness clusters — a loaded
  CI box makes every timing-sensitive test fail together.
- It cannot measure what it does not generate: infrastructure flakes, parallelism-only
  flakes, tests whose id changes between runs.
- Class balance is chosen, not observed, which is why per-label figures are reported
  rather than a single accuracy number.
- No comparison against other tools. Benchmarking someone else's tool fairly requires
  understanding their tuning, and getting it wrong would be worse than not doing it.
