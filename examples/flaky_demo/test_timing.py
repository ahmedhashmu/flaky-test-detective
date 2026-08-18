"""Timing flakes: a real worker thread racing a real deadline.

The failures here are genuine, not simulated. A thread sleeps for a jittery
interval and the test waits a fixed amount for it. Sometimes the wait is long
enough and sometimes it is not, which is exactly the shape of the most common
flake in real suites: a timeout tuned on a fast laptop and then run on a loaded CI
box.
"""

from __future__ import annotations

import random
import threading
import time

from _demo import DETERMINISTIC, slack


def test_worker_finishes_within_deadline() -> None:
    """Classic under-tuned timeout. Genuinely races."""
    finished: list[str] = []

    def work() -> None:
        time.sleep(random.uniform(0.005, 0.040))
        finished.append("done")

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout=slack(0.020, 2.0))

    assert finished, "worker did not finish within the 20ms deadline (timed out)"


def test_token_still_valid_at_check_time() -> None:
    """Time-boundary dependence: the check straddles an expiry instant.

    The work interval is drawn to sit either side of the token lifetime, so the
    boundary is genuinely crossed about half the time.
    """
    lifetime = slack(0.030, 5.0)
    expires_at = time.monotonic() + lifetime

    work = 0.001 if DETERMINISTIC else random.uniform(0.018, 0.042)
    time.sleep(work)

    remaining = expires_at - time.monotonic()
    assert remaining > 0, (
        f"token expired before it was checked, {abs(remaining) * 1000:.1f}ms past "
        "the expiry boundary -- the assertion depends on wall-clock timing"
    )


def test_batch_completes_before_poll_interval() -> None:
    """Several workers against one deadline, so the failure rate is higher and the
    tool has a clearly-flakier test to rank above the others."""
    done: list[int] = []
    lock = threading.Lock()

    def work(index: int) -> None:
        time.sleep(random.uniform(0.001, 0.030))
        with lock:
            done.append(index)

    workers = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(4)]
    for worker in workers:
        worker.start()

    deadline = slack(0.018, 2.0)
    for worker in workers:
        worker.join(timeout=deadline)

    if DETERMINISTIC:
        for worker in workers:
            worker.join(timeout=2.0)

    assert len(done) == 4, f"only {len(done)} of 4 workers finished before the deadline"
