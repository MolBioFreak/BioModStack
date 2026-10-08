#!/usr/bin/env python3
"""Verify fresh authenticated Protenix 5x5 layout evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in '0123456789abcdef' for char in value)


def classify_layout_report(observations_root: Path, identity: dict[str, Any]) -> dict[str, Any]:
    base = observations_root / 'runtime_inventory' / 'protenix_layout_fresh_v3'
    report_path = base / 'validation_report.json'
    meta_path = base / 'run.meta.json'
    supervisor_path = base / 'run_supervisor.py'
    log_path = base / 'stdout_stderr.log'
    input_path = base / 'input.json'
    required = (report_path, meta_path, supervisor_path, log_path, input_path)
    if not all(path.is_file() and not path.is_symlink() for path in required):
        return {'runtime_status':'unmeasured','evidence_tier':'unmeasured','gate_effect':'STOP','rationale':'fresh 5x5 layout evidence is incomplete','refs':[]}
    try:
        report = json.loads(report_path.read_text())
        meta = json.loads(meta_path.read_text())
    except Exception as exc:
        return {'runtime_status':'observed-fail','evidence_tier':'fresh authenticated','gate_effect':'STOP','rationale':f'layout evidence JSON invalid: {type(exc).__name__}: {exc}','refs':[report_path]}
    valid = (
        report.get('schema') == 'phase0-protenix-layout-authenticated-v3'
        and report.get('vector_id') == 'P0-PROTENIX-LAYOUT-001'
        and report.get('result') == 'PASS'
        and report.get('seeds') == [101,202,303,404,505]
        and report.get('samples') == [0,1,2,3,4]
        and report.get('prediction_count') == 25
        and report.get('cif_count') == 25
        and report.get('summary_confidence_count') == 25
        and report.get('full_data_count') == 25
        and report.get('all_files_parsed') is True
        and meta.get('schema') == 'phase0-protenix-layout-supervised-run-v1'
        and meta.get('vector_id') == 'P0-PROTENIX-LAYOUT-001'
        and meta.get('exit_code') == 0
        and isinstance(meta.get('elapsed_seconds'), (int, float))
        and meta.get('elapsed_seconds', 0) > 0
        and isinstance(meta.get('command'), list)
    )
    command = meta.get('command') if isinstance(meta.get('command'), list) else []
    expected_args = {
        '--input': '/evidence/input.json', '--out_dir': '/evidence/output',
        '--model_name': 'protenix-v2', '--seeds': '101,202,303,404,505',
        '--sample': '5', '--use_default_params': 'true', '--cycle': '10',
        '--step': '200', '--use_msa': 'false', '--use_template': 'false',
        '--use_rna_msa': 'false'
    }
    valid = valid and command[:2] == ['apptainer', 'exec']
    for flag, expected_value in expected_args.items():
        valid = valid and flag in command and command.index(flag) + 1 < len(command)
        if flag in command and command.index(flag) + 1 < len(command):
            valid = valid and command[command.index(flag) + 1] == expected_value
    rows = report.get('rows')
    valid = valid and isinstance(rows, list) and len(rows) == 25
    refs = [report_path, meta_path, supervisor_path, log_path, input_path]
    seen = set()
    artifact_relpaths = set()
    expected_keys = {(seed, sample) for seed in [101,202,303,404,505] for sample in range(5)}
    if isinstance(rows, list):
        for row in rows:
            valid = valid and isinstance(row, dict) and row.get('parsed') is True and isinstance(row.get('atom_count'), int) and row['atom_count'] > 0
            key = (row.get('seed'), row.get('sample')) if isinstance(row, dict) else None
            valid = valid and key not in seen
            seen.add(key)
            artifacts = row.get('artifacts') if isinstance(row, dict) else None
            valid = valid and isinstance(artifacts, list) and len(artifacts) == 3
            if isinstance(artifacts, list):
                for artifact in artifacts:
                    raw = artifact.get('path') if isinstance(artifact, dict) else None
                    expected = artifact.get('sha256') if isinstance(artifact, dict) else None
                    if not isinstance(raw, str) or not raw.startswith('/evidence/') or not isinstance(expected, str):
                        valid = False
                        continue
                    path = base / raw.removeprefix('/evidence/')
                    artifact_relpaths.add(raw)
                    try:
                        path.resolve(strict=True).relative_to(base.resolve(strict=True))
                        content = path.read_text(encoding='utf-8')
                        if path.suffix == '.json':
                            json.loads(content)
                        else:
                            valid = valid and '_atom_site.' in content and '\nATOM ' in content
                    except Exception:
                        valid = False
                    valid = valid and path.is_file() and not path.is_symlink() and _sha(path) == expected and path.stat().st_size > 0
                    refs.append(path)
    expected_artifacts = set()
    for seed, sample in expected_keys:
        prefix = f'/evidence/output/small_protein/seed_{seed}/predictions/small_protein'
        expected_artifacts.update({
            f'{prefix}_sample_{sample}.cif',
            f'{prefix}_summary_confidence_sample_{sample}.json',
            f'{prefix}_full_data_sample_{sample}.json',
        })
    valid = valid and seen == expected_keys and artifact_relpaths == expected_artifacts
    protenix = identity.get('runtime_files', {}).get('protenix_image', {})
    checkpoint = identity.get('runtime_files', {}).get('protenix_v2_checkpoint', {})
    wrapper = identity.get('source_files', {}).get('scripts/run_protenix_inference.py', {})
    producer = meta.get('identity') if isinstance(meta.get('identity'), dict) else {}
    valid = valid and protenix.get('available') is True and checkpoint.get('available') is True and wrapper.get('available') is True
    valid = valid and _valid_sha(protenix.get('sha256')) and _valid_sha(checkpoint.get('sha256')) and _valid_sha(wrapper.get('sha256'))
    valid = valid and _valid_sha(producer.get('image', {}).get('sha256')) and producer.get('image', {}).get('sha256') == protenix.get('sha256')
    valid = valid and _valid_sha(producer.get('checkpoint', {}).get('sha256')) and producer.get('checkpoint', {}).get('sha256') == checkpoint.get('sha256')
    valid = valid and _valid_sha(producer.get('wrapper', {}).get('sha256')) and producer.get('wrapper', {}).get('sha256') == wrapper.get('sha256')
    valid = valid and producer.get('input', {}).get('sha256') == _sha(input_path)
    valid = valid and producer.get('supervisor', {}).get('sha256') == _sha(supervisor_path)
    valid = valid and producer.get('stdout_stderr', {}).get('sha256') == _sha(log_path)
    valid = valid and producer.get('stdout_stderr', {}).get('bytes') == log_path.stat().st_size
    valid = valid and str(protenix.get('path')) in command
    if not valid:
        return {'runtime_status':'observed-fail','evidence_tier':'fresh authenticated','gate_effect':'STOP','rationale':'fresh Protenix 5x5 layout evidence failed validation','refs':refs}
    return {'runtime_status':'passed','evidence_tier':'fresh authenticated','gate_effect':'PASS','rationale':'fresh Protenix layout produced and parsed 25 CIF + 25 summary-confidence + 25 full-data sidecars over five seeds and five samples','refs':refs}
