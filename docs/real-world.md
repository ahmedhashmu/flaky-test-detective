# Real-world validation

[`docs/accuracy.md`](accuracy.md) measures the detector against generated histories whose
labels are known by construction. That is reproducible, and it has one unavoidable
weakness: it measures the scoring rules against their own model of the world. A detector
can fit its own generator perfectly and still be useless on real software.

So this page measures something the generator cannot. **Does it find flaky tests in real
repositories, that real people confirmed were flaky?**

## The answer

12 repositories · 288 suite runs · **41,585 test executions** · 211 published labels

| Metric | Value | Of |
|---|---:|---|
| **Recall** — labelled flakes found | **99.4%** | 174 / 175 that reproduced here |
| **Precision** — flagged with same-commit proof | **100.0%** | 183 / 183 |
| Consistently failing, correctly **not** called flaky | **20** | the false alarm that matters most |
| Consistently failing, wrongly called flaky | **0** | |
| Order dependence **diagnosed** | 11.6% | 17 / 146 |
| Polluter named | 6.2% | 9 / 146 |

Recomputable in seconds, because the raw output of every run is committed:

```sh
flaky validate validation/results
```

The last two rows are the weak ones and they are not buried. The detector finds real
order-dependent tests almost perfectly and then largely fails to *explain* them. See
[the limitation it exposed](#the-limitation-this-exposed).

## Where the labels come from

[IDoFT](https://github.com/TestingResearchIllinois/idoft), the Illinois Dataset of Flaky
Tests, from the Testing Research group at UIUC. For each entry it records a repository, a
commit SHA, a pytest node id and a cause category; many carry a link to the upstream pull
request that fixed the test.

**We did not write the answer key.** That removes the most obvious way to produce a
flattering result, and it is the reason this page exists in addition to the generated
benchmark.

Scored against `idoft@4903dc9233`. The dataset SHA is recorded in every result file,
because the dataset is actively maintained and a number that does not name its label set
cannot be reproduced later.

## Method

For each project in [`validation/projects.json`](../validation/projects.json):

1. Check out the exact SHA IDoFT recorded.
2. Install it into its own virtualenv at a Python version it was written for.
3. Run its suite 24 times through `flaky hunt`, with order shuffled per iteration by
   `pytest-random-order`. Shuffling is the instrument: a suite that always runs in one
   order is deterministic, and there is nothing to find.
4. `flaky analyze` the history and compare each verdict with the label.

Same shipped `analyze()` the CLI and dashboard call. No per-repository tuning, no
thresholds adjusted to improve a score.

Our reconstructed test ids match IDoFT's node-id format exactly, so labels are matched by
string rather than by fuzzy name comparison. That is a direct payoff from
[ADR-0002](adr/0002-reconstruct-pytest-node-ids.md), which reconstructs pytest node ids
instead of trusting the `classname` attribute in the XML.

## Why these two metrics

**Recall** is straightforward: of labelled tests that actually misbehaved during our runs,
how many did we report as flaky?

**Precision needs more care.** IDoFT lists flaky tests researchers found, not every flaky
test that exists. Treating a detection absent from the dataset as a false positive would
understate precision by construction, and inventing a hand-curated negative set would put
us back to writing our own answer key.

So precision is measured against something that cannot be argued with. A test that
**passed and failed at the same commit SHA during our own recorded runs** is flaky. Not
inferred — observed. The code was byte-identical between those runs. Of the 183 tests the
detector called flaky, **183 show that divergence.**

Those two close the loop: recall says we find what humans found, precision says we do not
flag what did not actually vary.

## Per repository

| Repository | Tests | Runs | Labels | Reproduced | Found | Recall |
|---|---:|---:|---:|---:|---:|---:|
| [santoshphilip/eppy](https://github.com/santoshphilip/eppy) | 218 | 24 | 87 | 83 | 83 | 100.0% |
| [huashengdun/webssh](https://github.com/huashengdun/webssh) | 89 | 24 | 29 | 26 | 26 | 100.0% |
| [marshmallow-code/flask-smorest](https://github.com/marshmallow-code/flask-smorest) | 575 | 24 | 16 | 0 | 0 | n/a |
| [spulec/freezegun](https://github.com/spulec/freezegun) | 112 | 24 | 15 | 15 | 15 | 100.0% |
| [django-beam/django-beam](https://github.com/django-beam/django-beam) | 80 | 24 | 11 | 11 | 11 | 100.0% |
| [voytekresearch/fooof](https://github.com/voytekresearch/fooof) | 191 | 24 | 11 | 11 | 11 | 100.0% |
| [didix21/mdutils](https://github.com/didix21/mdutils) | 102 | 24 | 8 | 8 | 8 | 100.0% |
| [microsoft/knack](https://github.com/microsoft/knack) | 232 | 24 | 8 | 3 | 3 | 100.0% |
| [hefnawi/json-storage-manager](https://github.com/hefnawi/json-storage-manager) | 9 | 24 | 7 | 7 | 7 | 100.0% |
| [mtik00/yamicache](https://github.com/mtik00/yamicache) | 22 | 24 | 7 | 4 | 4 | 100.0% |
| [chaosmail/python-fs](https://github.com/chaosmail/python-fs) | 61 | 24 | 6 | 1 | 1 | 100.0% |
| [laike9m/pdir2](https://github.com/laike9m/pdir2) | 33 | 24 | 6 | 6 | 5 | 83.3% |

## By labelled cause

| Category | Reproduced | Found | Recall | Meaning |
|---|---:|---:|---:|---|
| `OD-Vic` | 128 | 128 | 100.0% | order-dependent victim: passes in order, fails after a polluter |
| `OD-Brit` | 19 | 18 | 94.7% | order-dependent brittle: fails alone, passes after a state-setter |
| `NOD` | 28 | 28 | 100.0% | nondeterministic: timing, concurrency, randomness, network |

`NOD` mattered to include. Without webssh and django-beam, every real-world label in the
sample would have been an ordering bug, and a detector tested only on ordering has not
been tested on half the problem.

## The limitation this exposed

Detection: 146 of 146 order-labelled flakes found. Diagnosis: **17**.

[ADR-0004](adr/0004-order-dependence-needs-a-polluter.md) requires naming a polluter
before reporting order dependence, and only considers the **immediately preceding** test.
The docstring called that a known limitation and guessed it would be tolerable, on the
reasoning that suites usually shuffle within a file so the polluter is often adjacent.

Real data says otherwise. In a randomly shuffled 100-test suite, a specific polluter lands
immediately before its victim in roughly one run in a hundred. The guess was wrong, and it
took real repositories to show it: the generated benchmark places polluters immediately
before their victims, so it scored order-dependence precision and recall at 1.000 while
the real-world figure was 11.6%.

**A benchmark that agrees with your assumptions cannot correct them.** That is the most
useful thing this page has produced so far.

### The obvious fix did not work

The generator was corrected to place polluters at distances of 1, 2, 3, 5 and 8, and the
detector was extended to search a window of preceding tests with a multiplicity correction.
On generated data that is a clear win: polluter naming 6/24 → 21/24 at precision 1.000
([docs/accuracy.md](accuracy.md)).

Re-analysing these same recorded runs at each window:

| Search window | Diagnosed | Polluter named |
|---|---:|---:|
| 1 | 16 (11.0%) | 8 (5.5%) |
| 3 | 14 (9.6%) | 6 (4.1%) |
| 6 | 16 (11.0%) | 8 (5.5%) |
| 8 | 15 (10.3%) | 7 (4.8%) |
| 12 | 15 (10.3%) | 7 (4.8%) |

**No trend.** The median distance of the polluters it does find is 1.0 even at window 12:
widening the search finds nothing new, because the extra candidates tighten the significance
threshold by as much as the extra reach buys.

So the hypothesis was wrong for real code. It is recorded rather than dropped, because it was
the obvious hypothesis and the generated benchmark endorsed it enthusiastically.

### What is actually blocking it

Instrumenting every gate over the 146 order-labelled flakes:

| Where it stops | Count |
|---|---:|
| No candidate correlates strongly enough (share < 0.9) | ~109 |
| Cleared the share gate, failed the significance test | 15 |
| A polluter was named | 8 |
| Too few observations on one side | 9 |
| Fails too often for anyone to be blamed (≥ 75%) | 6 |

Median best-candidate share: **0.73**. Real pollution is not near-deterministic, so the 0.9
share threshold looked like the binding constraint. Lowering it to 0.6 was tried and
**reverted**: naming went from 8 to 8, because the candidates that newly cleared the share
gate then failed the significance test underneath it.

The honest reading is that correlational polluter identification has a ceiling on randomly
shuffled real suites. IDoFT's order-dependent entries were found with *deliberate* orderings,
often full reversal; random shuffling reproduces a specific pairing too rarely, and often no
single predecessor explains the failures at any defensible confidence.

**So the number stays at 11%.** The tool detects order-dependent tests essentially perfectly
and explains about one in nine. Full sequence in
[ADR-0014](adr/0014-search-a-window-for-the-polluter.md).

## What this cannot tell you

**The sample is small and selected for installability.** 14 projects were tried; 2 could
not be built at their recorded SHA. That exclusion is not random — it correlates with age
and dependency hygiene. Every dropped project and the exact error is recorded in
[`validation/results/skipped.json`](../validation/results/skipped.json), so the selection
is auditable instead of invisible.

**Per-project dependency pins were required** and are recorded in `projects.json` with the
reason for each: an ancient pytest that cannot import under a modern `attrs`, a Flask
stack predating the removal of `_app_ctx_stack`, a suite importing `pkg_resources` after
setuptools 81 removed it. None of these change the tests; they make 2020-era code run in
2026.

**NIO labels are excluded from recall.** Non-idempotent-outcome tests pass on first
execution and fail when re-run *inside the same process*. This tool reads JUnit XML from
separate executions, so unless a suite runs a test twice in one session there is nothing
to observe. 15 of the 211 labels are NIO and are counted separately rather than averaged
in, because folding them into recall would hide a real limitation.

**21 labelled tests never varied here** and are not counted as misses. A flake that did
not occur cannot be detected, and scoring the detector against a label whose behaviour did
not reproduce would be measuring the environment. flask-smorest is the extreme case: all
16 of its labelled victims were stable across 24 shuffled runs.

**20 labelled tests failed every single run** — broken in this environment, whatever they
were when the label was written. The detector called none of them flaky. That is the row
worth checking most carefully, because reporting a consistent failure as flaky is the
failure mode this whole project is organised against, and here the dataset itself was the
temptation to get it wrong.

**9 tests were flagged that IDoFT does not list**, all 9 with observed same-commit
divergence. Most likely flaky tests the dataset does not cover. Stated as an observation,
not claimed as a discovery: confirming them would mean filing upstream, which is outside
what this measurement can support.
