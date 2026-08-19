# Project structure

```
flaky-test-detective/
├── .kiro/
│   ├── specs/flaky-test-detective/   requirements, design, tasks
│   ├── steering/                     product, tech, structure
│   └── hooks/                        agent hooks
├── src/flaky_detective/
│   ├── __init__.py
│   ├── models.py          data model, Status, verdicts, result types
│   ├── normalize.py       message scrubbing and signatures
│   ├── storage.py         SQLite schema, queries, merging
│   ├── config.py           .flaky.toml discovery and defaults
│   ├── environment.py     git and CI metadata detection
│   ├── ingest/
│   │   ├── __init__.py    ingest orchestration, idempotency
│   │   └── junit.py       JUnit XML parsing, all dialects
│   ├── analysis/
│   │   ├── __init__.py    analyze(), triage() entry points
│   │   ├── flakiness.py   divergence, flips, scoring, verdicts
│   │   ├── clustering.py  signature clustering
│   │   ├── ordering.py    order-dependence detection
│   │   ├── attribution.py blame: when flakiness started
│   │   └── classify.py    root-cause heuristics
│   ├── benchmark/         accuracy against ground truth
│   │   ├── __init__.py    run_benchmark(), sweep()
│   │   ├── generate.py    labelled population generation
│   │   └── score.py       precision, recall, confusion matrix
│   ├── runner.py          hunt: repeated execution
│   ├── quarantine.py      quarantine list and exporters
│   ├── report/
│   │   ├── __init__.py
│   │   ├── console.py
│   │   ├── markdown.py
│   │   ├── json_report.py
│   │   ├── html.py
│   │   └── triage.py
│   └── cli.py             typer app, exit codes
├── tests/
│   ├── fixtures/          real JUnit XML from each runner
│   └── test_*.py
├── examples/flaky_demo/   deliberately flaky suite, the tool's own fixture
├── .github/workflows/
├── pyproject.toml
└── README.md
```

## Dependency direction

Strictly one way. `cli` → `report` → `analysis` → `storage` → `models`.

`runner` and `benchmark` sit beside the pipeline as producers, feeding it rather than
being called by it. `benchmark` depends on `analysis` being pure: it generates labelled
`TestOutcome` lists and hands them to the real `analyze()`. If analysis ever needed a
database, the harness would have to grow a parallel copy of the scoring, and would then
be measuring something other than the shipped code.

Result types belong in `models`, even when only one analysis module produces them.
`BlameResult` originally lived in `analysis/attribution.py`, which forced `report/` to
import from `analysis/` to render it; the architecture test caught that, and the fix was
to move the type rather than to relax the rule.

`models` and `normalize` import nothing from the package. `analysis` must not
import `storage`: it takes lists of `TestOutcome` and returns analyses, which is
what makes it testable without a database. If an analysis function needs a query,
the caller does the query and passes the data in.

`report` must not compute. If a reporter needs a derived number, it belongs in
`analysis`, otherwise the console and markdown outputs will drift apart.

## Where things go

**A new result format** (TAP, Allure): new module in `ingest/`, register it in
`ingest/__init__.py`. Nothing else changes.

**A new root-cause category**: add to the rule table in `analysis/classify.py`
and add a remediation hint. One place.

**A new output format**: new module in `report/`, wire into `cli.py`. Do not add
formatting logic anywhere else.

**A new detection signal**: new function in `analysis/flakiness.py`, folded into
the score, and document the weight change in the design doc. Score weights are
in one place at module level, never inline.

**A new ground-truth case**: `benchmark/generate.py`, plus a test asserting the
generator produces what it claims. The harness had a bug once that made it report
polluter precision of 0.000 while the detector was perfect, so the generator is
tested as carefully as the scorer.

## Changing a threshold

Any change to a scoring threshold needs a `flaky benchmark` number before and
after, recorded in the commit message or the relevant ADR. Not an opinion, and not
a demo-suite screenshot.

Two thresholds have been changed this way already, and both had effects no amount
of reasoning would have predicted: the streak-beats-chance rule cut the
missed-break rate from 18.9% to 5.4% while holding false alarms at zero, and making
the streak requirement proportional to history cut the 5-run false-alarm rate from
50% to 12.5%.

## Naming

- Modules singular (`report`, not `reports`), except `analysis`
- `test_id` everywhere for the stable identifier, never `test_name` or `nodeid`
- `outcome` for a single test result, `run` for one execution of a suite
- Test files mirror source: `analysis/flakiness.py` → `tests/test_flakiness.py`

## Things that must not happen

- `analysis/` importing `sqlite3`
- `report/` computing a score
- Business logic in `cli.py` beyond argument handling and exit codes
- The demo suite in `examples/` running as part of this project's test suite
