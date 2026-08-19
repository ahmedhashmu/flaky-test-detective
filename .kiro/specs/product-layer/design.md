# Design: Product Layer

## 0. The shape of the addition

```
                    ┌──────────────────────────────┐
   browser  ───────▶│  web/  React 18 + MUI 6      │
                    │  compiled into the package   │
                    └──────────────┬───────────────┘
                                   │ fetch /api/*
                    ┌──────────────▼───────────────┐
                    │  web/__init__.py  http.server│
                    │  web/api.py       serialize  │
                    └──────────────┬───────────────┘
                                   │
   terminal ──▶ cli.py ──▶ report/ ┴─▶ analysis/ ──▶ storage/ ──▶ models
```

The layer is thin on purpose. `web/api.py` calls the same `analyze()` the terminal
calls and turns the result into dicts. It computes nothing. That is what makes
FR2.6 — the dashboard cannot show a verdict the CLI would not — a structural property
rather than a promise, and there is a test asserting the payload's verdicts and scores
equal `analyze()`'s output element for element.

## 1. The CI Trust Score

### Why not a fitted index

The obvious move is to weight a handful of signals, tune the weights until the demo
database scores well, and call it a reliability index. It would look more
sophisticated and be strictly worse, because the number could not be interrogated.
This project's entire argument is that a conclusion you cannot check is a conclusion
you should not trust; shipping an unexaminable headline metric would contradict it in
the most visible place in the product.

So the score is a deduction from 100, and every point removed is attributed:

| Component | Ceiling | Deducts for |
|---|---:|---|
| Unresolved breaks | 35 | Regressions and never-passing tests |
| Flaky tests | 30 | Active flakes, weighted by score rather than counted |
| Commit evidence | 20 | Runs without a commit SHA |
| Quarantine debt | 15 | Entries left past their expiry |

Three of those ceilings encode a judgement worth stating:

**Breaks outrank flakes** (35 > 30, and saturating at two rather than ten). A suite
with one real regression is less trustworthy than one with five known flakes, because
the flakes are known. Ranking it the other way would reward the habit this tool
exists to break.

**Flakes are weighted, not counted.** The penalty sums the flakes' scores, so a test
failing 1 run in 20 costs almost nothing while one failing 10 in 20 costs a lot.
Counting them would make "quarantine the nearly-stable ones" the cheapest way to
raise the score, which is the wrong incentive to build in.

**Missing commit data costs 20 points**, and that is not a style complaint. The
benchmark from the previous round measured the false-alarm rate rising from 0% to 25%
without commit SHAs. The verdicts above really are weaker, and the score is the
honest place to say so.

### Rounding, and why it needed its own field

The score is displayed as a whole number: a CI trust score of 57.6 implies a
precision the inputs do not have. Component penalties are displayed to one decimal.
Those two choices interact badly, and the interaction was found by running this
project's own README verification step:

```
58 = 57.6
```

The penalties add up to 42.4, so the deduction is 42.4 and the displayed score is 58.
A reader adding up the numbers on screen lands half a point away from the headline
figure — and has no way to tell whether that is rounding or an undisclosed
adjustment. For a metric whose only justification is that it can be taken apart, that
is a real defect, not a cosmetic one.

The fix is `TrustScore.deducted`, the exact sum, surfaced in the API payload and shown
on the card as literal arithmetic:

```
100 − 42.4 deducted = 58
```

Rejected alternatives: a fractional score (implies precision the data lacks);
integer-only penalties (a sub-point penalty would round to zero and a genuine flake
would be labelled healthy); largest-remainder redistribution (would shift a
component's penalty away from its own stated formula, which is the one thing that
must stay checkable).

### Wasted CI time

```
estimate = (flaky failures) × (median suite duration)
```

The most quotable number in the product and the least defensible, which is exactly
the combination to be careful with. It assumes each flaky failure costs one re-run of
the suite; the tool cannot observe re-runs outside its own view. It is therefore
labelled an estimate everywhere it appears and carries its assumption as a tooltip,
in the JSON payload, and in the docs.

`analysis/health.py` is pure, per NFR5: it receives an `AnalysisReport`, a median
duration, and a quarantine-days figure. It does not read the quarantine file or the
database — the caller does that and passes the numbers in.

## 2. Serving it

### `http.server`, not FastAPI

FastAPI plus uvicorn is the default answer and was rejected. It would take runtime
dependencies from two to five or more, and the tech steering's rule — "every addition
is a reason for someone not to install the tool" — applies with more force here than
anywhere else, because a dashboard is a nice-to-have on a side-concern tool. Nobody
should fail to install a flaky-test detector because of a web server they did not ask
for.

What the stdlib costs: no automatic validation, no OpenAPI, manual routing. All
affordable for six read-only endpoints. What it buys: `pip install
flaky-test-detective` still pulls two packages.

```
GET /                  index.html
GET /assets/*          hashed bundle
GET /api/health        liveness plus API version
GET /api/overview      trust score, summary, ranked tests, clusters, caveats
GET /api/tests/<id>    evidence, timeline, diagnosis, blame, neighbours, actions
```

Unknown paths fall through to `index.html` so client-side routes survive a hard
refresh. The static handler resolves the target and rejects anything not contained in
the asset directory, so a crafted path cannot read outside it.

`serve()` returns the server rather than running it, so tests can drive it on an
ephemeral port and shut it down cleanly.

`_Server.handle_error` swallows `ConnectionResetError`, `BrokenPipeError`,
`ConnectionAbortedError` and `TimeoutError`. Browsers cancel in-flight requests
routinely, and the default handler prints a traceback for each one — a tool whose
selling point is trustworthiness should not appear to crash every time someone clicks
a link.

A two-second response cache sits in front of the analysis. The overview re-analyses
the whole history on every request, which is the correct thing to do for freshness and
wasteful when a page issues several requests at once.

### Security posture

Binds `127.0.0.1`. `--host` anything else prints a warning naming what becomes
readable: every test name, failure message and commit SHA in the database. There is
no authentication, which is the same model as `python -m http.server` and is stated
rather than implied.

Failure messages are untrusted text from CI artifacts, rendered in a browser, so
responses carry a Content Security Policy and the payloads are JSON-escaped by
construction.

## 3. Shipping the frontend

React 18 with Material UI 6, built by Vite into `src/flaky_detective/web/static/`,
**committed to the repository**.

Committing build output is normally poor practice. The alternative is requiring
`npm ci && npm run build` before the dashboard works, which for someone evaluating
the tool in five minutes means the dashboard does not exist. The cost of committing it
is that the bundle can silently go stale, so that cost is paid down directly: a CI job
rebuilds it on every push and fails if the tree comes back changed, and
`tests/test_web.py` asserts the bundle is present and contains the markers the pages
render. A forgotten rebuild breaks the build instead of shipping a blank page.

`recharts` was dropped after being added. One dependency for one chart, when the
timeline is a row of coloured boxes grouped by commit, is not a trade worth making;
the hand-rolled version is smaller and renders the divergence outline — the thing that
*is* the proof — more directly than a generic chart would.

### The investigation page

Four sections, in the order a person actually asks the questions:

1. **Evidence** — split into *Proven by the detector* and *Inferred*. Same-commit
   divergence, runner-recorded retries and polluter correlation are measurements.
   Flip rate and message-pattern matching are hints. They are grouped, labelled and
   styled differently, because rendering them alike lets the weaker one borrow the
   authority of the stronger, and that is precisely how a false "this is flaky" on a
   real regression happens.
2. **Timeline** — runs grouped by commit, with any group containing both a pass and a
   fail outlined. That outline is the proof, so it is drawn rather than described.
3. **Why** — the heuristic cause, marked as heuristic, plus the order-dependence
   figures and the predecessor table so the polluter claim can be checked rather than
   believed. A predecessor that precedes passes and failures about equally is visible
   as such.
4. **Action** — copy-pasteable commands. No buttons that mutate state.

The read-only decision is deliberate. A one-click "quarantine this" is one click away
from silencing a real regression, and the misclassification rate is not zero.

## 4. `flaky issue`

Renders an issue body or chat message from the real diagnosis:

```
flaky issue test_expects_clean_registry | gh issue create --body-file -
flaky issue test_expects_clean_registry -f slack | curl -d @- "$SLACK_WEBHOOK"
```

Formats: `markdown`, `github`, `jira`, `slack`, `json`.

It emits; it never posts. A built-in Slack or Jira client would need a token, and the
credential-free property is worth more than the saved pipe — it is what makes the
whole tool inspectable by someone who has just met it. Piping into `gh` or `curl` also
means the user's existing auth is used and no secret passes through this tool.

The body is written to be worth filing. Not "fix flaky test", but the verdict, the
counts behind it, the divergence evidence, the named polluter with its correlation,
the attribution window, and the remediation hint — the same content the terminal
prints, in a form that can be pasted.

## Trade-offs accepted

**A committed bundle is a 485 KB binary blob in the repository.** Justified by the
first-five-minutes experience and guarded by a CI job that rebuilds it. The guard is
the part that makes it defensible.

**No authentication.** Single-user local viewer bound to loopback. Any auth story
worth having needs accounts, which is explicitly out of scope.

**Vite output must be reproducible for the staleness check to work.** Asset hashes
depend on the toolchain, so CI pins the Node major version and `package-lock.json`
pins the rest. If that ever drifts, the failure is a confusing diff rather than a
silent bug — an acceptable failure mode, and the error message says what to do.

**The trust score's ceilings are judgements, not measurements.** Unlike the detection
thresholds, which the benchmark can settle, there is no ground truth for "how much
should a regression cost". They are stated, bounded, and individually visible, which
is the best available substitute for being measured.
