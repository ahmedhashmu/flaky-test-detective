# 0003 — The analysis layer takes data, not a database

**Status:** Accepted

## Context

The obvious shape for `analyze()` is to hand it a database connection and let it query
what it needs. It has the connection, it knows what it wants, and it avoids loading rows
it will not use.

## Decision

`analysis/` accepts lists of `TestOutcome` and returns conclusions. The caller performs
the query. `analysis/` may not import `sqlite3`, `storage`, `os` or `pathlib`.

## Consequences

**Scoring rules are cheap to test.** Every case in
[`tests/test_flakiness.py`](../../tests/test_flakiness.py) is a compact pattern string:

```python
analyze_one_pattern(".F.F.F.F.FFF")  # flaky, then an unlucky streak
analyze_one_pattern("........FFFF")  # a regression
```

With a database in the signature, each of those needs a fixture, a temp path and a schema.
The rules would have been tested less thoroughly, and the rules are where the subtle bugs
live — both detector corrections
([0004](0004-order-dependence-needs-a-polluter.md),
[0006](0006-streak-beats-chance.md)) were found and fixed in tests that construct data
directly.

**The accuracy benchmark is possible at all.** `benchmark/` generates labelled
`TestOutcome` lists and feeds them straight to the real `analyze()`. If analysis needed a
database, the harness would have to write one — or, more likely, would have grown a
parallel reimplementation of the scoring, which would then measure something other than
the shipped code.

This consequence was not foreseen when the rule was written. It is the strongest argument
for it.

**One extra pass over the data.** Analysis loads everything in the window and aggregates
in memory. At 100k results that is roughly 30 MB and comfortably inside the performance
budget, and it avoids an N+1 query per test.

**Two companion rules fall out.** `report/` may not compute a derived value, so the four
output formats cannot drift apart; and `models` and `normalize` import nothing internal,
so everything can import them without a cycle.

## Enforcement

Prose decays on the first hurried change, so all three rules are asserted in
[`tests/test_architecture.py`](../../tests/test_architecture.py), which parses the AST of
every module and checks its imports. It has already fired twice for real: once when
`benchmark/` was added and the internal-name allowlist was stale, and once when
`report/console.py` imported `BlameResult` from `analysis/attribution` — correctly
prompting those result types to move to `models` where the other result types live.
