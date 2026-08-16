#!/usr/bin/env python3
"""Audit BioModStack design result contracts for persisted jobs or saved JSON.

The script is deliberately read-only. It can summarize a JSON export from
`/api/designs/by-job/{job_id}` or fetch that endpoint from a local API URL.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlencode
from urllib.request import urlopen


def _key(value: Any, fallback: str = "unsupported") -> str:
    text = str(value or "").strip()
    return text or fallback


def _designs_from_payload(payload: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        designs = payload.get("designs")
        if isinstance(designs, list):
            return [row for row in designs if isinstance(row, Mapping)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    return []


def summarize_design_contracts(designs: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = list(designs)
    by_result_set: Counter[str] = Counter()
    by_contract: Counter[str] = Counter()
    by_stage_family: Counter[str] = Counter()
    by_artifact_class: Counter[str] = Counter()
    by_analyzer: Counter[str] = Counter()
    unsupported_rows: List[Dict[str, Any]] = []

    for row in rows:
        result_set = _key(row.get("result_set"))
        contract = _key(row.get("analysis_contract_id"))
        stage_family = _key(row.get("stage_family"), fallback="unknown")
        artifact_class = _key(row.get("artifact_class"), fallback="unknown")
        by_result_set[result_set] += 1
        by_contract[contract] += 1
        by_stage_family[stage_family] += 1
        by_artifact_class[artifact_class] += 1
        analyzers = row.get("supported_analyzers")
        if isinstance(analyzers, list) and analyzers:
            for analyzer in analyzers:
                by_analyzer[_key(analyzer, fallback="unknown")] += 1
        else:
            by_analyzer["none"] += 1
        if contract == "unsupported" or not (isinstance(analyzers, list) and analyzers):
            unsupported_rows.append({
                "id": row.get("id"),
                "name": row.get("name"),
                "stage_family": row.get("stage_family"),
                "artifact_class": row.get("artifact_class"),
                "result_set": row.get("result_set"),
            })

    return {
        "total": len(rows),
        "by_result_set": dict(sorted(by_result_set.items())),
        "by_contract": dict(sorted(by_contract.items())),
        "by_stage_family": dict(sorted(by_stage_family.items())),
        "by_artifact_class": dict(sorted(by_artifact_class.items())),
        "by_analyzer": dict(sorted(by_analyzer.items())),
        "unsupported_rows": unsupported_rows,
    }


def fetch_designs(api_base_url: str, job_id: str, limit: int) -> List[Mapping[str, Any]]:
    base = api_base_url.rstrip("/")
    query = urlencode({"limit": limit, "include_children": "true"})
    with urlopen(f"{base}/api/designs/by-job/{job_id}?{query}", timeout=20) as response:  # noqa: S310 - operator-provided local URL
        payload = json.loads(response.read().decode("utf-8"))
    return _designs_from_payload(payload)


def load_designs(path: str) -> List[Mapping[str, Any]]:
    with open(path, "r", encoding="utf-8") as handle:
        return _designs_from_payload(json.load(handle))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-file", help="Path to a saved designs API JSON payload")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:18000", help="BioModStack API base URL")
    parser.add_argument("--job-id", help="Job id to fetch from /api/designs/by-job/{job_id}")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args(argv)

    if args.json_file:
        designs = load_designs(args.json_file)
    elif args.job_id:
        designs = fetch_designs(args.api_base_url, args.job_id, args.limit)
    else:
        parser.error("provide --json-file or --job-id")

    print(json.dumps(summarize_design_contracts(designs), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
