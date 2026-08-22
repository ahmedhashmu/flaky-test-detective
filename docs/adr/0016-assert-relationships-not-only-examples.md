# 0016 — Assert relationships, not only examples

**Status:** Accepted

## Context

This suite had 897 example-based tests before this record and they are the right tool for
pinning a decision: *this history produces that verdict* is exactly what a regression test
should say. They share one structural weakness, and this project has already paid for it.

Every example test was written by someone who already had a theory about what mattered.
[ADR-0014](0014-search-a-window-for-the-polluter.md) records three fixture bugs that each
survived a full example suite and each produced *plausible* numbers — polluter accuracy of
2/8, then 0/8, then a reported precision of 0.875 while every attribution was in fact
correct. None of them was caught by a hand-written case, because the fixture and the
detector were built from the same belief. A test that encodes the author's assumption
cannot falsify it.

The README also makes claims in the form of relationships rather than examples. "It refuses
to cry wolf" is not a statement about one history; it is a statement about *all* of them.
Those claims deserve to be checked the way they are phrased.

## Decision

Add Hypothesis as a **development dependency** and assert relationships over generated
histories, in `tests/test_properties.py`. The two runtime dependencies stay at two; nothing
in the shipped package imports Hypothesis.

Forty properties in six groups.

**Analysis contracts.** A score is probability-shaped. A test that never failed is stable.
**A test that never passed is never flaky** — the product's central promise, now searched
for a counterexample instead of demonstrated on one history. Flaky requires both a pass and
a failure. Every test seen appears exactly once. The ranking obeys its stated tiebreaker.

**Order independence, exactly where it is claimed.** This group is the one that had to be
written carefully, and the care is the interesting part — see below.

**Merge is a set union.** Idempotent, commutative, and the union of run ids, checked through
real SQLite files rather than around them. That is the claim `merge_from`'s docstring makes,
and it is what makes sharded CI and pooled local hunts safe.

**Formatting cannot compute.** The steering rule that `report/` must not derive numbers,
as an executable check: the JSON verdicts must equal the verdicts analysis reached, for any
history. Plus a monotonicity property — lowering the flake threshold can only *add* flaky
labels, never remove one, because the verdict ladder puts broken, regression and fixed ahead
of flaky and a threshold must not be able to disturb those.

**Numerical identities.** `statistics.py` now has five callers. `P(X ≥ k) + P(X ≤ k−1) = 1`
is two independent summations over the same distribution, and the two lived as separate
ad-hoc calculations in three modules before that file existed. Clopper–Pearson must bracket
the observed rate. `trials_needed(rate)` must actually satisfy the inequality it is quoted
for: `(1 − rate) ** n ≤ 0.05`.

**Delta debugging invariants.** `ddmin` from [ADR-0015](0015-reproduce-by-experiment-not-correlation.md)
is where a subtle bug would cost the most, because a wrong reduction still prints a command
and the command still fails — so the mistake reaches a user looking exactly like an answer.
The result must be an order-preserving subset, must still satisfy the oracle, must never
exceed its budget, and must be identical across two runs.

### The generator's own reach is asserted

Property-based testing invites a specific self-deception: narrow the strategy until
everything is green, and end up asserting confidently about histories no runner could
produce. "A test that never passed is never flaky" is worthless if no generated history ever
contains a test that never passed.

So `TestTheGeneratorIsNotVacuous` runs the strategy and fails if any guarded state is
missing. Measured over 400 examples:

| State reached | Count |
|---|---:|
| flaky | 600 |
| broken | 247 |
| regression | 45 |
| stable | 160 |
| never passed, with runs recorded | 247 |
| same-commit divergence observed | 514 |
| runner-recorded retries | 433 |

Order-dependence evidence is reached **zero** times, and that is correct rather than a gap:
the generator assigns statuses independently, and a candidate needs a ≥0.9 correlation share
to be named a polluter, which will not arise by chance. Nothing in this file asserts anything
about order evidence, and `benchmark/generate.py` is the fixture that constructs it
deliberately.

## What the exercise actually found

**No defect in the analysis layer.** Forty properties, run across generated histories, and
the shipped code satisfied all of them once they were stated correctly. That is the result,
and inventing a more dramatic one would be the opposite of the point.

What it did produce is two corrections to what the code was *believed* to guarantee, both
found while trying to state a property precisely enough to be true.

**Insertion-order independence is narrower than it looks.** The obvious property — shuffle
the outcomes, get the same answer — is **false**, and correctly so: `analyze_test` documents
that it requires chronological order, because flip counting is a walk over a sequence.
Asserting the shuffle-invariant would have been asserting against a stated precondition. Two
narrower properties are true and are the ones that matter operationally: the order in which
*different tests* interleave cannot matter, and the order in which *runs are ingested* cannot
matter.

**Ingest-order independence needs distinct timestamps, and there is a measurable case where
it fails.** Storage reads `ORDER BY started_at ASC, u.id ASC`, so runs sharing a timestamp
fall back to insertion order. Twelve runs of `F F P P P P P P P P P P`, inserted forward and
then reversed:

| Timestamps | Forward | Reversed |
|---|---|---|
| identical | `fixed`, 10 trailing passes | `flaky`, 0 trailing passes |
| distinct | `fixed`, 10 trailing passes | `fixed`, 10 trailing passes |

The generator therefore produces distinct increasing timestamps, and its docstring says why.
That is a precondition, not a workaround: a generator producing ties would be manufacturing
a counterexample to a property nobody holds.

The fallback itself is left as it is, deliberately. For a single machine — a hunt, a CI job —
insertion order *is* chronological order, so the tiebreaker is right. Across merged
databases from different machines, chronology between two runs recorded in the same instant
is not knowable from the data, and any tiebreaker would be arbitrary. pytest and the mtime
fallback both record sub-second precision, so ties are rare in practice. Recorded here rather
than fixed, because "fixing" it would mean inventing an ordering the artifacts do not carry.

## Consequences

**Hypothesis is dev-only, and the architecture test enforces the dev-dependency list.**
`pyproject.toml` keeps one list of pins in the `dev` extra, with the PEP 735 group
referencing it, so `uv sync` and `pip install -e ".[dev]"` cannot drift.

**`.hypothesis/` is gitignored** and added to `norecursedirs`. The example database is a
record of counterexamples *this machine* has found; committing it would make one developer's
search history part of everyone else's test run.

**A profile caps examples at 60 and disables the deadline.** The storage properties build
real SQLite files, which is slower than Hypothesis' default per-example budget expects, and
a timing-based failure in this project's own suite would be a particularly poor joke.

**Properties are not a substitute for the example tests.** They check that relationships
hold; they cannot check that a specific history produces the specific verdict a human
decided it should. Both stay.

## Rejected alternatives

**Shuffling all outcomes as the ordering property.** False by the analysis layer's stated
contract, and asserting it would have forced either a wrong test or a pointless sort inside
`analyze_test` that hid the real requirement.

**Generating arbitrary strings for test ids and commit SHAs.** Tried, and it made every
history a population of unique tests with one run each, which reaches no verdict worth
guarding. Small sampled pools are what create the repeated observations flakiness is defined
over.

**Letting the generator produce identical timestamps** and adding a sort inside `analyze` to
compensate. It would make the property pass by moving the tiebreaker somewhere less visible,
and storage is where run order belongs.

**Asserting that same-commit divergence implies a flaky verdict.** It does not, and the
reason is deliberate: the verdict ladder checks never-passed, regression and fixed *first*,
so a diverging test whose history ends in a long clean streak is `fixed`. Writing that
property would have meant either a false assertion or reordering the ladder to satisfy a
test.
