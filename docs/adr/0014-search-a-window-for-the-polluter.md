# 0014 — Search a window for the polluter, and publish that it did not help on real code

**Status:** Accepted, with a negative result recorded

## Context

[ADR-0004](0004-order-dependence-needs-a-polluter.md) requires naming a polluter before
reporting order dependence, and only ever considered the **immediately preceding** test.
Its docstring called that a known limitation and guessed it would be tolerable, reasoning
that suites usually shuffle within a file so the polluter is often adjacent.

The generated benchmark agreed enthusiastically: order-dependence precision and recall both
1.000. It agreed because `benchmark/generate.py` placed every polluter immediately before
its victim, encoding the detector's own assumption into the answer key. **Two components
built from the same belief validated each other.**

[ADR-0011](0011-validate-against-real-repositories.md) broke the tie. Scored against 146
order-labelled tests in real repositories, detection was **146 of 146** and diagnosis was
**17**. The tool knew those tests were flaky and could not say why.

## Decision

Search a bounded window of preceding tests, defaulting to six, with a multiplicity
correction. Also fix the generator, the significance statistic, and a bug in the scorer that
was hiding the truth about all of it.

### The generator had to change first

A benchmark whose polluters are all adjacent cannot measure a change to how far back the
search looks. `POLLUTER_DISTANCES = (1, 2, 3, 5, 8)` now cycles across the population, with
real spacer tests executing in the gap. 8 is deliberately beyond the default window: a
benchmark whose hardest case sits inside the implementation's reach cannot report a limit.

Two mistakes were made building that fixture and both are worth recording, because both
produced *plausible* numbers:

**The spacers were confounded.** First version ran them between polluter and victim only in
the polluting layout, so their presence correlated perfectly with the polluter's. Polluter
accuracy came out 2/8 at every window, because the nearest spacer was blamed every time. The
fix is that spacers now run before the victim in *both* layouts, so only the polluter's
presence differs.

**The victim was pinned to one position.** Second version put the victim at the same index in
both layouts. Detection requires the victim's position to vary — a test that always runs at
index 12 cannot have its outcome explained by where it ran — so the detector correctly
declined on every case and the fixture silently tested nothing. Reported as 0/8.

### A window needs a multiplicity correction

Widening from one candidate to six means six hypotheses per victim, and a 5% threshold
applied to six candidates fires on noise about a quarter of the time. That is the trap the
first two versions of `ordering.py` already fell into, arriving by a new route.

The threshold is divided by the number of candidates with enough observations to be testable
(Bonferroni). Crude, and the easiest to explain, which for a rule that has misfired twice is
the right trade.

### The significance statistic was wrong for anything but a perfect correlation

It was `base_rate ** fail_count`: the probability that *every* observation after the candidate
failed. Correct when the share is 1.0 and increasingly conservative below it. Now an exact
binomial tail over the runs where the candidate preceded the victim — the same
`statistics.tail_at_least` the branch comparison and fix verification use.

### The scorer was misreporting precision

`polluter_precision` divided by `order_dependent_diagnosed`, which counts tests diagnosed as
order dependent *by any route* — including the message-text heuristic, which needs no polluter
at all. A victim the detector honestly declined to attribute therefore counted against
precision exactly as though it had been attributed wrongly.

That is how the reported 0.875 came about while every polluter actually named was correct.
Precision now divides by how many were named, and the naming rate is reported separately,
because declining to attribute and attributing wrongly are not the same mistake and a metric
that conflates them pushes the detector towards guessing.

## Measured outcome

`flaky benchmark --sweep window`, three seeds, 30 runs:

| Search window | Polluter named | Polluter precision | False alarm | Accuracy |
|---|---:|---:|---:|---:|
| 1 | 6/24 | 1.000 | 0.0% | 94.7% |
| 2 | 12/24 | 1.000 | 0.0% | 94.7% |
| 3 | 18/24 | 1.000 | 0.0% | 94.7% |
| 4 | 18/24 | 1.000 | 0.0% | 94.7% |
| **6** | **21/24** | **1.000** | 0.0% | 94.7% |
| 8 | 24/24 | 1.000 | 0.0% | 94.7% |
| 12 | 24/24 | 1.000 | 0.0% | 94.7% |

Naming rate up 3.5×. **Precision 1.000 at every window** — it never once named the wrong
test. Accuracy, false-alarm rate and missed-break rate unchanged. Separately, across 300
tests with no polluter by construction, **zero** were given one at any window.

## The negative result

Re-analysing the recorded runs from the twelve real repositories, at the same windows:

| Search window | Order-labelled flakes | Diagnosed | Polluter named |
|---|---:|---:|---:|
| 1 | 146 | 16 (11.0%) | 8 (5.5%) |
| 3 | 146 | 14 (9.6%) | 6 (4.1%) |
| 6 | 146 | 16 (11.0%) | 8 (5.5%) |
| 8 | 146 | 15 (10.3%) | 7 (4.8%) |
| 12 | 146 | 15 (10.3%) | 7 (4.8%) |

**It does not help.** The numbers drift around 5% with no trend. The median distance of the
polluters it does find is 1.0 even at window 12 — widening the search finds nothing new,
because the extra candidates tighten the correction by exactly as much as the extra reach
buys.

So the hypothesis this ADR was opened to implement — that the adjacency restriction was what
limited diagnosis — is **wrong for real code**. Recorded rather than quietly dropped, because
it was the obvious hypothesis and the generated benchmark endorsed it.

### What is actually blocking diagnosis

Instrumenting every gate over those 146 tests:

| Where it stops | Count |
|---|---:|
| No candidate correlates strongly enough (share < 0.9) | ~109 |
| Cleared the share gate, failed the significance test | 15 |
| A polluter was named | 7–8 |
| Too few observations on one side | 9 |
| Fails too often for anyone to be blamed (≥75%) | 6 |

Median best-candidate share: **0.73**. Real pollution is not near-deterministic.

Lowering `POLLUTER_SHARE_THRESHOLD` to 0.6 was tried on that evidence and **reverted**: real
polluter naming went from 8 to 8, because the candidates that newly cleared the share gate
then failed the significance test underneath it. A relaxed safety threshold with no measured
benefit is not a change this project ships.

The honest reading is that correlational polluter identification has a real ceiling on
randomly-shuffled real suites. IDoFT's order-dependent entries were found with *deliberate*
orderings, often full reversal; random shuffling reproduces a specific pairing too rarely,
and often no single predecessor explains the failures at any defensible confidence.

## Consequences

**Default window 6, not 8.** 8 reaches 24/24 on generated data, and the generator's hardest
case is a distance of 8 — picking it would be tuning to the fixture. 6 is the value that is
neutral on real data (8 named, tying window 1) and captures most of the generated gain. It is
a config knob, `order_window`, so a project can trade differently.

**`docs/real-world.md` publishes 11%.** The tool detects order-dependent tests essentially
perfectly and explains about one in nine. That gap is the most useful thing the real-world
evaluation has produced and it stays on the page.

**Fix verification uses the same window.** `count_exposures` counts a polluter running ahead
of a victim anywhere in the window, matching detection. Left at distance 1 it would report
zero exposures for a polluter found four tests back, and every such fix would be permanently
unverifiable for a reason that is an artefact of the mismatch.

**The distance-1 index survives** for the dashboard's neighbour table, where "what ran
immediately before" is a thing being displayed rather than searched.

**`OrderEvidence` carries distance, lift, observations and candidate count.** A reader is
entitled to the multiplicity correction's denominator, since that is exactly what makes a
0.05 threshold too generous.

## Rejected alternatives

**Dropping the multiplicity correction** to recover real-world detections. It would trade a
misfire this module has already fixed twice for the same misfire at a new distance.

**Reading test source to find shared state.** Would break the language-agnostic property
that makes the tool work for pytest, jest, go and .NET without knowing anything about them.

**Reporting order dependence without naming a polluter**, on the strength of position
separation. Measured and rejected in ADR-0004: position tracks machine warm-up. Re-checking
it here would repeat a settled experiment.

**Leaving the generator adjacency-only** and reporting 1.000. Available, comfortable, and the
reason this ADR was needed.
