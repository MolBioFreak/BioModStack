"""Read native Complexa publication for existing artifact/Project receivers."""
from __future__ import annotations
import json
from pathlib import Path
from scripts.lib.complexa_contract import RESULT_CONTRACT


def read_results(root: Path) -> dict:
    result = json.loads((root / "complexa-results.json").read_text())
    if result.get("contract") != RESULT_CONTRACT:
        raise ValueError("Not a Complexa v1 publication")
    for artifact in result["artifacts"]:
        path = (root / artifact["path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Artifact escapes publication root")
    return result


def candidate_documents(root: Path) -> list[dict]:
    """Do not turn lookaheads/evaluation descendants into invented Design rows."""
    result = read_results(root)
    return result["documents"]


def observation_page(root: Path, *, offset: int = 0, limit: int = 100) -> dict:
    if offset < 0 or limit < 1:
        raise ValueError("Invalid page")
    rows = read_results(root)["observations"]
    return {"items": rows[offset:offset + limit], "total": len(rows),
            "has_more": offset + limit < len(rows)}
