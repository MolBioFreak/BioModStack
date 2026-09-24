"""Executable BC2 campaign handoff, not model admission or scientific result publication.

The typed compiler owns the request. This adapter writes that *request*, not its
resolved settings (which contain native infinity sentinels), for the pinned CLI.
It can prepare on CPU; execution and checkpoint preflight belong to BindCraft2.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from services.bindcraft2_native import PIN, _canonical, relocate_compilation


class CampaignContractError(ValueError):
    """A compilation receipt cannot be handed to the native CLI unchanged."""


# Native argparse authorities: rank.py:main, campaign_filter.py:main,
# campaign_output.py:main, cli.py:archive_trajectories at PIN.
NATIVE_ACTIONS = ('resume', 'rank', 'filter', 'campaign_output', 'archive', 'unarchive', 'score')


def action_schema() -> dict:
    scope = {'campaign_relative_path': {'type': 'string', 'default': '.', 'control': 'campaign-arm'}}
    common = {**scope, 'table': {'type': 'string', 'enum': ['accepted', 'candidates', 'trajectories']},
              'top': {'type': 'integer', 'default': 20},
              'list': {'type': 'boolean', 'default': False}}
    return {
        'resume': {'source': 'bindcraft/settings.py:resume; bindcraft/campaign.py', 'properties': {}},
        'rank': {'source': 'bindcraft/rank.py:main', 'properties': {
            **common, 'table': {**common['table'], 'default': 'accepted'},
            'on': {'type': 'array', 'items': {'type': 'string'}, 'default': []},
            'direction': {'type': 'string', 'enum': ['native', 'lowest', 'highest'], 'default': 'native'},
            'binder': {'type': 'string', 'default': ''}, 'target': {'type': 'string', 'default': ''},
            'rescore': {'type': 'boolean', 'default': False}}},
        'filter': {'source': 'bindcraft/campaign_filter.py:main', 'properties': {
            **common, 'table': {**common['table'], 'default': 'candidates'},
            'where': {'type': 'array', 'items': {'type': 'object', 'required': ['metric', 'comparison', 'value'],
                'properties': {'metric': {'type': 'string'},
                               'comparison': {'type': 'string', 'enum': ['>=', '<=', '!=', '==', '>', '<', '=']},
                               'value': {'type': 'number'}}}, 'default': []},
            'write_rejected': {'type': 'boolean', 'default': False}}},
        'score': {'source': 'bindcraft/score.py:main', 'required': ['structure_relative_path'], 'properties': {
            'structure_relative_path': {'type': 'string', 'control': 'structure'},
            **{name: {'type': 'string', 'default': ''} for name in ('binder', 'target', 'hotspots', 'coldspots')}}},
        **{name: {'source': 'bindcraft/campaign_output.py; bindcraft/cli.py', 'properties': scope}
           for name in ('campaign_output', 'archive', 'unarchive')},
    }


def validate_action_options(operation: str, options: dict) -> dict:
    """Typed native arguments only, not a scientific verdict or readiness check."""
    import math
    if operation not in NATIVE_ACTIONS:
        raise ValueError('Unknown BC2 native action')

    def check(value, descriptor, path):
        kind = descriptor['type']
        valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
                 'boolean': type(value) is bool, 'integer': type(value) is int,
                 'number': type(value) in (int, float) and math.isfinite(value),
                 'string': isinstance(value, str)}[kind]
        if not valid or ('enum' in descriptor and value not in descriptor['enum']):
            raise ValueError(f'{path}: invalid native {kind} argument')
        if kind == 'object':
            properties = descriptor['properties']
            if set(value) - set(properties) or set(descriptor.get('required', [])) - set(value):
                raise ValueError(f'{path}: unknown or missing native options')
            for key, item in value.items():
                check(item, properties[key], f'{path}.{key}')
        elif kind == 'array':
            for item in value:
                check(item, descriptor['items'], path + '[]')
    check(options, {'type': 'object', **action_schema()[operation]}, operation)
    return options


def native_command(compiled: dict, campaign: Path, settings_path: Path) -> list[str]:
    action = compiled.get('native_action', {})
    operation = action.get('operation', 'campaign')
    if operation in ('campaign', 'resume'):
        return ['bindcraft', 'design', str(settings_path)]
    options = validate_action_options(operation, action.get('options', {}))
    relative = Path(options.get('structure_relative_path' if operation == 'score' else 'campaign_relative_path', '.'))
    selected = (campaign / relative).resolve()
    if relative.is_absolute() or not selected.is_relative_to(campaign.resolve()):
        raise ValueError('Native action input must remain within the owned campaign')
    campaign = selected
    command = ['bindcraft', operation, str(campaign)]
    for key in ('table', 'top', 'binder', 'target', 'hotspots', 'coldspots'):
        if key in options:
            command.extend(['--' + key, str(options[key])])
    for key in ('list', 'rescore'):
        if options.get(key):
            command.append('--' + key)
    for metric in options.get('on', []):
        command.extend(['--on', metric])
    direction = options.get('direction', 'native')
    if direction != 'native':
        command.append('--' + direction + '-first')
    for row in options.get('where', []):
        command.extend(['--where', f"{row['metric']}{row['comparison']}{row['value']}"])
    if options.get('write_rejected'):
        command.extend(['--rejected', str(campaign / 'rejected.csv')])
    return command


def materialize_runtime_compilation(compiled: dict, source_root: Path, destination: Path) -> dict:
    """Relocate a transported tree to writable output, without mutating input.

    The bridge already inventories the preparation tree. Re-entry uses the exact
    execution receipt and leaves progressed native state alone.
    """
    import shutil
    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    if source_root == destination:
        return compiled
    rebound = relocate_compilation(compiled, destination)
    receipt = destination / 'compilation.json'
    if receipt.exists():
        if _canonical(json.loads(receipt.read_text())) != _canonical(rebound):
            raise CampaignContractError('existing BC2 compilation differs from transported request')
        return rebound
    for name in ('sources', 'campaign'):
        folder = source_root / name
        if folder.exists():
            if not folder.resolve().is_relative_to(source_root):
                raise CampaignContractError('BC2 transported folder escapes preparation root')
            for path in folder.rglob('*'):
                if path.is_symlink() and not path.resolve().is_relative_to(source_root):
                    raise CampaignContractError('BC2 transported source escapes preparation root')
            shutil.copytree(folder, destination / name, dirs_exist_ok=True)
    return rebound


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
    if request.get("project_folder") != str(campaign) or type(request.get("resume")) is not bool:
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
            partial = path.with_name(path.name + '.partial')
            partial.write_bytes(payload)
            partial.replace(path)
    if _canonical(native_read(settings_path)) != _canonical(effective):
        raise CampaignContractError("BC2 CLI settings read differs from compiled effective settings")
    return {"settings_path": str(settings_path), "receipt_path": str(receipt_path),
            "campaign_root": str(campaign), "command": native_command(compiled, campaign, settings_path),
            "native_output": {"scope": "campaign/<arm>/ when sweep arms > 0; campaign/ otherwise",
                              "trajectories": "1_Trajectories/!_Trajectories.csv",
                              "refolded": "2_Refolded/!_Refolded.csv",
                              "ranked": "3_Ranked/!_Ranked.csv",
                              "summary": "summary.csv",
                              "presence": "native-dependent; zero-yield and sparse-output do not imply missing execution"},
            "aggregate_attempt_allowance": allowance}


def run_campaign(prepared: dict, *, executable: str = "bindcraft") -> int:
    """Invoke the native CLI exactly once; never synthesize acceptance or outputs."""
    # Scoped Python startup adapter survives the native CLI's fresh interpreter
    # and its subprocess design workers. Settings/receipts remain portable JSON.
    adapter = Path(__file__).resolve().parents[3] / 'scripts/bindcraft2_native_adapter'
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join(
        part for part in (str(adapter), os.environ.get('PYTHONPATH', '')) if part)}
    command = [executable, *prepared.get("command", ["bindcraft", "design", prepared["settings_path"]])[1:]]
    if command[1] == 'score':
        completed = subprocess.run(command, check=False, text=True, capture_output=True, env=environment)
        # The native CLI emits JSON plus explanatory lines, not a pure JSON file.
        (Path(prepared['campaign_root']) / 'native_score.txt').write_text(completed.stdout)
        print(completed.stdout, end='', flush=True)
        if completed.stderr:
            import sys
            print(completed.stderr, end='', file=sys.stderr, flush=True)
        return completed.returncode
    return subprocess.run(command, check=False, env=environment).returncode
