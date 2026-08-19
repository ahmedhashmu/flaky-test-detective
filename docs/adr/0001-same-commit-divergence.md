# 0001 — Same-commit divergence as the primary signal

**Status:** Accepted

## Context

A flaky test produces different outcomes for the same code. Detecting one therefore
requires distinguishing "the outcome changed" from "the code changed", and a single
failed CI run carries no information about which happened.

Most approaches reach for one of:

1. **Retry and see.** Re-run the failure; if it passes, call it flaky. Cheap, and wrong
   often enough to be dangerous: a genuine failure that depends on machine state will
   also pass on retry, and this is how real regressions get waved through.
2. **Failure-rate thresholds.** Anything failing between 5% and 95% of the time is
   flaky. Cannot distinguish a flake from a test that broke a fortnight ago and has been
   failing since.
3. **Pattern matching on failure text.** "Timeout" means flaky. Guesses about the cause
   dressed up as detection.

## Decision

Treat **same-commit divergence** as the primary signal: one test, one commit SHA, both a
pass and a fail.

If a test passed and failed at the same SHA, the code was byte-identical between those
runs. Whatever varied, it was not the code. That is not an inference — it is the
definition of flaky, observed directly.

Flip rate is kept as a secondary signal at lower weight, because divergence needs the
test to have run more than once per commit and that is not always true.

## Consequences

**A commit SHA on every run becomes load-bearing.** Hence automatic detection from git
and from six CI providers, an explicit `--commit` override, and a warning in every
output format when commit data is missing. Measured cost of running without it: the
false alarm rate goes from 0% to 25% ([accuracy](../accuracy.md)).

**Running the suite once per commit is not enough.** Divergence needs at least two runs
at one SHA, which is why `flaky hunt` exists rather than only passive ingestion.

**The scoring weights follow from the signal hierarchy.** 0.7 on proof, 0.3 on
inference. The exact split is a judgement; the ordering is not.

**Runner-recorded retries count as proof too.** Surefire's `<flakyFailure>` is the
runner stating it watched one test produce two outcomes inside a single run. Same
epistemic status as divergence, so it gets the same weight.

## Verification

`flaky benchmark --sweep coverage` measures the claim directly. Without commit data the
false alarm rate is 25%; with it, 0%.
