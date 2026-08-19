# 0010 — Ship the dashboard in the package, served by the standard library

**Status:** Accepted

## Context

Everything the tool knows was reachable only through a terminal. The person who most
needs it is the one whose build just went red and who is about to hit re-run for the
third time; they are not going to install a CLI first.

So: a dashboard. Which immediately raises two questions with expensive default answers.

**What serves it?** The default answer is FastAPI and uvicorn.

**How does it get built?** The default answer is "run `npm ci && npm run build` first".

Both defaults are wrong for this project, and for the same underlying reason: the
dashboard is a nice-to-have on a tool that solves a side-concern, and a side-concern tool
gets exactly one chance to be easy.

## Decision

**Serve it with `http.server` from the standard library.** Runtime dependencies stay at
`typer` and `rich`, and `tests/test_architecture.py::TestRuntimeDependencies` enforces
that with an import-level assertion.

**Commit the compiled bundle** to `src/flaky_detective/web/static/` and package it in the
wheel, so `flaky serve` works from a plain `pip install` with no Node toolchain present.

Six read-only endpoints:

```
GET /                  index.html
GET /assets/*          hashed bundle
GET /api/health        liveness plus API version
GET /api/overview      trust score, summary, ranked tests, clusters, caveats
GET /api/tests/<id>    evidence, timeline, diagnosis, blame, neighbours, actions
```

## Consequences

**No third runtime dependency.** The tech steering's rule is that every addition is a
reason for someone not to install the tool, and it applies with more force here than
anywhere else: nobody should fail to install a flaky-test detector because of a web
framework they did not ask for.

What the standard library costs: no request validation, no OpenAPI, manual routing. All
affordable for six read-only GETs. What it buys: `pip install flaky-test-detective` still
pulls two packages.

**A committed build artifact needs a guard, and it has one.** Committing build output is
normally poor practice, and the specific risk is that the bundle silently goes stale and
the dashboard renders against an old contract. That cost is paid down directly rather than
accepted:

- A CI job rebuilds the frontend on every push and fails if the working tree comes back
  changed, with an error message naming the fix.
- The check uses `git status --porcelain` rather than `git diff`, because a changed asset
  hash arrives as an untracked file plus a deletion and `git diff` alone would miss the
  new file.
- Node's major version is pinned in CI and `package-lock.json` pins the rest, since asset
  hashes are only reproducible on the toolchain that produced them.
- `tests/test_web.py::TestBundleIntegrity` asserts the assets exist and contain the
  markers the pages render, so a missing bundle fails the suite rather than serving a
  blank page.
- The same job then starts a real server against a real database and makes the three
  requests a browser makes, in order. A dashboard that only passes unit tests can still
  500 on the first request a judge makes.

**Browsers cancel requests, and the default server made that look like a crash.**
`http.server` prints a traceback for any unhandled handler exception, so ordinary
navigation produced `ConnectionResetError` on the console. `_Server.handle_error`
suppresses `ConnectionResetError`, `BrokenPipeError`, `ConnectionAbortedError` and
`TimeoutError`, and nothing else. A tool whose selling point is trustworthiness cannot
appear to fall over every time someone clicks a link.

**A two-second response cache sits in front of the analysis.** The overview re-analyses
the whole history per request, which is correct for freshness and wasteful when one page
load issues several requests.

**Unknown paths fall through to `index.html`** so client-side routes survive a hard
refresh. The static handler resolves each target and refuses anything not contained in
the asset directory, so a crafted path cannot read outside it.

**Bound to loopback, with no authentication.** Same model as `python -m http.server`.
`--host` anything else prints a warning naming exactly what becomes readable: every test
name, failure message and commit SHA in the database. Stated rather than implied.

**Failure messages are untrusted text rendered in a browser**, so responses carry a
Content Security Policy. They arrive from CI artifacts, which is the same threat model
that governs XML parsing.

**The dashboard is read-only.** Actions are commands shown for the user to run. A
one-click "quarantine this test" is one click away from silencing a real regression, and
the measured misclassification rate is not zero.

## Rejected alternatives

**FastAPI plus uvicorn.** Takes runtime dependencies from two to five or more, for six
read-only endpoints.

**Requiring `npm run build` before the dashboard works.** For anyone evaluating the tool
in five minutes, the dashboard would not exist.

**A hosted backend.** The zero-setup promise is the reason anyone installs this at all.

**`recharts` for the timeline.** Added, then removed. One dependency for one chart, when
the timeline is runs grouped by commit with the divergent groups outlined — a row of
coloured boxes. The hand-rolled version is smaller and draws the proof more directly than
a generic chart component would.

**An AI "explain the fix" panel.** Attractive, and it fails two constraints at once. It
needs an API key, which breaks the credential-free path a judge or a new user follows.
And it puts generated prose beside measured evidence in a tool whose entire argument is
evidence over inference; careful labelling would reduce that risk without changing the
fact that the most eye-catching sentence on the page would be the least verifiable one.
