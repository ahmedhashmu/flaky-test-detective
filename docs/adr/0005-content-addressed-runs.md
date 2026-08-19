# 0005 — Content-addressed run identity

**Status:** Accepted

## Context

Every ingested run needs an identity, for one immediate reason: CI retries re-present the
same artifact, and double-counting a run would silently corrupt every rate the tool
computes. A test that failed once in ten runs would look like it failed twice in twenty.

The obvious options are an auto-increment id (no help — the same file ingested twice gets
two ids) or the file path (no help either — CI overwrites the same path every run).

## Decision

```
run_uid = sha256(report content + resolved source path + iteration)
```

All three components earn their place:

- **Content** makes the same report recognizable however it arrives.
- **Path**, because two suites can legitimately produce byte-identical XML — two empty
  shards, for instance.
- **Iteration**, because a deterministic suite produces identical XML on every hunt
  iteration. Without it, a 20-iteration hunt would record one run.

## Consequences

**Ingest is idempotent.** Re-ingesting is a no-op, so CI retries and local
experimentation are both safe.

**Databases can be merged.** This was not the reason for the decision, and it is the more
valuable consequence.

Two databases built independently hold disjoint `run_uid` sets, *except* where they
genuinely ingested the same artifact — in which case the duplicate should collapse. So
merging is a set union, which is:

- **idempotent** — merging twice changes nothing;
- **order-independent** — merging A into B gives the same analysis as B into A.

Both are asserted directly in [`tests/test_merge.py`](../../tests/test_merge.py),
including across all six permutations of a three-way merge.

That turns a documented limitation into a feature. Before `flaky merge`, the README
listed cross-machine history under "Limitations", which ruled out sharded CI, matrix
builds, and pooling local hunts — most realistic use. The property that made the fix
sound had been sitting in the schema from the beginning, unexploited.

**Merging can reveal what neither source could see.** One machine only ever saw a test
pass; another only ever saw it fail. Neither can call it flaky. Pooled, the same commit
shows both outcomes, which is proof. There is a test for exactly this.

**Ids are opaque and unordered.** Nothing can sort by `run_uid` or infer anything from
it, which is why `started_at` exists and is indexed — flip detection depends on
chronological order.

**A changed report is a new run.** Re-running a suite after an edit produces a different
hash, correctly, since it is a different run. Reformatting the XML without changing
results would also produce a new id; harmless, and preferable to the alternative of
trying to canonicalize a format five runners disagree about.
