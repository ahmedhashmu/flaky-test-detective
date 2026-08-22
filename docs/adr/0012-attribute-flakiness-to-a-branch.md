# 0012 — Attribute flakiness to a branch, against the baseline's own uncertainty

**Status:** Accepted

## Context

`flaky triage` answers the question someone on build duty has: this run is red, do I
investigate or re-run? It answers it well, and it is the wrong question for a pull
request.

On a pull request the question is not "is this test flaky" but "**did I make it
flaky**". Those have different answers and different consequences. A gate that blocks a
merge because a test was already flaky punishes whoever touched the repository last, and
a gate people resent is a gate people route around — usually by deleting it. A gate that
waves through newly introduced flakiness is how a suite arrives at "always a bit red",
which is the state this whole tool exists to prevent.

So flakiness has to be attributable to a change, and the attribution has to be right
often enough to be trusted.

## Decision

`flaky compare` takes two analysed histories and reports what the newer one
**introduced**, in seven categories, of which exactly two block a merge:

| Change | Blocks | Meaning |
|---|---|---|
| `new_break` | yes | Passed on the baseline, consistently fails here |
| `new_flake` | yes | Stable on the baseline, flaky here |
| `worse` | no | Flaky on both sides, measurably more so here |
| `known_flake` | no | Flaky on both sides. Pre-existing, not this change's debt |
| `improved` | no | Flaky on the baseline, clean here |
| `unchanged` | no | Nothing worth reporting |
| `unproven` | no | Something moved; the evidence does not support naming it |

### The naive version is wrong, and wrong in the expensive direction

Compare failure rates: 0/40 on `main`, 11/40 here, therefore the change made it worse.

That reasoning fires on luck. **Zero failures in 40 runs does not mean the failure rate
is zero; it means it is low.** A test that genuinely fails 6% of the time will show a
clean 40-run baseline about one time in ten. Ship a gate on rate comparison and it will
eventually tell an engineer their one-line change broke a test they never touched — and
that is the false alarm this project is organised against, arriving in the most public
place it could.

### So the baseline gets the benefit of the doubt, twice

1. Take the **upper confidence bound** on the baseline's failure rate — the
   Clopper-Pearson limit at 95%, found by bisection. Zero failures in 40 runs is
   consistent with a true rate up to about 7%, so 7% is the bar, not 0%. For zero
   failures this reduces to a closed form and reproduces the familiar rule of three.
2. Ask how probable this many failures would be **at that bound**. Only if that is
   below 5% is the change named.

A flake therefore has to clear a bar the baseline's own uncertainty already sets. The
asymmetry is deliberate: missing an introduced flake costs a slightly worse suite, and
inventing one costs the gate's credibility.

Same "beat chance, not just the observed number" reasoning as the streak rule in
`flakiness.py` ([ADR-0006](0006-streak-beats-chance.md)) and the polluter rule in
`ordering.py` ([ADR-0004](0004-order-dependence-needs-a-polluter.md)). An exact binomial
rather than those rules' simple power, because here the baseline count is not always
zero. Implemented with `math.comb` — adding scipy to read test reports is not a trade
this project makes.

### Verified rather than asserted

The remedy the tool recommends when it cannot attribute something is "record more
baseline history". That recommendation had to be checked, not just written:

| Baseline runs | Bound on old rate | Head 5/20 | Verdict |
|---|---:|---:|---|
| 20 | 13.9% | p = 0.135 | `unproven` |
| 60 | 4.9% | p = 0.002 | `new_flake`, high confidence |

Measured on the demo suite with the head runs held identical and only the baseline
grown. The statistic moves the way the documentation says it does.

### Confidence is a band over two facts, not a third number

`high` requires **both** a probability under 1% and *proof* in the new runs —
same-commit divergence, or a runner-recorded retry. One of the two gives `moderate`.
Neither gives `weak`.

Keeping proof separate from probability matters: a low p-value is a statistical
argument, and same-commit divergence is a demonstration that the code was identical
between a pass and a fail. Reporting them as one number would let the weaker one borrow
the authority of the stronger, which is the same mistake the dashboard's evidence panel
exists to avoid ([ADR-0001](0001-same-commit-divergence.md)).

## Consequences

**`worse` reports but does not block.** A pre-existing flake getting worse is often a
coincidence of sampling, and blocking on it would make the gate unpredictable. Available
via `--fail-on any` for teams that want it.

**"Cannot tell" is its own category.** Folding `unproven` into `unchanged` would let the
gate guess silently. Two situations land there and they get different sentences: a test
that is *demonstrably* flaky now but whose baseline is too thin to rule out that it
always was, and a test that simply failed a bit more often. The first is worth someone's
attention; the second is noise. One shared hedge would have hidden the difference.

**A test failing identically on both sides is `unchanged`, not `unproven`.** Found by
running the comparison on real data: `test_known_broken` fails every run on both sides
and was landing in a list of things needing investigation. "Nothing changed" and "we
cannot tell" are different answers.

**The direction is checked.** Comparing `main` against the flaky branch reports 9
introduced flakes; comparing the flaky branch against `main` reports 0 introduced and 10
improved. A comparison that is not antisymmetric is measuring something other than what
changed.

**The action gained a `compare-against` input** and three outputs. When set, what the
branch introduced decides the merge in *both* directions: it blocks a merge that added a
flake triage would have shrugged at, and it clears a merge whose only red tests were
already red. The second half is what stops teams switching the gate off.

**It needs history on both sides, and says so plainly.** One CI run cannot establish that
a test is flaky. Early pull requests report "cannot attribute" until enough runs exist,
which is the honest answer rather than a limitation to paper over. A missing baseline is a
`::notice::`, not a failure — failing someone's pull request over the tool's own setup
would be indefensible.

## Rejected alternatives

**Comparing against the observed baseline rate.** Fires on luck. Discussed above.

**A single significance test with no proof requirement.** Would call a test introduced
on statistics alone, discarding the difference between an inference and a demonstration.

**Blocking on `worse`.** Makes the gate unpredictable for a signal that is frequently
sampling noise.

**Requiring the same commit on both sides.** Tempting, since same-commit divergence is
the tool's strongest signal, but a branch and its base are different commits by
definition. The comparison is between *populations of runs*, and each side's internal
divergence is still what establishes flakiness within it.

**Inferring the base branch from git.** The action takes it as an input instead. Guessing
which branch is the baseline from a detached-HEAD CI checkout is a source of silent wrong
answers, and the workflow already knows.
