"""Executable BC2 campaign handoff, not model admission or scientific result publication.

The typed compiler owns the request. This adapter writes that *request*, not its
resolved settings (which contain native infinity sentinels), for the pinned CLI.
It can prepare on CPU; execution and checkpoint preflight belong to BindCraft2.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

from services.bindcraft2_native import PIN, _canonical


class CampaignContractError(ValueError):
    """A compilation receipt cannot be handed to the native CLI unchanged."""


def prepare_campaign(compiled: dict, destination: Path,
                     native_resolve: Callable[[dict], dict],
                     native_sweep_arms: Callable[[dict], tuple],
                     native_read: Callable[[Path], dict]) -> dict:
    """Prepare one immutable compilation in a job-owned folder, without running GPU code.

    `destination` contains the receipt and CLI input; native output is a separate
    `campaign/` child. Callers materialize target/scaffold paths before compilation.
    The two supplied functions must come from the pinned native settings/sweep modules.
    """
    destination = Path(destination).resolve()
    request = compiled.get("native_request")
    if compiled.get("schema_version") != 1 or compiled.get("upstream_commit") != PIN or not isinstance(request, dict):
        raise CampaignContractError("BC2 compilation version or pinned source mismatch")
    campaign = destination / "campaign"
    if request.get("project_folder") != str(campaign) or request.get("resume") is not False:
        raise CampaignContractError("BC2 campaign folder/resume must bind this output root")
    limit = request.get("max_trajectories")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise CampaignContractError("BC2 campaign requires a finite positive attempt limit")
    # Re-resolve, rather than trusting an arbitrary effective settings blob. The CLI
    # calls read_settings, which resolves paths relative to the settings document.
    # Materialized absolute paths therefore survive that second native read.
    for target in request.get("targets", []):
        if not isinstance(target, dict) or not Path(target.get("target_path", "")).is_absolute():
            raise CampaignContractError("BC2 targets must have materialized absolute paths")
    if request.get("binder_scaffold") and not Path(request["binder_scaffold"]).is_absolute():
        raise CampaignContractError("BC2 scaffold must have a materialized absolute path")
    effective = native_resolve(request)
    if _canonical(effective) != _canonical(compiled.get("effective_settings")):
        raise CampaignContractError("BC2 native settings differ from compilation")
    if hashlib.sha256(_canonical(effective)).hexdigest() != compiled.get("effective_sha256"):
        raise CampaignContractError("BC2 effective settings digest differs from compilation")
    arms = native_sweep_arms(effective) if effective.get("parameter_sweep") else ()
    count = len(arms)
    per_arm = max(1, limit // count) if count else limit
    allowance = per_arm * count if count else limit
    if allowance > limit or compiled.get("sweep_budget") != {"arms": count, "per_arm": per_arm, "aggregate_allowance": allowance}:
        raise CampaignContractError("BC2 native sweep exceeds or differs from compiled attempt budget")
    if "requested_settings" in compiled and hashlib.sha256(_canonical(compiled["requested_settings"])).hexdigest() != compiled.get("request_sha256"):
        raise CampaignContractError("BC2 requested settings digest differs from compilation")
    destination.mkdir(parents=True, exist_ok=True)
    settings_path = destination / "native_settings.json"
    receipt_path = destination / "compilation.json"
    settings_bytes = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    receipt_bytes = _canonical(compiled) + b"\n"
    for path, payload in ((settings_path, settings_bytes), (receipt_path, receipt_bytes)):
        if path.exists() and path.read_bytes() != payload:
            raise CampaignContractError(f"existing BC2 {path.name} differs; allocate a fresh campaign")
    for path, payload in ((settings_path, settings_bytes), (receipt_path, receipt_bytes)):
        if not path.exists():
            path.write_bytes(payload)
    if _canonical(native_read(settings_path)) != _canonical(effective):
        raise CampaignContractError("BC2 CLI settings read differs from compiled effective settings")
    return {"settings_path": str(settings_path), "receipt_path": str(receipt_path),
            "campaign_root": str(campaign), "command": ["bindcraft", "design", str(settings_path)],
            "native_output": {"scope": "campaign/<arm>/ when sweep arms > 0; campaign/ otherwise",
                              "trajectories": "1_Trajectories/!_Trajectories.csv",
                              "refolded": "2_Refolded/!_Refolded.csv",
                              "ranked": "3_Ranked/!_Ranked.csv",
                              "summary": "summary.csv",
                              "presence": "native-dependent; zero-yield and sparse-output do not imply missing execution"},
            "aggregate_attempt_allowance": allowance}


def run_campaign(prepared: dict, *, executable: str = "bindcraft") -> int:
    """Invoke the native CLI exactly once; never synthesize acceptance or outputs."""
    command = [executable, "design", prepared["settings_path"]]
    return subprocess.run(command, check=False).returncode
