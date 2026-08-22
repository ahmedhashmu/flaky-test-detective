# Investigation engine — design

Every decision here has an ADR. This document is the shape; the ADRs carry the arguments and
the measurements, including the ones that went the wrong way.

## Where the new code sits

The dependency direction from `structure.md` does not change: `cli → report → analysis →
storage → models`. Two additions and one new role.

```
analysis/
  statistics.py     NEW  binomial reasoning, in one place
  comparison.py     NEW  what a branch introduced
  verification.py   NEW  whether a fix can be believed
  correlation.py    NEW  where a test fails
  ordering.py       CHANGED  windowed polluter search
reproduce.py        NEW  producer: delta debugging to a minimal failing sequence
demo.py             NEW  producer: a populated database in one command
benchmark/
  realworld.py      NEW  scoring against published labels
report/
  comparison.py verification.py reproduction.py validation.py   NEW
```

`reproduce.py` is the one producer that runs the suite **to answer a question** rather than to
collect history. That difference drives its whole shape (below).

### One place for "beat chance"

Four questions in the tool turned out to be the same question with different signs: did this
branch cause the increase, is this fix real, is this streak more than the test's own
behaviour, does this candidate explain these failures. They had been three separate ad-hoc
calculations — exactly the drift the steering file warns about for score weights.

`analysis/statistics.py` now owns the exact binomial machinery and the callers supply the
sign. Five callers as of this round. Standard library only: `math.lgamma` in log space, plus a
fixed-step bisection so results are bit-identical run to run.

That consolidation is also what made [FR8](requirements.md#fr8--assert-relationships-not-only-examples)
worth doing: `P(X ≥ k) + P(X ≤ k−1) = 1` is two independent summations over the same
distribution, and a property test can check they agree.

## The reproducer

The originality piece, and the answer to Finding 2.

**Split the search from the execution.** `ddmin(candidates, oracle)` is Zeller and
Hildebrandt's delta-debugging minimization: pure, deterministic, takes a callable. It knows
nothing about tests or subprocesses. `reproduce(...)` takes `runner(sequence, trials)`, also
injected. The real one executes the suite and reads JUnit XML — the same parser ingestion
uses.

This is not architectural taste. Delta debugging makes O(n log n) oracle calls and each real
call costs a suite run, so a test suite that exercised the search for real would take hours
and would still have observed one project. With an injected oracle, 55 known-answer tests pin
the behaviour in a tenth of a second, including the conjunction case and the
lucky-reduction case.

**Measure a control, always.** The victim runs alone first, and that rate is the baseline
every later observation must beat. Without it the idea collapses in the most embarrassing way
available: a test that fails one time in three "reproduces" under whatever prefix the search
happens to be holding, so an innocent test gets named and the printed command even appears to
work when the reader runs it. A wrong answer wearing the costume of proof.

**Two trial budgets.** Search at 3 trials per experiment, then re-measure the final sequence
at 20. The published rate always comes from the confirmation. A reduction that passed on three
trials and fails over twenty is reported as not reproduced, with the sequence still shown and
the explanation saying the reduction was luck.

**Rank by suspicion, run in execution order.** Candidates are *selected* by how often each
preceded an actual failure, with the detector's named polluter first. The search *runs* them
in execution order. Conflating those two orderings was the first thing that went wrong while
building it: running the selected set in rank order measures an arrangement the history never
observed.

**Force order preservation.** `-p no:randomly` on the trials and in the printed command.
pytest-randomly is active by default and would shuffle the very sequence being measured.

[ADR-0015](../../../docs/adr/0015-reproduce-by-experiment-not-correlation.md)

## The windowed polluter search, and its negative result

Widen from the adjacent test to a window of six, with a Bonferroni correction over the
candidates that have enough observations to be testable. Replace the significance statistic:
`base_rate ** fail_count` computed *P(all failed)*, which is only valid at a correlation share
of 1.0.

The generator had to change first, because a fixture whose polluters are all adjacent cannot
measure a change to how far back the search looks. Distances now cycle `(1, 2, 3, 5, 8)`, with
real spacer tests executing in the gap, and 8 is deliberately beyond the default reach — a
benchmark whose hardest case sits inside the implementation's reach cannot report a limit.

Result on generated data: naming rate up 3.5×, precision 1.000 at every window, zero innocent
tests ever blamed. Result on real data: **no effect**. The numbers drift around 5% with no
trend, and the median distance of the polluters it does find is 1.0 even at window 12.

Kept anyway, and published as a negative result. The gate instrumentation says why the real
ceiling is elsewhere: the median best-candidate share is 0.73, so no single predecessor
explains the failures at any defensible confidence. That is what motivated the reproducer.

[ADR-0014](../../../docs/adr/0014-search-a-window-for-the-polluter.md)

## Environment correlation

Generic `run_labels(run_id, key, value)` rather than named columns for OS and architecture.
Projects differ in what explains their flakiness, and the interesting dimension is often one
nobody would put in a schema: a shard index, a parallelism setting, one bad runner image.

Confounding is reported rather than hidden. When every ARM runner has two CPUs, `arch=arm64`
and `cpus=2` describe the same set of runs and the data cannot separate them. Reporting both
as findings would invent a second cause; reporting only the stronger would hide a real
alternative. `covaries_with` names them as indistinguishable, computed from the actual run
sets rather than from matching counts.

Schema version 2. Migrations are additive and the schema is replayed, so a v1 database gains
the table and keeps every run.

## Fix verification

Three conditions, not one. The streak must beat the old rate; the failing condition must have
been exercised; nothing else may have broken. `count_exposures` uses the same windowed index
detection uses — at distance 1 it would report zero exposures for a polluter found four tests
back, and every such fix would be permanently unverifiable for a reason that is an artefact of
the mismatch.

The conservative choice throughout: the old rate is assumed as *low* as its data allows, which
makes a clean streak less surprising and raises the bar for declaring a fix real.

[ADR-0013](../../../docs/adr/0013-verify-fixes-against-three-conditions.md)

## Branch comparison

Judge the head against the baseline's **Clopper-Pearson upper bound**, not its observed rate.
No failures in 8 runs still admits a true rate near 30%, so a head failing 5 of 20 proves
nothing against it; against 60 baseline runs the same observation is conclusive. Only
introduced flakiness and introduced breakage block, because blocking people for pre-existing
flakes is how CI gates get switched off.

[ADR-0012](../../../docs/adr/0012-attribute-flakiness-to-a-branch.md)

## Real-world validation

Build each project at its recorded SHA and run it. Recall is measured against labels that
reproduced in our runs; precision against observed same-commit divergence, which is a
denominator that cannot be argued with or gamed. Labels that never varied here are reported in
their own row rather than counted as misses — counting them would be measuring how dependency
resolution has moved since the label was written.

Raw results are committed so the score recomputes without re-running anything, and CI asserts
the published figures still match.

[ADR-0011](../../../docs/adr/0011-validate-against-real-repositories.md)

## Property-based invariants

Assert relationships over generated histories, with the generator's own reach asserted so a
property cannot pass while never reaching the interesting state.

Two properties that look obvious are **false** and were not written: shuffling all outcomes
(flip counting is a walk over a chronological sequence, which `analyze_test` documents), and
same-commit divergence implying a flaky verdict (the ladder puts broken, regression and fixed
first, so a diverging test ending in a long clean streak is `fixed`).

[ADR-0016](../../../docs/adr/0016-assert-relationships-not-only-examples.md)

## Cross-platform support

The decision that shaped this: **a green Windows CI run would not have been evidence.** pytest
replaces `sys.stdout` with a UTF-8 buffer, so the failure that actually bites — a redirected
console on the locale codepage meeting the block characters in the verification bars — cannot
appear from inside the suite.

So two mechanisms. `tests/test_portability.py` reconstructs each platform condition explicitly
and runs everywhere, and CI additionally runs the installed console script with output
redirected and `PYTHONIOENCODING=cp1252` on all three platforms.

The audit found four defects before the matrix ran; the matrix then found two the audit could
not, including a real one: `HTTPServer` sets `allow_reuse_address = 1`, and on Windows
SO_REUSEADDR permits binding a port another socket is actively listening on, so `flaky serve`
silently started a second server on an occupied port.

[ADR-0017](../../../docs/adr/0017-windows-is-a-supported-platform.md)

## What a judge sees in three minutes

Each deep piece needs one visible moment or it may as well not exist:

| Moment | Command |
|---|---|
| It works, with data | `flaky demo` |
| It knows its own accuracy | `flaky benchmark` |
| It was checked against labels we did not write | `flaky validate validation/results` |
| It makes a flake fail on demand | `flaky reproduce <test> -- pytest` |
| It refuses to certify a fix it cannot prove | `flaky verify <test>` |
| It blames the branch, not the person | `flaky compare` |
