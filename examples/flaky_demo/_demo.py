"""Shared switch for the demo suite.

Every test in this directory is *genuinely* nondeterministic: real threads, real
clocks, real sockets, real unseeded randomness. Nothing here fakes a failure with
a coin flip on a hardcoded list, because a tool that detects simulated flakes has
not been shown to detect anything.

That creates one problem: this repository's own CI would be permanently red. The
switch below makes every test deterministic when
`FLAKY_DEMO_DETERMINISTIC=1` is set, which is what this project's CI does. Leave it
unset to see the flakes.
"""

from __future__ import annotations

import os

DETERMINISTIC = os.environ.get("FLAKY_DEMO_DETERMINISTIC") == "1"
"""When set, every test in this suite behaves deterministically and passes.

Set by this project's own CI so that the demo suite does not break the build it
is shipped in.
"""


def slack(unstable: float, stable: float) -> float:
    """Pick a timing budget: tight enough to race, or generous enough not to."""
    return stable if DETERMINISTIC else unstable
