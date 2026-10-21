"""Print safe read-only connectivity and required-schema diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.database.connectivity_check import check_postgres_connectivity


def main() -> int:
    result = check_postgres_connectivity(settings.DATABASE_URL)
    print(json.dumps(result, sort_keys=True))
    if not result.get("connected"):
        return 1
    return 0 if result.get("schema_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
