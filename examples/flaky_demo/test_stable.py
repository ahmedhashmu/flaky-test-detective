"""Controls.

Without these the demo proves nothing. A tool that labels everything flaky would
look identical to a working one on a suite made entirely of flakes.

- The `test_stable_*` tests must always pass and must be scored 0.00.
- `test_known_broken` must always fail and must be reported as **broken**, not as
  flaky. That is the distinction that decides whether CI exits 1 or 2, so getting
  it wrong matters more than any scoring nicety.
"""

from __future__ import annotations


def _total(prices: list[int], discount: int = 0) -> int:
    return sum(prices) - discount


def test_stable_sums_prices() -> None:
    assert _total([100, 250, 75]) == 425


def test_stable_applies_discount() -> None:
    assert _total([100, 250, 75], discount=25) == 400


def test_stable_handles_empty_basket() -> None:
    assert _total([]) == 0


def test_stable_rejects_negative_total() -> None:
    assert _total([100], discount=500) == -400


def test_known_broken() -> None:
    """Consistently wrong, never intermittent.

    The expected value is deliberately incorrect. This must be reported as
    `broken`, because it has never passed in recorded history, rather than as a
    flake.
    """
    assert _total([20, 21]) == 42, "expected 42, got 41 -- consistent, not intermittent"
