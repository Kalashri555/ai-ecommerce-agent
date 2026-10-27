"""Make the backend's `app` package importable from the MCP server processes.

The MCP servers are separate programs, but they deliberately reuse the same
business services as the FastAPI app (no duplicated product/cart logic).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"

for p in (str(BACKEND_DIR), str(PROJECT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
