# Project structure

```
flaky-test-detective/
├── .kiro/
│   ├── specs/flaky-test-detective/   requirements, design, tasks
│   ├── steering/                     product, tech, structure
│   └── hooks/                        agent hooks
├── src/flaky_detective/
│   ├── __init__.py
│   ├── models.py          data model, Status, verdicts
│   ├── normalize.py       message scrubbing and signatures
│   ├── storage.py         SQLite schema and queries
│   ├── config.py           .flaky.toml discovery and defaults
│   ├── environment.py     git and CI metadata detection
│   ├── ingest/
│   │   ├── __init__.py    ingest orchestration, idempotency
│   │   └── junit.py       JUnit XML parsing, all dialects
│   ├── analysis/
│   │   ├── __init__.py    analyze() entry point
│   │   ├── flakiness.py   divergence, flips, scoring, verdicts
│   │   ├── clustering.py  signature clustering
│   │   ├── ordering.py    order-dependence detection
│   │   └── classify.py    root-cause heuristics
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
