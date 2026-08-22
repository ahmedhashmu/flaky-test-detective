# 0015 — Reproduce by experiment, because correlation has a measured ceiling

**Status:** Accepted

## Context

[ADR-0014](0014-search-a-window-for-the-polluter.md) ended in a wall. Scored against 146
order-labelled tests in twelve real repositories, the detector identified all 146 as flaky
and could explain **about one in nine**. Widening the search window did nothing. Relaxing
the correlation threshold did nothing. The instrumented gate table said why: the median
best-candidate share was **0.73**, and no single predecessor explained the failures at any
defensible confidence.

That is not a tuning problem. It is a property of the evidence. Correlational attribution
asks "which test was usually there when this failed", and on a randomly shuffled suite the
answer is often "no one test in particular", either because two tests together cause it,
or because the pairing never recurred often enough to clear a significance test.

Three separate honest options followed from that:

1. Accept 11% and say so. Already done, and it stays published.
2. Lower the bar until the number looks better. Rejected in ADR-0014, measured, no benefit.
3. **Stop inferring and start experimenting.**

Every commercial flaky-test service the market currently offers stops at option 1, with a
better dashboard. Detection is a solved and commoditized problem; a flaky test that has
been *detected* is still a flaky test nobody can fix, because the person assigned to it
cannot make it fail on purpose. Reproduction is where the actual cost lives.

## Decision

Add `flaky reproduce`, which runs the suite against candidate subsets and reduces them by
**delta debugging** until what remains is a locally minimal set of tests that still makes the
victim fail. Output is not a correlation. It is a shell command and a measured failure rate.

```
flaky reproduce test_expects_clean_registry -- pytest
```

### Amendment: judge against the control's bound, not its observed rate

The first version of this compared each batch against the control's *observed* failure
rate. That is the same mistake
[ADR-0012](0012-attribute-flakiness-to-a-branch.md) was written to fix in the branch
comparison, arriving by a different route, and it took a review to notice.

A clean control does not prove a zero rate. **Zero failures in 20 runs still admits a true
rate near 14%**, so comparing against 0.0 meant any single failure was accepted as a
reproduction — and a one-in-twenty flake could be pinned on whatever subset the search
happened to be holding. Worse than a plain false positive, because the printed command
would sometimes appear to work when the reader ran it.

It now compares against the Clopper-Pearson upper bound, via the same
`statistics.upper_bound` the branch comparison uses.

That has a consequence worth stating rather than discovering: because the bar is a rate
rather than "any failure at all", the number of search trials sets a floor on how often a
sequence must fail to be findable. Measured, against a clean control of 0/20:

| Search trials | Failures needed | Which is a rate of |
|---|---:|---:|
| 3 | 3 | **100%** |
| 4 | 3 | 75% |
| 5 | 3 | 60% |
| **6** | **3** | **50%** |
| 8 | 4 | 50% |

At the original default of three, the search could only ever clear on a sequence that
failed *every single time* — so the tool would have found deterministic order dependence
and nothing else, while appearing to search for the general case. `DEFAULT_SEARCH_TRIALS`
is now 6, the cheapest point where a merely-frequent dependence is findable.

The cost of both changes together, on the same demo case: **45 suite runs to 88**. The
answer is unchanged (15 candidates to 1, the true polluter) and now it is defensible.

### Measure the control first, always

The victim runs alone before anything else is tried, and the number of times it fails alone
becomes the baseline every later observation must beat.

Without this the whole idea collapses in the most embarrassing way possible. A test that
fails on its own one time in three will "reproduce" under whatever prefix the search
happens to be holding when it flips, so a search with no control would confidently print a
command naming an innocent test, and the command would even appear to work when the reader
ran it. That is worse than saying nothing: it is a wrong answer wearing the costume of
proof.

With a clean control any failure counts, correctly — a test that never fails alone and just
failed after a prefix has been reproduced. With a non-zero control the observation has to
clear an exact binomial tail (`statistics.tail_at_least`, the same function the branch
comparison and fix verification use) before it counts.

Where the control is high and no subset beats it, the answer is `fails_alone`, which is a
real finding: the cause is inside the test, repetition is the reproducer, and there is no
ordering to hunt for.

### Delta debugging, not a linear scan

Trying candidates one at a time cannot find a **conjunction** — two tests that only break
the victim together — and there is nothing exotic about that case: an object cached by one
test and mutated by another needs both. A linear scan reports nothing at all there, which
looks identical to no bug existing.

`ddmin` (Zeller and Hildebrandt) splits the candidate set, tests chunks and complements,
and increases granularity when neither helps. It finds conjunctions, and it costs
logarithmically rather than linearly.

What it guarantees is **local** minimality, and the docstring says so rather than implying
more: removing any single chunk at the granularity reached no longer reproduces. Proving
global minimality is exponential. In practice the reduction observed is from tens of tests
to one or two, which is the entire difference between a lead and an answer.

### Two trial budgets, not one

Delta debugging makes O(n log n) oracle calls. At twenty trials each, minimizing forty
candidates would run a suite several thousand times and no one would wait for it.

So the search uses `search_trials` (default 3) per experiment, and then **re-measures the
final sequence at full `trials`** (default 20). The published rate always comes from the
confirmation, never from the search. A reduction that passed on three trials and does not
hold up over twenty is reported as `not_reproduced` with the sequence still shown — the
explanation says the reduction was luck, because printing it as a reproducer would send
someone chasing a command that does not work.

### The search logic never touches a subprocess

`ddmin` takes an oracle callable. `reproduce` takes a `runner(sequence, trials)` callable.
The real one is built by `_subprocess_runner`, which executes the suite and reads **JUnit
XML** — the same parser ingestion uses — to see what the victim did.

This is the only affordable way to test any of it. Fifty-five tests pin the search's
behaviour against known-answer fakes in 0.1 seconds, including the conjunction case, the
lucky-reduction case and the fails-alone case. A test suite that drove the real thing would
take hours and would still only have observed one project.

### Order randomization is force-disabled

The output is an ordered sequence. `pytest-randomly` installs itself active-by-default, so
without `-p no:randomly` on both the trial runs and the printed command, the tool would
shuffle the very arrangement it is measuring and the command it printed would reproduce
nothing. Not a preference; a correctness requirement.

### Test selection arguments are stripped, visibly

`pytest tests/ tests/test_a.py::test_b` collects all of `tests/` **and** that node id, so
the carefully constructed sequence would be buried inside a full suite run in collection
order. Selection arguments are therefore removed, and which ones were removed is printed in
the explanation. Silently dropping a `-k` expression would change what the printed command
means without telling anyone.

## Measured outcome

Against `examples/flaky_demo`, whose order dependence is real module-level state and not a
simulated coin flip, with history from a 20-iteration shuffled hunt:

| Test | Outcome | Result | Suite runs |
|---|---|---|---:|
| `test_expects_clean_registry` | reproduced | 15 candidates → **1**, the true polluter | 45 |
| `test_counts_registered_sessions` | reproduced | 15 candidates → **1**, same polluter | 44 |
| `test_worker_finishes_within_deadline` | fails_alone | 8/12 alone, no prefix blamed | 15 |
| `test_stable_sums_prices` | not_reproduced | 0/8 alone, 0/8 with all 15 | 11 |

The reproduced command was then run independently, outside the tool:

```
$ pytest -p no:randomly \
    examples/flaky_demo/test_shared_state.py::test_registers_session \
    examples/flaky_demo/test_shared_state.py::test_expects_clean_registry
1 failed, 1 passed          # three times out of three

$ pytest -p no:randomly \
    examples/flaky_demo/test_shared_state.py::test_expects_clean_registry
1 passed                    # three times out of three
```

20/20 in that order, 0/20 alone. Eight experiments to get there from fifteen candidates.

Two properties of that table matter as much as the successes. The negative answers were
**cheap** — 11 and 15 suite runs against 45 for a positive — because the search checks all
candidates together once and stops when that does nothing. And the timing flake was **not
blamed on anything**, which is the control doing its job.

### What has not been measured

Reproduction has **not** been evaluated against the twelve real repositories from
ADR-0011. Doing it honestly means checking out each project, installing its dependencies,
and running its suite hundreds of times per candidate test; the recorded JUnit XML that the
rest of the validation harness replays is not enough, because reproduction has to execute
code. So the 11% correlational diagnosis rate stays the published real-world figure, and no
claim is made here that reproduction beats it on real code. It is measured on a suite whose
ground truth is known, and its limits are stated below rather than left for a reader to
discover.

## Consequences

**pytest only, said plainly.** Reproduction needs a runner that accepts an ordered explicit
list of tests and honours that order. pytest does. Other runners raise a usage error naming
the limitation and pointing at `flaky investigate`, which reports correlational evidence
without executing anything. Advertising support before measuring it is the failure mode this
project spent ADR-0014 documenting.

**It costs real time.** The estimate is printed before the search starts, and the number of
suite executions is reported afterwards, because the honest answer to "should I run this" is
often no and a user cannot make that call without the number.

**The candidate list is ranked, then reordered.** Candidates are *selected* by suspicion —
how often each preceded an actual failure, with the detector's named polluter first — but the
search *runs* them in execution order. Conflating the two orderings was the first thing that
went wrong while building it: shuffling the selected set into rank order measures an
arrangement the history never observed.

**A missing victim is a usage error, never a pass.** If the node id no longer resolves, every
trial reports the victim as absent. Counting absence as a pass would return a confident
"stable" for a test that was renamed, so it raises instead.

**`flaky verify` is the natural next step** and the console output says so. Reproduce to get
a failing command, fix the shared state, then verify that the fix holds against the three
conditions in [ADR-0013](0013-verify-fixes-against-three-conditions.md).

## Rejected alternatives

**Bisecting the ordering instead of delta debugging.** Halving a prefix cannot find a
conjunction whose members land in different halves, and would report nothing on exactly the
cases correlation already fails.

**Reusing the recorded history as the oracle** — searching for a past run whose prefix
matches a candidate subset — which would need no execution at all. Rejected because the
histories do not contain those runs: the reason correlation stalled is that specific
pairings recur too rarely under random shuffling. Mining absent data cannot fix a data
shortage.

**Reading test source to find the shared state directly.** Would break the
language-agnostic property that makes this tool work for pytest, jest, go and .NET without
knowing anything about any of them.

**One trial budget for search and answer.** Either unaffordable at twenty, or publishing
three-trial rates as though they were measurements.

**Running the victim first to establish the control lazily, only when needed.** The control
is what makes every later number mean anything; making it conditional is how it eventually
gets skipped.
