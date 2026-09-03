"""Shared pytest fixtures: make the M1 flow modules importable.

The flow modules (gates, arbiter, schemas) are plain top-level modules inside
flows/isabelle_rlcr/ -- hmz loads the flow with runpy and the flow's own
directory on sys.path, so the tests do the same.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FLOW_DIR = REPO_ROOT / "flows" / "isabelle_rlcr"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(FLOW_DIR) not in sys.path:
    sys.path.insert(0, str(FLOW_DIR))
