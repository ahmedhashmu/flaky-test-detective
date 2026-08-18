"""Order-dependent flakes caused by module-level state.

This is the category worth detecting properly, because the usual reflex -- adding
a retry -- does not fix it. The test is not racing anything; it is reading state
that another test left behind. Retrying it in the same process fails again.

`_REGISTRY` below stands in for the real-world version of this bug: a module-level
cache, a configured singleton, a connection pool, a Django settings override that
was never undone.

These tests only flake when the execution order changes between runs, which is
what `flaky hunt --shuffle` provokes and what makes the position-separation
statistic meaningful.
"""

from __future__ import annotations

from _demo import DETERMINISTIC

_REGISTRY: dict[str, str] = {}
"""Deliberately module-level, and deliberately never cleaned up."""


def test_registers_session() -> None:
    """The polluter. Passes every time, which is what makes it hard to blame."""
    _REGISTRY["session"] = "leaked-token"
    assert _REGISTRY["session"] == "leaked-token"


def test_expects_clean_registry() -> None:
    """The victim. Fails if and only if the polluter ran first."""
    if DETERMINISTIC:
        _REGISTRY.clear()

    assert "session" not in _REGISTRY, (
        "registry already contains 'session' -- state left behind by another test"
    )


def test_counts_registered_sessions() -> None:
    """A second victim, so the polluter shows up as a shared cause rather than a
    one-off. Fails with a different message than the first victim, which exercises
    per-test signature tracking."""
    if DETERMINISTIC:
        _REGISTRY.clear()

    assert len(_REGISTRY) == 0, f"expected an empty registry, found {len(_REGISTRY)} entries"
