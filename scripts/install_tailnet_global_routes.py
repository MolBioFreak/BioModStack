#!/usr/bin/env python3
"""Install BioModStack global Tailnet routes without touching the selected root."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biomodstack_tailnet import ensure_global_tailnet_routes  # noqa: E402


def main() -> int:
    snapshot = ensure_global_tailnet_routes()
    print(json.dumps({
        "tailnet_origin": snapshot.origin,
        "serve_root_proxy": snapshot.root_proxy,
        "global_handlers": {
            path: handler
            for path, handler in snapshot.handlers.items()
            if path != "/"
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
