# 0009 — An explainable trust score, not a fitted index

**Status:** Accepted

## Context

The dashboard needed a headline number. `flaky stats` reports row counts and
`flaky analyze` reports per-test verdicts; neither answers the question a tech lead
actually asks, which is whether the suite can be believed right now.

The obvious implementation is a weighted index: pick a handful of signals, tune the
weights until the demo database scores well, call it a reliability score. It looks more
sophisticated than a subtraction and it is strictly worse, because nobody — including
its author, six months later — can say why it returned 71.

That is not a general complaint about metrics. It is specific to this tool. The argument
for flaky-test-detective is that a conclusion you cannot check is a conclusion you should
not trust; [ADR-0001](0001-same-commit-divergence.md) ranks evidence over inference and
[ADR-0007](0007-measure-our-own-accuracy.md) publishes the tool's own error rates
including the unflattering ones. Putting an unexaminable number in the largest typeface
on the screen would contradict all of that in the most visible place in the product.

## Decision

The trust score is **100 minus a sum of named penalties**. Nothing else.

| Component | Ceiling | Deducts for |
|---|---:|---|
| Unresolved breaks | 35 | Regressions and never-passing tests |
| Flaky tests | 30 | Active flakes, weighted by score rather than counted |
| Commit evidence | 20 | Runs without a commit SHA |
| Quarantine debt | 15 | Entries left past their expiry |

Each component carries its ceiling and a sentence of reasoning naming what it saw, and
every one of them is displayed. A healthy component shows `ok` rather than being hidden,
so the reader can see the full set of things being checked rather than only the ones
currently complaining.

Three of those numbers encode judgements worth stating out loud:

**Breaks outrank flakes** — 35 against 30, and saturating at two tests rather than ten. A
suite with one real regression is less trustworthy than one with five known flakes,
because the flakes are known. Ranking it the other way would reward the re-run habit this
tool exists to break.

**Flakes are weighted, not counted.** The penalty sums the flakes' flakiness scores, so a
test failing 1 run in 20 costs almost nothing while one failing 10 in 20 costs a lot.
Counting them would make "quarantine the nearly-stable ones" the cheapest way to raise
the score, which is precisely the wrong thing to incentivise.

**Missing commit data costs 20 points**, and that is not a style complaint. Benchmarked,
the false alarm rate rises from 0% to 25% without commit SHAs
([`docs/accuracy.md`](../accuracy.md)). The verdicts above genuinely are weaker, and the
headline number is the honest place to say so.

## Consequences

**Rounding needed its own field, and finding out why was embarrassing.** The score is
displayed as a whole number, because 57.6/100 implies a precision the inputs do not have.
Component penalties are displayed to one decimal. Running this project's own README
verification snippet printed:

```
58 = 57.6
```

The penalties sum to 42.4, so the deduction is 42.4 and the rounded score is 58. A reader
adding up the numbers on screen lands half a point from the headline figure with no way to
tell rounding from an undisclosed adjustment — in a metric whose only justification is
that it can be taken apart. The docstrings, the docs and the UI tooltip all claimed the
components summed "exactly" to `100 − score`, and all three were wrong.

Fixed by exposing `TrustScore.deducted`, the exact sum, in the payload and as literal
arithmetic on the card:

```
100 − 42.4 deducted = 58
```

**The test that should have caught it had been weakened to fit.** It read
`assert round(deducted) == 100 - score.score`. The `round()` made a passing test out of a
false claim, which is a more useful lesson than the bug: 642 other tests were green at the
time. There are now two assertions, one on the exact sum and one on the derivation of the
displayed score.

**The ceilings are judgements, not measurements, and are labelled as such.** Unlike the
detection thresholds — which [ADR-0006](0006-streak-beats-chance.md) settled with numbers
— there is no ground truth for "how much should one regression cost". They are stated,
bounded, individually visible and individually arguable, which is the best available
substitute for being measured.

**Wasted CI time is an estimate and says so everywhere.** `(flaky failures) × (median
suite duration)`, on the assumption that each flaky failure costs one re-run. The tool
cannot see re-runs outside its own view. It is the most quotable number in the product and
the least defensible, so the assumption travels with it in the tooltip, the JSON payload
and the docs.

**`analysis/health.py` stays pure**, per [ADR-0003](0003-analysis-layer-is-pure.md). It
receives the report, a median duration and a quarantine-days figure; the caller reads the
quarantine file.

## Rejected alternatives

**A fitted or learned index.** Unexaminable, and it would need labelled "how much do you
trust this suite" data that does not exist.

**A fractional score.** Would make the arithmetic exact at the cost of implying precision
the inputs do not support, against the project's own rule on displayed precision.

**Integer-only penalties.** Also exact, but a sub-point penalty would round to zero and a
genuine flake would be reported as a healthy component.

**Largest-remainder redistribution across components.** Exact, and it would shift a
component's penalty away from its own stated formula — breaking the one property that has
to hold.
