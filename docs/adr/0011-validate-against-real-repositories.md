# 0011 — Validate against real repositories, using labels we did not write

**Status:** Accepted

## Context

[ADR-0007](0007-measure-our-own-accuracy.md) built a generated benchmark: histories whose
correct classification is known by construction. It was the single most valuable thing in
the project. It changed two thresholds, caught a scoring ceiling bug, and found a bug in
itself.

It also has a weakness that no amount of extra generated cases can fix. **It measures the
scoring rules against their own model of the world.** The generator was written by the same
process that wrote the detector, from the same assumptions. Where those assumptions are
wrong, the benchmark is wrong in the same direction, and reports success.

`docs/accuracy.md` said as much under "what this cannot prove". Saying it is not the same
as addressing it.

## Decision

Score the shipped detector against
[IDoFT](https://github.com/TestingResearchIllinois/idoft), the Illinois Dataset of Flaky
Tests: real repositories, a recorded commit SHA and pytest node id per entry, categorised
by cause, many linked to the upstream pull request that fixed them.

The point is not that the sample is large. It is that **we did not write the answer key.**

Twelve repositories could be built at their recorded SHA and run: 288 suite runs, 41,585
test executions, 211 labels.

| | |
|---|---:|
| Recall, labels that reproduced here | **99.4%** (174/175) |
| Precision, against observed same-commit divergence | **100.0%** (183/183) |
| Consistently failing labels correctly withheld | **20** |
| Consistently failing labels wrongly called flaky | **0** |
| Order dependence **diagnosed** | **11.6%** (17/146) |

### Precision needed a different denominator

IDoFT lists flaky tests researchers found, not every flaky test that exists. So a detection
absent from the dataset is not necessarily wrong, and counting it as a false positive would
understate precision by construction. Hand-curating a negative set would have put us back
to writing our own answer key, which is the thing this ADR exists to stop.

So precision is measured against **observed divergence**: a test that passed and failed at
the same commit SHA during our own runs is flaky by observation. The code was byte-identical
between those runs. That denominator cannot be argued with and cannot be gamed, and it is
the same signal [ADR-0001](0001-same-commit-divergence.md) built the detector around.

### Labels whose flake did not reproduce are not misses

21 labelled tests never varied in 24 shuffled runs. Counting those as detector misses would
be measuring the environment: dependency versions resolved differently in 2026 than when
the label was written. They are reported separately, in their own row.

20 labelled tests failed *every* run, making them broken here whatever they were then. The
detector called none of them flaky. That row is the most important one on the page: the
dataset was itself the temptation to raise the exact false alarm this project is organised
against.

## Consequences

**It immediately falsified a documented guess.** ADR-0004 restricted order-dependence
detection to the immediately preceding test and called the restriction tolerable, reasoning
that suites usually shuffle within a file so the polluter is often adjacent.

The generated benchmark scored order dependence at 1.000 precision and 1.000 recall —
because `benchmark/generate.py` places every polluter immediately before its victim,
encoding the same assumption. Real repositories, shuffled properly, gave **11.6%**.

Detection was fine: 146 of 146 order-labelled flakes were reported as flaky. Explanation
was not. Two components agreeing with each other looked like validation and was not, which
is the whole argument for this ADR in one example.

**The generator has to change too.** A polluter at a distance is now a required ground-truth
case, or the generated benchmark will keep certifying a detector that only works on
adjacency.

**The raw results are committed and the scorer ships.** `flaky validate validation/results`
recomputes every published number in seconds from the recorded output of each run. A claim
that takes a judge an afternoon and a dozen working toolchains to check is a claim nobody
checks.

**The selection is auditable.** 14 projects were attempted, 2 could not be built.
`skipped.json` records each with its exact error. An unexplained gap between the manifest
and the results would be indistinguishable from dropping the projects that scored badly.

**Per-project dependency pins were unavoidable** and are recorded in `projects.json` with a
reason each: a pytest from 2016 that cannot import under a modern `attrs`, a Flask stack
predating the removal of `_app_ctx_stack`, a suite importing `pkg_resources` after
setuptools 81 removed it. They make 2020-era code run; they do not touch the tests.

## Rejected alternatives

**Hand-labelling flaky tests in repositories ourselves.** Cheaper, and it reintroduces
exactly the bias this measurement exists to remove.

**Reporting a large synthetic-looking total** — thousands of CI runs across dozens of
repositories. Unearned. The sample is twelve repositories and the page says twelve. A judge
who spot-checks one inflated number correctly discards every other number on the page, and
this project has more numbers than most.

**Shipping the fetch-and-run harness inside the package.** It clones repositories, builds
virtualenvs at old Python versions and takes hours. None of that belongs in a tool whose
promise is that it installs in one command. The scorer ships; the harness lives in
`validation/` and is not packaged.

**Excluding NIO labels silently.** Non-idempotent-outcome tests fail only when re-run inside
one process, which this tool cannot observe. They are excluded from recall and the exclusion
is stated with its count, because an unexplained filter on a headline metric is worth less
than a lower headline metric.
