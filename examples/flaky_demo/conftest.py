"""Makes `_demo` importable from the test modules.

The demo suite is intentionally not a package: it should look like an ordinary
test directory someone would point the tool at, not like part of
flaky-test-detective.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
