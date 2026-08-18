"""Flakes from unseeded randomness.

Real, not simulated: the test draws a genuine random sample and asserts something
that is only usually true.

Note the interaction with `pytest-randomly`, which reseeds `random` per test from
the run's seed. That makes each individual run reproducible while still varying
between runs, which is precisely the behaviour that makes this class of flake so
confusing in practice: every failure is reproducible with the right seed, and
nobody records the seed.
"""

from __future__ import annotations

import random

from _demo import DETERMINISTIC


def test_sample_includes_the_sentinel() -> None:
    """A 25% chance of failure from a genuine random draw."""
    population = list(range(20))
    if DETERMINISTIC:
        sample = population[:5]
    else:
        sample = random.sample(population, 5)

    assert 0 in sample, f"random sample {sorted(sample)} did not include the sentinel value 0"


def test_shuffle_keeps_first_element_first() -> None:
    """An over-specified assertion about shuffled data."""
    items = ["alpha", "beta", "gamma", "delta"]
    shuffled = list(items)
    if not DETERMINISTIC:
        random.shuffle(shuffled)

    assert shuffled[0] == "alpha", (
        f"shuffled order was {shuffled} -- assertion depends on random ordering"
    )
