# 0013 — A fix is verified against three conditions, not one

**Status:** Accepted

## Context

The tool detected flakiness, diagnosed it, attributed it to a commit and to a branch. It
had nothing to say about the step that actually removes flakiness from a suite: deciding
whether a change worked.

In practice that decision gets made by running the test a few times and seeing green.
That is wrong in a specific and expensive way. Declaring a flake fixed puts it back into
the trusted set, so the *next* time it fails, the failure reads as new breakage — and the
tool has then manufactured exactly the confusion it exists to remove. A wrong "fixed" is
worse than no answer.

## Decision

`flaky verify <test>` reports one of three outcomes, and a clean streak alone earns none
of them.

**`fixed`** requires all three of the following. **`inconclusive`** is what any single
failure produces, and it is a first-class answer rather than a soft no.

### 1. The streak must beat the old failure rate

A test that failed 35% of the time needs 8 clean runs to clear a 5% bar. One that failed
2% of the time needs **149**. That asymmetry is counter-intuitive and it is exactly
backwards from how people behave: the rare flake is the one declared fixed after three
green runs.

`statistics.trials_needed` turns it into a number the tool states up front, so "run it
more" arrives with a count attached.

The old rate is taken at its **lower** confidence bound. That is the conservative
direction for claiming an improvement: a lower assumed old rate makes a clean streak less
surprising, so it raises the bar. Using the observed rate would let a handful of green
runs certify a fix.

Note the sign is the opposite of [ADR-0012](0012-attribute-flakiness-to-a-branch.md),
which takes the *upper* bound on the baseline before blaming a branch. Both choices are
conservative about the claim being made, which is why they point in different directions.

### 2. The failing conditions must have been exercised

This is the check that makes the other two worth anything.

If a test only failed when it ran after `test_registers_session`, and the polluter
happened to precede it twice in fifty runs, then fifty green runs prove close to nothing:
the situation that used to fail was barely attempted. Reporting that as a fix would be
the worst kind of false negative, because it arrives with a confident number attached.

So when the earlier diagnosis named a polluter, verification counts how often that
polluter actually ran ahead of the test in the new runs, and refuses to conclude below
`MIN_EXPOSURES`. The floor is the same one `ordering.py` needs to detect a polluter in the
first place, deliberately: the tool must not detect on evidence it then declines to
verify against.

### 3. Nothing else may have broken

A fix that makes one test stable by leaking state into another has moved the problem, not
removed it. Verification runs the whole-suite comparison from ADR-0012 across the same
before/after split and treats anything newly introduced as disqualifying.

Reusing `compare()` rather than writing a second rule was the point: a fix that introduces
a flake and a branch that introduces a flake are the same event seen from two angles, and
two implementations would eventually disagree about it.

## Consequences

**The binomial reasoning moved to `analysis/statistics.py`.** Comparison and verification
ask the same question with the sign reversed, and `flakiness.py` and `ordering.py` already
had their own ad-hoc versions. Four copies of "beat chance" is the drift the structure
steering warns about for score weights. One module now owns it and the callers supply the
sign.

**That refactor immediately surfaced an overflow.** The original implementation multiplied
`math.comb(n, k)` by a float. At 2000 trials `comb` is a 600-digit integer and the
multiplication raises `OverflowError` — found by a test at exactly that size, not by
inspection. The mass function is now computed via `lgamma` in log space, which is stable
at any size a caller can reach, and the large-input threshold went from a correctness
requirement to a speed guard.

**Two bugs in the before/after split, both found by running it rather than reading it.**

The cutoff was compared as an ISO *string* against recorded timestamps. Recorded times
carry whatever offset the machine had, so `2026-08-22T15:47+05:00` sorts after
`2026-08-22T10:53+00:00` as text while being two hours earlier in fact. Every run landed
on the wrong side of the boundary and the command reported "no runs recorded before" its
own cutoff. Comparison is now on parsed instants.

Hunt progress was written to stdout while `--format json` also wrote there, so the JSON
was interleaved with iteration lines and no parser could read it. Progress now goes to
stderr throughout.

**A tiny probability is stated as a bound, not a percentage.** `f"{8.3e-05:.1%}"` is
"0.0%", which reads as a rounding artefact rather than as the strongest evidence the tool
can offer. Values below a tenth of a percent render as "under 0.1%".

**`--after-commit` is the natural entry point.** "Is it fixed since I landed a3f2c91"
splits the history at that commit's first recorded run, which is how the question is
actually asked. `--since` and running the suite inline are the other two ways in.

**The impact figure is a counterfactual over runs that happened**, not a projection: at
the old rate these runs would have produced about N failures and they produced fewer. It
is still an estimate, because a rate measured in one window need not hold in the next, and
it is labelled as one everywhere it appears — the same rule the wasted-CI-time figure
follows ([ADR-0009](0009-explainable-trust-score.md)).

## Rejected alternatives

**A pass/fail answer.** Collapsing "not yet provable" into "not fixed" makes the tool
pessimistic about real fixes; collapsing it into "fixed" makes it credulous. The
three-value outcome is the honest shape, and `inconclusive` is the most common one.

**A fixed number of clean runs, such as 20.** Wrong in both directions at once: far more
than a 40%-failure flake needs, nowhere near enough for a 2% one.

**Verifying against the observed old rate instead of its lower bound.** Easier to clear
and it certifies fixes that are luck.

**Ignoring the polluter check when order dependence was diagnosed.** It is the difference
between a verified fix and a lucky shuffle, and skipping it would make every
order-dependence fix unverifiable in practice while looking verified.
