# Real-world validation

The [accuracy benchmark](../docs/accuracy.md) measures the detector against generated
histories whose labels are known by construction. That is reproducible and it is honest
about what it is: a measurement of the scoring rules against their own model of the world.

This directory measures something the generator cannot: whether the detector finds flaky
tests **in real projects, that real people confirmed were flaky.**

## Where the labels come from

[IDoFT](https://github.com/TestingResearchIllinois/idoft), the Illinois Dataset of Flaky
Tests, maintained by the Testing Research group at UIUC. It records, per project, a commit
SHA and a pytest node id for tests found to be flaky, categorised by cause. Many entries
carry a link to the pull request that fixed them upstream.

The labels are therefore **not ours**. We did not decide which tests are flaky, which
removes the most obvious way to produce a flattering result.

## Method

For each project in [`projects.json`](projects.json):

1. Check out the exact SHA IDoFT recorded.
2. Install it in its own virtualenv, at a Python version it was written for.
3. Run its suite `N` times through `flaky hunt`, with test order shuffled per iteration
   by `pytest-random-order`. Shuffling is what exposes order-dependent tests; without it,
   a suite that always runs in the same order is deterministic and there is nothing to
   find.
4. `flaky analyze` the resulting history and compare each verdict against the label.

The detector is the shipped one. No project-specific tuning, no thresholds adjusted per
repository, and the same `analyze()` the CLI and the dashboard call.

Reproduce with:

```sh
python validation/run.py            # hours: clones, installs and runs every project
flaky validate validation/results   # seconds: re-scores the committed raw results
```

## The two numbers, and why they are these two

**Recall against IDoFT labels.** Of the labelled flaky tests we could actually execute,
how many did the detector report as flaky? This is a fair recall measure because the
labels are external.

**Precision against observed divergence.** IDoFT is not exhaustive — it lists flaky tests
that researchers found, not every flaky test that exists — so a detection absent from the
dataset is not automatically wrong, and counting it as a false positive would understate
precision by construction.

So precision is measured against something that cannot be argued with: a test that
**passed and failed at the same commit SHA during our own runs** is flaky. Not inferred,
observed. The code was byte-identical. Of the tests the detector called flaky, how many
show that divergence in the recorded history?

Those two numbers together close the loop. Recall says we find what humans found.
Precision says we do not flag what did not actually vary.

## What this cannot tell you

**The sample is small and selected for installability.** Projects that could not be built
at their recorded SHA were dropped, and that is not a random exclusion: it correlates with
age and dependency hygiene. `skipped.json` records every project dropped and why, so the
selection is auditable rather than invisible.

**NIO tests are largely out of scope, and the numbers say so.** Non-idempotent-outcome
tests pass on their first execution and fail when re-run *within the same process*. This
tool reads JUnit XML from separate suite executions, so unless a project's own suite runs
a test twice in one session, there is nothing for the detector to see. Reported
separately rather than folded into recall, because averaging them in would hide a real
limitation.

**Environment drift.** IDoFT recorded these labels on other machines, at other times,
with dependency versions resolved then. A test labelled order-dependent in 2021 may be
deterministically broken or deterministically passing here. Where a labelled test never
varied in our runs, that is reported as **not reproducible in this environment** rather
than as a detector miss — blaming the detector for a flake that did not occur would be as
dishonest as the reverse.
