# Design: Accuracy and Adoption

## 1. Accuracy harness

### The idea

Generate test histories where the correct answer is known by construction, run the
real analysis over them, and compare.

This is only meaningful if the generator is honest. It would be trivial to generate
data that the current scorer happens to classify perfectly — alternate pass and
fail, call it flaky, declare 100% accuracy, learn nothing. The generator therefore
has to produce the cases that are genuinely hard, and the benchmark has to report
the failures.

### Ground-truth labels

Each generated test carries a `GroundTruth` describing what it actually is:

| Label | How it is generated | Expected verdict |
|---|---|---|
| `flaky` | Fails with probability *p*, independently per run | `flaky` |
| `stable` | Always passes | `stable` |
| `broken` | Always fails | `broken` |
| `regression` | Passes until commit *k*, fails after | `regression` |
| `fixed` | Flaky early, then a clean streak | `fixed` |
| `order_dependent` | Fails iff a named polluter ran earlier | `flaky`, cause `order_dependence` |

The hard cases are deliberately included:

- **Low-rate flakes** (*p* = 0.05). Genuinely flaky, but may not fail at all in a
  short window. When they do not fail, no detector can find them, and the recall
  number should show that rather than hiding it.
- **High-rate flakes** (*p* = 0.9). Likely to fail every run in a short window,
  which makes them indistinguishable from `broken`. This is the case that caught out
  the demo suite twice during the first spec.
- **Regressions with flaky history.** The case that produced a real bug in the first
  round.
- **Sparse commit coverage.** Runs with no commit SHA, where only the weaker signal
  is available.

### Why per-label precision and recall, and not just accuracy

Accuracy is useless here because the classes are unbalanced: a suite where 90% of
tests are stable can score 90% accuracy by calling everything stable. Precision and
recall per label expose that immediately.

### The metric that matters most

Beyond the standard table, one number gets called out separately:

```
false_alarm_rate = (regression or broken tests reported as flaky)
                   ÷ (regression or broken tests)
```

The product steering names this the worst failure mode the tool has, because it
teaches the user to re-run instead of investigate. Measuring it separately means it
cannot be averaged away into a good-looking aggregate.

The mirror image is tracked too:

```
missed_break_rate = (flaky tests reported as regression or broken)
                    ÷ (flaky tests)
```

### Structure

```
benchmark/
├── __init__.py      run_benchmark(), the entry point
├── generate.py      ground-truth population generation
└── score.py         precision, recall, F1, confusion matrix
```

`generate.py` produces `TestOutcome` lists, exactly what `analysis.analyze` already
consumes, so the harness exercises the real pipeline rather than a parallel one.
Nothing here touches storage, which keeps the existing architecture rule intact.

### Determinism

A single `random.Random(seed)` instance threads through generation. No module-level
`random` calls, so a reported figure is reproducible from its seed. This matters:
an accuracy claim that cannot be re-derived is an anecdote.

## 2. History merging

### Why this is sound rather than a hack

`run_uid = sha256(report content + resolved source path + iteration)`.

Two consequences:

1. The same report ingested twice produces the same `run_uid`, so a duplicate is
   detectable without comparing contents.
2. Two databases built on different machines from different reports contain
   disjoint `run_uid` sets, except where they genuinely ingested the same artifact —
   in which case the duplicate *should* collapse.

So merging is: copy every run whose `run_uid` is not already present, along with its
results. Set union. Idempotent and order-independent by construction, which is
exactly what FR2.2 demands.

### Implementation

```
flaky merge <source…> [--into <target>]
```

Attach each source with SQLite's `ATTACH DATABASE`, then insert missing rows in one
statement per table. Avoids pulling rows through Python and keeps the operation
transactional.

Schema version is checked on every source before anything is written. A source from
a newer schema is refused, matching the existing behaviour when opening one.

The `results.run_id` foreign key has to be remapped, since ids are per-database.
Insert runs first, then join through `run_uid` to find the new id:

```sql
INSERT INTO results (run_id, test_id, ...)
SELECT target_runs.id, source_results.test_id, ...
FROM source.results AS source_results
JOIN source.runs AS source_runs ON source_runs.id = source_results.run_id
JOIN runs AS target_runs ON target_runs.run_uid = source_runs.run_uid
WHERE source_runs.run_uid IN (<the run_uids just inserted>)
```

## 3. GitHub Action

A **composite** action, not Docker or JavaScript. Composite is transparent — the
judges can read the YAML and see exactly what runs, with no opaque image.

```yaml
- uses: ahmedhashmu/flaky-test-detective@main
  with:
    report-path: reports/junit.xml
```

Steps: restore cache → install → ingest → triage → comment on the PR → save cache →
upload artifacts → set outputs.

PR comments update in place, found by a hidden HTML marker in the body. A bot that
posts a fresh comment on every push is a bot people mute, and a muted bot reports
nothing.

`fail-on` maps to the documented exit codes. Default `regression`, matching the CLI:
known flakes should not block a merge, a real break should.

## 4. Flakiness attribution

Group a test's outcomes by commit in chronological order, then find the first commit
that shows divergence (a pass and a fail at the same SHA, or a runner-recorded
retry).

The honest part is what happens when the answer is unknowable:

- No commit SHAs at all → say so, suggest running inside a repo.
- No commit with more than one run → divergence cannot be observed anywhere; say
  that more runs per commit are needed rather than pointing at a random commit.
- Divergence at the very first recorded commit → the flakiness predates the
  recorded history. Report that, rather than blaming the earliest commit that
  happens to be in the window, which would be an accusation the data does not
  support.

That last case is the one a careless implementation gets wrong, and it is the one
most likely to send someone to revert an innocent commit.

## Trade-offs accepted

**Synthetic data is not real data.** The generator models failures as independent
draws, and real flakiness clusters — load spikes, noisy neighbours. So the benchmark
measures the scoring rules against their own model of the world, which is a genuine
limitation and is documented as one. It is still far more than the previous
evidence, which was one hand-built suite of sixteen tests.

**Merging trusts source schemas.** Version is checked, but a hand-corrupted database
could still poison a merge. Acceptable for a local developer tool.

**The Action pins to `@main` in examples.** A released tag would be better practice;
there is no release yet.
