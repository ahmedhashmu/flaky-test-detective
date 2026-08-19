# 0008 — Ship a composite GitHub Action

**Status:** Accepted

## Context

The CLI worked, but adopting it in CI meant hand-writing a cache key, a database path, an
ingest step, a triage step and a failure policy. Five chances to get it wrong, for a tool
solving a side-concern that gets exactly one chance to be easy.

Worse, the most important step is the least obvious: **without persisting the database
between runs, the tool sees a single run and can detect nothing at all**, because flakiness
is only visible across runs. Someone following an incomplete example would conclude the
tool does not work.

## Decision

Ship a **composite** action at the repository root.

Composite rather than Docker or JavaScript, for one reason above the others: it is
readable. Every step is YAML that anyone adopting it can inspect. A Docker action asks
teams to run an opaque image in their CI, which for a tool asking to be trusted about
trustworthiness is the wrong first impression. It also avoids a build step and an image
registry.

```yaml
- uses: ahmedhashmu/flaky-test-detective@main
  with:
    report-path: reports/junit.xml
```

Defaults chosen so the obvious usage is the correct one:

| Input | Default | Why |
|---|---|---|
| `cache` | `true` | Without it the tool cannot work at all. Opt out, never opt in. |
| `fail-on` | `regression` | Known flakes must not block a merge; a real break must. |
| `comment` | `true` | The triage summary is worthless if nobody sees it. |

## Consequences

**PR comments update in place.** Found by a hidden HTML marker in the body. A bot that
posts a fresh comment on every push is a bot people mute, and a muted bot reports nothing.

**Outputs let workflows branch.** `exit-code`, `actionable`, `known-flakes`,
`all-known-flaky` and a one-line `summary`, so a workflow can label a PR or notify a
channel without re-parsing anything.

**The tool installs from `github.action_path`**, which is the action's own checkout — so
the version that runs is the commit the workflow pinned, not whatever is on PyPI.

**Runner-agnostic.** The action takes a path to JUnit XML and never assumes pytest. Python
is an implementation detail of the tool, not a constraint on the suite under test.

**`ingest-only` for the default branch.** Accumulate history on `main` without triaging or
failing, then triage on pull requests against that history. Without this the recommended
setup would only ever have one run of baseline.

## Rejected alternatives

**A reusable workflow** (`workflow_call`) instead of an action. More capable, but it takes
over the whole job, and this needs to slot into an existing test job right after the test
step.

**Publishing to the Actions Marketplace.** Requires a release tag and there is no release
yet. `@main` works and is honest about what it is; examples say so rather than implying a
stability guarantee that does not exist.
