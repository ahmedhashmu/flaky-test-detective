"""A real data race: unsynchronized read-modify-write across threads.

Not a simulated failure. Several threads read a shared counter, yield, and write
back an incremented value. Without a lock, updates are lost, and how many are lost
varies per run.

Under CPython the GIL makes the window narrower than it would be in other
languages, so the sleep between read and write widens it deliberately. That is
representative rather than artificial: a real version of this bug involves I/O
between the read and the write, which has the same effect.
"""

from __future__ import annotations

import random
import threading
import time

from _demo import DETERMINISTIC

THREADS = 2
"""Two threads, not eight.

With eight the race is so wide that every update is lost and the test fails every
time, which makes it a broken test rather than a flaky one. Two threads with a
short random hold produce a genuine coin flip: sometimes the first write lands
before the second read, sometimes it does not.
"""


def test_counter_increments_are_not_lost() -> None:
    """Genuine lost-update race on an unsynchronized counter."""
    state = {"count": 0}
    guard = threading.Lock() if DETERMINISTIC else None

    def increment() -> None:
        if guard is not None:
            with guard:
                state["count"] += 1
            return

        current = state["count"]
        time.sleep(random.uniform(0.0, 0.003))
        state["count"] = current + 1

    workers = [threading.Thread(target=increment) for _ in range(THREADS)]
    for worker in workers:
        worker.start()
        if not DETERMINISTIC:
            time.sleep(random.uniform(0.0, 0.003))
    for worker in workers:
        worker.join()

    assert state["count"] == THREADS, (
        f"concurrent increments lost updates: expected {THREADS}, got "
        f"{state['count']} -- data race on a shared counter"
    )


def test_append_order_is_stable() -> None:
    """Threads appending to a shared list without ordering guarantees.

    Asserting on order here is the bug, and it is a very common one: the code is
    correct, the assertion is over-specified.
    """
    collected: list[int] = []
    guard = threading.Lock()

    def work(index: int) -> None:
        if not DETERMINISTIC:
            time.sleep(random.uniform(0.0, 0.004))
        with guard:
            collected.append(index)

    # Two threads, giving a roughly even chance of ordered completion. Three made
    # the test fail five runs in six, which is often enough that it never passes
    # during a short hunt and gets reported as `broken` instead of flaky.
    workers = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert collected == sorted(collected), (
        f"threads completed out of order: {collected} -- the assertion depends on "
        "concurrent scheduling"
    )
