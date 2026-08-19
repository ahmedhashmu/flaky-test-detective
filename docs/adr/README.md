# Decision records

Short records of the decisions that shaped this tool, and why.

Several of them document a rule that was **wrong first**, with the measurement that
exposed it. Those are the useful ones. A design document that only records what was
decided reads like it was obvious; recording what was tried, what the numbers said, and
what changed is the part that transfers.

| # | Decision | Status |
|---|---|---|
| [0001](0001-same-commit-divergence.md) | Same-commit divergence as the primary signal | Accepted |
| [0002](0002-reconstruct-pytest-node-ids.md) | Reconstruct pytest node ids rather than trusting the XML | Accepted |
| [0003](0003-analysis-layer-is-pure.md) | The analysis layer takes data, not a database | Accepted |
| [0004](0004-order-dependence-needs-a-polluter.md) | Order dependence requires naming a polluter | **Superseded 0004a, 0004b** |
| [0005](0005-content-addressed-runs.md) | Content-addressed run identity | Accepted |
| [0006](0006-streak-beats-chance.md) | A failure streak must beat the test's own baseline | Accepted |
| [0007](0007-measure-our-own-accuracy.md) | Measure accuracy against ground truth | Accepted |
| [0008](0008-composite-github-action.md) | Ship a composite GitHub Action | Accepted |

## Format

Each record states the context, the decision, the consequences, and — where relevant —
what was tried before and why it failed. Deliberately short. A decision record nobody
reads has no value, and length is the main reason they go unread.
