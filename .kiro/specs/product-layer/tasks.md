# Implementation Plan: Product Layer

Ordered so the engine work lands before anything that renders it. The trust score and
the payloads are pure functions, so they are testable before a server exists at all.

- [x] 1. Suite health scoring
  - `analysis/health.py`: `trust_score()`, `median_run_duration()`, the four
    component functions, penalty ceilings and saturation points at module level
  - `models.py`: `HealthComponent`, `TrustScore`
  - Pure: takes an `AnalysisReport`, a median duration and a quarantine-days figure
  - _Requirements: FR1.1–FR1.6, NFR5_

- [x] 2. JSON payloads, standard library only
  - `web/api.py`: `overview_payload()`, `test_detail_payload()`, `triage_payload()`,
    `test_summary()`, `VERDICT_TONE`
  - Serialization only; every number comes from `analyze()`
  - Evidence split into proven and inferred at the payload level, not in the UI
  - _Requirements: FR2.4, FR2.6, FR2.7, NFR1, NFR6_

- [x] 3. `flaky serve`
  - `web/__init__.py`: routing, static handling with containment checks, response
    cache, CSP, loopback default with a warning on `--host`
  - `serve()` returns the server so tests can drive it on an ephemeral port
  - _Requirements: FR2.1, FR2.8, NFR7_

- [x] 4. React + Material UI scaffold
  - `web/` with Vite, TypeScript, MUI 6 theme, typed API client
  - Build output targeted at `src/flaky_detective/web/static/`
  - _Requirements: FR2.1, NFR3_

- [x] 5. Overview page
  - `TrustScoreCard` with every component and its reasoning, headline counts,
    ranked table carrying the counts behind each verdict, caveats
  - _Requirements: FR1.3, FR2.2, FR2.7_

- [x] 6. Investigation page
  - Evidence (proven vs inferred), timeline grouped by commit with divergent groups
    outlined, why (heuristic cause, order figures, predecessor table), action
    (copy-pasteable commands)
  - _Requirements: FR2.3, FR2.4, FR2.5_

- [x] 7. Ship the bundle
  - Commit the compiled assets; package them in the wheel
  - `tests/test_web.py::TestBundleIntegrity` asserts presence and page markers
  - CI job rebuilds and fails on any diff
  - _Requirements: NFR3, NFR4, acceptance criteria 1, 6_

- [x] 8. `flaky issue`
  - `report/issue.py`: markdown, github, jira, slack, json
  - Emits to stdout; posts nothing
  - _Requirements: FR3.1–FR3.3, NFR2_

- [x] 9. Tests
  - `tests/test_health.py`, `tests/test_web.py`, `tests/test_issue.py`
  - The load-bearing ones: penalties account for the whole deduction; payload
    verdicts equal `analyze()`; proven and inferred are separated; path traversal is
    refused; the bundle is current
  - _Requirements: acceptance criteria 2–5_

- [x] 10. Documentation
  - `docs/dashboard.md`; README section, command table and testing steps
  - _Requirements: FR1.3, FR2.4_

- [x] 11. Verification
  - Gates, full suite, frontend typecheck, HTTP smoke test in CI, clean clone
  - _Requirements: all_

---

## Outcome

All 11 tasks landed. Final state:

- 654 tests, running in about 16 seconds
- `ruff check`, `ruff format --check`, `mypy` (30 files) and `tsc` all clean
- Runtime dependencies still exactly two: `typer` and `rich`
- `flaky serve` works from a clean clone with no `npm` step; CI proves the committed
  bundle matches a fresh build and that the three requests a browser makes all return
  200 against a real database

### What the plan got wrong

**Task 1's headline claim was false, and this project's own README caught it.** The
docstrings, the docs and the UI tooltip all said the component penalties "sum exactly
to `100 − score`, with no residual and no fudge factor". Running the verification
snippet from the README printed `58 = 57.6`. Penalties are displayed to one decimal,
the score is rounded to a whole number, and nothing anywhere exposed the difference —
so a reader adding up the numbers on screen would land half a point off the headline
figure with no way to distinguish rounding from an undisclosed adjustment. For a
metric whose only justification is that it can be taken apart, that is a defect in the
thing being sold.

Fixed by adding `TrustScore.deducted` (the exact sum), surfacing it in the payload, and
printing the arithmetic on the card: `100 − 42.4 deducted = 58`. Four pieces of prose
were corrected to match. The instructive part is that the bug was invisible to 642
passing tests, because the test asserting the invariant had been written with `round()`
around it — the assertion had quietly been weakened to fit the behaviour instead of the
claim.

**An unplanned task appeared: architecture rules for the new layer.** `web/` arrived
with no enforced constraints at all, in a repository where the equivalent rules for
`analysis/` and `report/` are 60-odd assertions. The rule that the dashboard cannot show
a verdict the terminal would not was a comment in a docstring. It is now
`TestWebIsAPresentationLayer`: no `sqlite3`, no locally defined weight, threshold or
penalty ceiling, and nothing upstream of `cli` importing `web`. The middle one is the
load-bearing check — a penalty ceiling copied into the payload builder is exactly how the
browser and the terminal would start disagreeing about the same suite.

**Task 3 printed a traceback on every navigation.** `http.server` logs unhandled
handler exceptions, and browsers cancel in-flight requests as a matter of course, so
clicking a link produced `ConnectionResetError` on the console. A tool whose selling
point is trustworthiness cannot look like it is crashing. `_Server.handle_error` now
suppresses the four connection-teardown errors and nothing else.

**Task 4 added `recharts` and then removed it.** One dependency for one chart. The
timeline is runs grouped by commit with divergent groups outlined, which is a row of
coloured boxes; the hand-rolled version is smaller and shows the proof more directly
than a generic chart component.

**`storage.recent_runs()` returned `list[dict[str, object]]`** and every caller had to
`cast()` its way back to the shape it needed. mypy flagged the casts, which was the
real signal: the contract had been lost at the boundary. Replaced with a `RunRecord`
dataclass, along with `DatabaseStats` for the same reason.

**An AI "explain the fix" feature was specified, then cut.** It appeared attractive and
failed two of this round's own constraints: it needs an API key, which breaks the
credential-free judge path, and it puts generated prose next to measured evidence in a
tool whose argument is evidence over inference. Careful labelling would reduce the risk
but not the fact that the most eye-catching sentence on the page would be the least
verifiable one. Recorded in the requirements as deliberately out of scope rather than
left as an omission.
