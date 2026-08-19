# Requirements: Product Layer

Third spec for this project.

- [`../flaky-test-detective/`](../flaky-test-detective/) built the detector.
- [`../accuracy-and-adoption/`](../accuracy-and-adoption/) made it measurable and
  adoptable.
- This one makes it legible.

## Why a third spec

The tool at the end of the second round was, in one sentence, an impressive CLI. It
measured its own false-alarm rate at 0.0%, gated CI, merged history across machines,
and attributed flakiness to a commit. Every one of those capabilities was reachable
only by someone already reading a terminal.

That is a real gap, not a cosmetic one. The person who most needs this tool is the
one whose build just went red and who is about to hit "re-run" for the third time.
They are not going to install a CLI first. And the second audience — whoever decides
whether the team adopts it — needs a number they can act on, not a table of
per-verdict F1 scores.

### Gap 1: the strongest capability is the least visible

`triage` already separates known flakes from genuine breakage, and deliberately
excludes the run being triaged from its own history so a first-time failure cannot
use the evidence of itself. That is the most valuable thing the tool does. It is
currently a line of console output.

### Gap 2: there is no answer to "is my CI healthy"

`flaky stats` reports rows and `flaky analyze` reports verdicts. Neither answers the
question a tech lead actually asks, which is whether the suite can be believed right
now, and what it is costing.

### Gap 3: diagnosis stops short of the workflow

The tool can say a test is order-dependent, name the likely polluter, quantify the
correlation, and suggest a remediation. Then it stops. Getting that into a tracker or
a chat channel is copy-paste and re-typing, which in practice means it does not
happen.

### The risk this spec has to avoid

A dashboard is where honesty about weak evidence goes to die. A prettier interface
invites rounding a hint up to a fact, because a confident number looks better in a
card than a caveat does. The product steering names the worst failure mode as a false
"this is flaky" on a real regression, and the fastest way to cause it is a UI that
renders a pattern match and a measurement in the same typeface.

So the constraint on this round is stronger than "look good": **the dashboard must be
harder to misread than the CLI, not easier.**

## Functional requirements

### FR1 — Suite-level health

- **FR1.1** A single 0–100 score answering "can I trust my CI right now", with a
  one-word band.
- **FR1.2** Built only from figures the tool already collects. No fitted model, no
  learned weights, no coefficient that exists because it made the demo look better.
- **FR1.3** Every deducted point attributed to a **named component with a sentence
  of reasoning**. The component penalties must account for the entire deduction.
- **FR1.4** The gap between the exact deduction and the displayed whole-number score
  must itself be visible, so rounding cannot be mistaken for an undisclosed
  adjustment.
- **FR1.5** Missing commit data must cost points, because benchmarked it takes the
  false-alarm rate from 0% to 25%. Verdicts really are weaker without it.
- **FR1.6** An estimate of CI time lost to flaky failures, labelled as an estimate
  wherever it appears, with its assumption stated in full.

### FR2 — A local dashboard

- **FR2.1** `flaky serve` opens it. No build step, no Node toolchain, no container.
- **FR2.2** **Overview**: trust score, headline counts, and a ranked table carrying
  the counts behind each verdict rather than only the score.
- **FR2.3** **Investigation page** for one test, in four sections: evidence,
  timeline, why, action.
- **FR2.4** Evidence must be split into **proven** (same-commit divergence,
  runner-recorded retries, polluter correlation) and **inferred** (flip rate,
  message pattern), rendered differently, so the weaker signal cannot borrow the
  authority of the stronger.
- **FR2.5** Read-only. Anything that changes state is a command shown for the user
  to run and review.
- **FR2.6** Every number must come from the same `analyze()` the CLI calls. The
  dashboard must be incapable of showing a verdict the terminal would not.
- **FR2.7** The caveats the CLI prints must appear here too — short history, thin
  evidence, absent commit data.
- **FR2.8** Bind loopback by default. Binding anything else must warn, in the
  terminal, that test names and failure messages become readable by anyone who can
  reach the port.

### FR3 — Handoff to the workflow

- **FR3.1** `flaky issue <test>` renders an issue body or chat message from the real
  diagnosis: verdict, counts, divergence, polluter, remediation, attribution.
- **FR3.2** Formats for GitHub, Jira, Slack, plain Markdown, and JSON.
- **FR3.3** It **emits; it never posts.** No API client, no token, no webhook
  configuration. Output goes to stdout to be piped.

## Non-functional requirements

- **NFR1** Runtime dependencies stay at **two**: `typer` and `rich`. A web framework
  is not worth an install failure on a side-concern tool.
- **NFR2** The judge path stays credential-free. Nothing in this round may require
  an API key, an account, or network access.
- **NFR3** `flaky serve` must work from a plain `pip install` with no Node present.
- **NFR4** The committed bundle must be provably current, or the convenience in NFR3
  becomes a way to ship a dashboard rendering stale data.
- **NFR5** `analysis/` stays pure. Health scoring takes an `AnalysisReport` and
  returns a value; it does not read the filesystem or the database.
- **NFR6** `report/` and the API layer must not compute. If the dashboard needs a
  derived number, it belongs in `analysis/`, or the terminal and the browser will
  drift apart.
- **NFR7** Failure messages are untrusted text from CI artifacts, and the dashboard
  renders them in a browser. Content Security Policy and escaping are required, and
  static asset serving must not be escapable with a crafted path.

## Out of scope

- **Login, organizations, billing, user management.** Single-user local viewer.
- **A hosted backend.** The zero-setup promise is the reason anyone installs this.
- **Write actions from the browser.** A one-click "quarantine" is one click away from
  silencing a real regression.
- **An AI explanation feature.** Considered and deliberately dropped: it would need
  an API key, which breaks NFR2, and this project's whole argument is evidence over
  inference. Adding a language model to the output of a tool that exists to replace
  guesswork is a bad trade even when the separation is well marked.
- **More detection algorithms.** This round adds no new signal. That is the point of
  calling it a thin layer.

## Acceptance criteria

1. `flaky serve` works from a clean clone with no `npm` step.
2. The trust score's component penalties account for the whole deduction, and the
   arithmetic from components to displayed score is shown, not implied.
3. A test asserts the API payload's verdicts and scores match `analyze()` exactly.
4. The investigation page separates proven evidence from inferred signals, and a test
   asserts the split.
5. `flaky issue` produces a body containing the real diagnosis for a test the tool
   genuinely detected, with no credential supplied.
6. CI rebuilds the frontend and fails if the committed bundle differs.
7. Runtime dependencies are still exactly two.
