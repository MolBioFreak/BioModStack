#!/usr/bin/env python3
"""Worker-local grouping of canonical prepared v3 requests; never submits jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_frustrampnn_grouped_batch as grouped
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.manifests import validate_v3_input_closure
from component_runtime import ordered_candidates, plan_frustrampnn

FILES = ("workflow_component_request_v3.json", "canonical_source.pdb", "frustrampnn_structure_map_v1.json")


def read_candidate(directory):
    directory = Path(directory)
    payloads = tuple((directory / name).read_bytes() for name in FILES)
    request = json.loads(payloads[0])
    if canonical_json_bytes(request) != payloads[0]:
        raise ValueError("request is not canonical JSON")
    validate_v3_input_closure(request, payloads[1], payloads[2])
    return request, payloads


def prepare_record(record, *, authority_root, work_root):
    """Validate the producer-provenance v3 closure before predict_batch executes."""
    paths = [authority_root / record[f"{kind}_relative_path"] for kind in ("request", "source", "structure_map")]
    payloads = [path.read_bytes() for path in paths]
    for kind, payload in zip(("request", "source", "structure_map"), payloads, strict=True):
        if len(payload) != record[f"{kind}_size_bytes"] or hashlib.sha256(payload).hexdigest() != record[f"{kind}_sha256"]:
            raise ValueError("remote batch byte binding mismatch")
    request = json.loads(payloads[0])
    if canonical_json_bytes(request) != payloads[0] or request['candidate_id'] != record['candidate_id'] or request['invocation_id'] != record['invocation_id']:
        raise ValueError("remote batch identity mismatch")
    validate_v3_input_closure(request, payloads[1], payloads[2])
    staged = work_root / f"{record['ordinal']:04d}_{hashlib.sha256(record['candidate_id'].encode()).hexdigest()[:16]}.pdb"
    staged.write_bytes(payloads[1])
    return grouped.PreparedCandidate(record, paths[0], paths[1], paths[2], staged)


def materialize_batch(directories, authority_root):
    from plan_frustrampnn_groups import immutable_write
    candidates = ordered_candidates([read_candidate(path) for path in directories], lambda item: item[0])
    if not candidates:
        raise ValueError("remote batch has no candidates")
    first = candidates[0][0]
    settings = first['requested_settings']
    size = settings['structures_per_job']
    if not 1 <= len(candidates) <= (size if settings['batching_enabled'] else 1):
        raise ValueError("remote batch cardinality disagrees with settings")
    ids = [request['candidate_id'] for request, _ in candidates]
    plan = plan_frustrampnn([request for request, _ in candidates], settings)
    plan.require_groups([ids])
    records = []
    for ordinal, (request, payloads) in enumerate(candidates):
        if any(request[key] != first[key] for key in ('parent_job_id', 'parent_workflow_id', 'requested_settings', 'requested_settings_sha256')):
            raise ValueError("remote batch mixes owners or requested settings")
        record = dict(record_schema_name='bms_frustrampnn_scheduler_record', record_schema_version=2,
                      ordinal=ordinal, candidate_id=request['candidate_id'], invocation_id=request['invocation_id'])
        for kind, folder, name, payload in zip(('request', 'source', 'structure_map'), ('requests', 'sources', 'maps'), FILES, payloads, strict=True):
            relative = f"inputs/{folder}/{ordinal:04d}/{name}"
            path = authority_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            immutable_write(path, payload)
            record.update({f'{kind}_relative_path': relative, f'{kind}_sha256': hashlib.sha256(payload).hexdigest(), f'{kind}_size_bytes': len(payload)})
        records.append(record)
    batch = dict(schema_name='bms_frustrampnn_scheduler_batch', schema_version=3,
                 execution_owner_job_id=first['parent_job_id'], batching_enabled=settings['batching_enabled'],
                 structures_per_job=size, settings_sha256=first['requested_settings_sha256'],
                 expected_cardinality=len(records), records=records)
    manifest = authority_root / 'batches' / 'batch.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    immutable_write(manifest, canonical_json_bytes(batch))
    return manifest, batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', action='append', required=True, type=Path)
    parser.add_argument('--container', required=True)
    parser.add_argument('--physical-gpu-id', required=True, type=int)
    parser.add_argument('--apptainer', default='apptainer')
    args = parser.parse_args()
    if os.environ.get('BMS_REMOTE_EXECUTION') != '1':
        raise ValueError('remote execution envelope required')
    manifest, batch = materialize_batch(args.candidate_dir, Path.cwd() / 'authority')
    # Each Nextflow batch has its own task-local receipt root; no child job exists.
    root = Path.cwd() / 'receipt_owner' / batch['execution_owner_job_id']
    root.mkdir(parents=True)
    evidence = grouped.run_grouped_batch(batch_manifest_path=manifest, job_root=root,
        container=args.container, physical_gpu_id=args.physical_gpu_id,
        apptainer=args.apptainer, prepare_record=prepare_record)
    receipt_id = hashlib.sha256(manifest.read_bytes()).hexdigest()
    receipts = Path('batch_receipts') / receipt_id
    receipts.mkdir(parents=True)
    shutil.copyfile(manifest, receipts / 'batch_manifest_v3.json')
    shutil.copyfile(root / 'frustrampnn/batches/grouped_batch_terminal_receipt_v1.json', receipts / 'grouped_batch_terminal_receipt_v1.json')
    if any(item['status'] != 'succeeded' for item in evidence['records']):
        raise ValueError('required remote FrustraMPNN batch contains failed candidates')


if __name__ == '__main__':
    main()
