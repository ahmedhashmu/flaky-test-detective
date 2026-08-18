# Design: Flaky Test Detective

## Architecture

A pipeline. Each stage has one input type and one output type, which keeps the
analysis testable without touching a filesystem or a database.

```
test runner                                                    reports
  │ JUnit XML                                                     ▲
  ▼                                                                │
ingest/ ──── models ────► storage (SQLite) ────► analysis/ ────► report/
  parsers    TestOutcome    runs, results        flakiness       console
             TestRun        idempotent by        clustering      markdown
                            content hash         ordering        json
                                                 classify        html
                                                     │
                                                     ▼
                                                 quarantine/
                                                   skip lists
```

The `runner` module sits beside `ingest` and feeds it: it executes a real test
command N times and hands each resulting XML file to the same parsers. Hunting
and CI ingestion converge on one code path, so there is no second
implementation to keep correct.

## Data model

Two tables. Denormalized deliberately: analysis is read-heavy and the join cost
on every query is not worth the normalization.

```sql
CREATE TABLE runs (
    id           INTEGER PRIMARY KEY,
    run_uid      TEXT UNIQUE NOT NULL,  -- content hash, gives idempotency
    commit_sha   TEXT,
    branch       TEXT,
    ci_run_id    TEXT,
    started_at   TEXT NOT NULL,
    source_path  TEXT,
    runner       TEXT,                  -- pytest | jest | go | junit | unknown
    iteration    INTEGER,               -- hunt iteration, NULL for CI ingest
    seed         TEXT,                  -- randomization seed if known
    total        INTEGER NOT NULL,
    failed       INTEGER NOT NULL,
    skipped      INTEGER NOT NULL,
    duration     REAL
);

CREATE TABLE results (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_id      TEXT NOT NULL,         -- file::class::name, stable across runs
    suite        TEXT,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL,         -- passed | failed | error | skipped
    duration     REAL,
    message      TEXT,
    detail       TEXT,
    signature    TEXT,                  -- normalized message, computed at ingest
    position     INTEGER                -- index within the run, for FR3.4
);
```

`test_id` stability is the load-bearing assumption of the whole tool. If the
same test yields different ids across runs, history fragments and nothing works.
Parsers must construct it from `classname` + `name` and must not include
parameterized values that embed random data — a `pytest` test id like
`test_x[tmp0x7f2a]` would fragment, so ids are normalized through the same
scrubber as messages before storage.

`signature` is computed at ingest rather than at analysis time. It is
deterministic given the message, and computing it once avoids re-normalizing
100k rows on every query (NFR1).

Indexes on `results(test_id)`, `results(run_id)`, `results(signature)`, and
`runs(commit_sha)`. The analysis queries all filter on those.

`run_uid` is `sha256(source content + source path + iteration)`. Content hashing
rather than path alone, because CI writes the same path each time; path included
too, because two suites can legitimately produce byte-identical XML.

## Detection algorithm

The core insight: **same code, different outcome** is the only direct proof of
flakiness available without reading source. Everything else is inference.

### Same-commit divergence (primary)

Group results by `(test_id, commit_sha)`. Any group containing both a pass and a
fail is proof. Runs with no commit SHA are excluded from this signal, not
guessed at.

```
divergent_commits = |{ c : ∃ pass ∧ ∃ fail at commit c }|
observed_commits  = |{ c : test ran at c more than once }|
divergence_rate   = divergent_commits / observed_commits
```

Denominator is commits where the test ran *more than once*, since a commit with
a single run cannot show divergence and including it would dilute the rate with
non-evidence.

### Flip rate (secondary)

Order results by time, map to a binary pass/fail sequence, count transitions.

```
flip_rate = transitions / (len(sequence) - 1)
```

A test alternating every run scores 1.0; a test that failed once and stayed
failed scores near 0, correctly treating it as a regression rather than a flake.

### Flake score

```
raw        = 0.7 · divergence_rate + 0.3 · flip_rate
confidence = min(1, runs / CONFIDENCE_RUNS)      # CONFIDENCE_RUNS = 10
score      = raw · (0.5 + 0.5 · confidence)
```

Divergence is weighted higher because it is proof and flip rate is inference.
The confidence factor never drops below 0.5, so a strong signal on few runs
still surfaces — it just cannot outrank the same signal on many runs.

When divergence is unavailable (no SHAs), the weight renormalizes onto flip rate
alone rather than silently scoring everything low — but capped:

```
raw = FLIP_ONLY_CEILING · flip_rate        # FLIP_ONLY_CEILING = 0.85
```

The cap was added after a test caught the original version scoring a perfectly
alternating history at 1.00 with no commit data, identical to one backed by
same-commit proof. Both are probably flaky, but only one has evidence that the
code was not the variable. Without the cap the score stops carrying information
about how much the verdict can be trusted, which is the reason for having a score
rather than a boolean.

### Classification

A test is exactly one of:

- **flaky** — score above threshold and at least one pass and one fail
- **regression** — fails consistently, no divergence, previously passed
- **broken** — fails in every observed run, never passed
- **fixed** — historically flaky, last N runs all passed
- **stable** — everything else

`regression` versus `flaky` is the distinction that decides exit code 2 versus 1,
so it drives CI behaviour and is worth getting right. `broken` is separated from
`regression` because a test that has never passed is usually an incomplete
commit, not a break.

### Regression detection, refined

The first implementation called anything with three trailing failures a
regression. Run against the demo suite that misfired: a genuinely flaky test that
happened to fail its last three iterations was reported as a regression, which
would send someone hunting a bad commit that does not exist.

The rule now branches on what evidence exists:

- **Commit SHAs present.** Ask whether the newest commit still shows a pass. If it
  does, flakiness is the live explanation. If it shows only failures, the code is
  the more likely variable. This is evidence, so it is trusted outright.
- **No commit SHAs.** There is no way to separate "flaky and unlucky" from "newly
  broken", so fall back to shape. A regression flips once: pass, pass, pass, fail,
  fail, fail. A flake flips repeatedly. Requiring `flips <= 2` separates them.

Verified against three cases: a regression with commit data, the same shape
without commit data, and a flake with nine flips whose last three runs failed. The
first two report `regression`, the third reports `flaky`.

## Message normalization

Ordered substitutions, most specific first, since a UUID would otherwise be
partly eaten by the hex-address rule:

| Pattern | Replacement |
|---|---|
| UUIDs | `<UUID>` |
| ISO timestamps | `<TIMESTAMP>` |
| Hex addresses `0x...` | `<ADDR>` |
| `:port` in host:port | `:<PORT>` |
| Temp paths | `<TMP>` |
| Absolute paths | `<PATH>` |
| `line 123` | `line <N>` |
| Durations `1.234s`, `45ms` | `<DURATION>` |
| Bare integers ≥ 3 digits | `<NUM>` |
| Collapsed whitespace | single space |

Truncated to 500 chars, since stack traces make poor cluster keys past the first
few frames. Short integers are preserved: `expected 2, got 3` stays meaningful,
while `port 54321` does not.

## Root-cause heuristics

Each rule is a set of regexes over the **raw** message plus optional structural
evidence. Rules are scored, and the highest-scoring match wins, with matched
terms retained so a human can see *why* and disagree.

Raw rather than normalized, which is a correction to the original plan:
normalization replaces integers of three or more digits with `<NUM>`, which
destroys exactly the values some rules depend on. `HTTP 503` becomes `HTTP <NUM>`
and the network rule stops firing. Normalization is for clustering only.

| Category | Signal | Suggested fix direction |
|---|---|---|
| `timeout` | timeout, timed out, deadline exceeded, `<DURATION>` near limit | explicit waits over sleeps |
| `race` | concurrent, thread, lock, deadlock, async, event loop | synchronize shared access |
| `order_dependence` | structural: position correlation (below) | isolate per-test state |
| `network` | connection refused, DNS, socket, HTTP 5xx, unreachable | mock the boundary |
| `resource` | out of memory, too many open files, disk, port in use | pool or release resources |
| `time_dependence` | date, timezone, DST, `<TIMESTAMP>` in assertion | inject a clock |
| `randomness` | random, shuffle, uuid, faker | seed deterministically |
| `assertion` | fallback | inspect the assertion |

These are heuristics and the report labels them as such. The value is narrowing
a 200-test worklist into "these 30 are timeouts, that is one afternoon".

## Order-dependence detection

This section was rewritten twice after measuring the results. Both the original
design and the first correction produced false positives on the demo suite, and
the record of why is more useful than the final rule alone.

### What was designed first

Compare the distribution of a test's `position` when passing versus failing, and
flag a large separation relative to the spread:

```
separation = |mean(pos | fail) − mean(pos | pass)| / (stdev(all positions) + ε)
```

Flag when `separation > 1.0` with at least 3 observations per side.

**This misfired.** Dividing by the pooled spread ignores sample size, so a wide gap
measured from five noisy points outranked a narrow gap measured from fifty
consistent ones. Run against the demo suite it labelled a purely random test as
order dependent at 1.1 standard deviations, and named an innocent predecessor as
the polluter.

### First correction: make it sample-size aware

Divide by the standard error of the difference of means instead, and gate on a
t-statistic of 2.5:

```
se = sqrt(var(pos | pass)/n_pass + var(pos | fail)/n_fail)
t  = |mean(pos | fail) − mean(pos | pass)| / se
```

This removed that false positive. The predecessor test was also strengthened, and
made an independent trigger on the reasoning that a polluter can sit anywhere
earlier in the run and so may produce little position effect.

**This misfired differently.** Requiring only "fails 90% of the time after X"
flagged eight of ten demo tests with a reported share of 1.0. In a shuffled suite
of ten tests, a given predecessor precedes the victim only three or four times, and
a test that already fails 70% of the time will fail all four of those by chance
about a quarter of the time.

### What the measurement actually showed

Forty shuffled iterations of the demo suite, position statistics per test:

| Test | t | separation | Truth |
|---|---:|---:|---|
| `test_append_order_is_stable` | 3.47 | 1.06 | thread race |
| `test_worker_finishes_within_deadline` | 3.47 | 0.89 | timing |
| `test_expects_clean_registry` | 2.33 | 0.71 | **order dependent** |
| `test_counts_registered_sessions` | 2.27 | 0.70 | **order dependent** |
| `test_token_still_valid_at_check_time` | 1.25 | 0.40 | timing |
| others | < 1.1 | < 0.4 | random / network / race |

The two strongest position signals are both *timing* flakes. The two genuinely
order-dependent tests rank below them. Position tracks how late in the run a test
executes, which tracks machine state: warmer caches, more threads created, more
garbage to collect. That is a real cause of flakiness but it is not shared-state
pollution, and reporting it as `order_dependence` sends someone looking for a
leaked fixture that does not exist.

### Final rule

Order dependence requires **naming a polluter**. The predecessor must:

- have run immediately before the test at least 4 times,
- precede a failure at least 90% of those times,
- and beat chance: `base_failure_rate ^ failures_after_it <= 0.05`, where the base
  rate is the test's own overall failure rate.

Plus a guard that a test failing more than 75% of the time overall cannot blame a
predecessor at all, because it fails after everything.

Position separation is still computed and reported as supporting detail, since it
is the interpretable number to show a human, but it cannot trigger the verdict.

Result on the demo suite: exactly the two genuine victims flagged, both naming
`test_registers_session` at 100%, and every other flake keeps its true cause.

**Known limitation:** only the immediately preceding test is considered, so a
polluter running several tests earlier is missed. Checking every earlier test for
every candidate is quadratic in suite size. The cheap version catches the common
case, because suites usually shuffle within a file or class.

## Hunting

Run the command N times via `subprocess`, each iteration writing JUnit XML to a
temp path that is then ingested. Order randomization is injected per runner:
`-p no:randomly --randomly-seed=<n>` for pytest when the plugin is present,
`--shuffle` for jest, `-shuffle=on` for go test. If randomization is not
available for the detected runner, say so rather than silently running N
identical iterations, since the user would otherwise believe they had tested for
order dependence.

The runner never parses stdout. It requires the XML path, because stdout formats
are unstable across versions while JUnit XML is not.

## Storage and performance

Analysis loads all results for the queried window in one pass and aggregates in
memory. At 100k rows this is roughly 30 MB and comfortably inside NFR1, and it
avoids N+1 queries per test. If a suite ever exceeds a few hundred thousand
results the aggregation moves into SQL, but designing for that now would add
complexity for a scale most repos never reach.

`PRAGMA journal_mode=WAL` so a concurrent CI ingest does not block a read.
Schema version stored in a `meta` table for future migrations.

## CLI surface

```
flaky init                      write config, create db
flaky ingest <paths...>         parse and store JUnit XML
flaky hunt -- <command>         run a command N times, ingest each
flaky analyze                   ranked flakes to the console
flaky triage <report>           are this run's failures known flakes?
flaky report --format md|json|html
flaky quarantine list|add|remove|export|verify
flaky history <test-id>         timeline for one test
flaky stats                     database summary
```

`hunt` uses `--` to separate its own flags from the wrapped command, avoiding
ambiguity when the command has flags of its own.

## Trade-offs accepted

**JUnit XML only.** It loses information the runner had, but it is the one format
every runner already produces, and requiring plugin installation would kill
adoption.

**Heuristic classification.** Will misclassify. Mitigated by showing the matched
evidence rather than presenting a verdict.

**No source analysis.** Gives up precise root causes; buys language independence,
which is the larger win for a tool meant to be dropped into any repo.

**SQLite over a service.** Cross-machine history requires committing or caching
the file. Accepted: zero-setup is what gets the tool used at all.
