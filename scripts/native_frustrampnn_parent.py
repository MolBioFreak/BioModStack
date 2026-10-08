#!/usr/bin/env python3
"""File-only parent expansion and exact native join, shared by both placements.

Nextflow, not this coordinator, executes and owns GPU tasks. No host database
connection or HTTP API is consulted. Native preparation and inference remain
the existing global FrustraMPNN implementation.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform/api'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from component_runtime import canonical_bytes, digest, durable_write, plan_frustrampnn, GroupingLedger, ComponentBoundary, ResultReference
from plan_frustrampnn_groups import materialize_groups, reconcile_tree, tree_authority, immutable_write
from remote_frustrampnn_batch import read_candidate, materialize_batch, prepare_record
from prepare_frustrampnn_candidate import prepare_candidate
from services.frustrampnn.settings import validate_persisted_requested_settings, requested_settings_sha256
from services.frustrampnn.manifests import load_result_manifest, validate_result_manifest
from publish_frustrampnn_bundle import publish, _publish_source


def prepare(source: Path, metadata: dict, settings: dict, origin: str, output: Path):
    required = {'candidate_id', 'parent_job_id', 'parent_workflow_id', 'producer_stage', 'producer_candidate_key', 'requiredness'}
    # Preserve producer IDs (CM and Fold-CP do not use UUID-derived IDs).
    import re
    if not required <= metadata.keys() or metadata['requiredness'] != 'required' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', metadata['candidate_id']):
        raise ValueError('invalid terminal candidate authority')
    from prepare_frustrampnn_candidate import _canonical_relative_key
    _canonical_relative_key(metadata['producer_candidate_key'], field='producer_candidate_key')
    requested = validate_persisted_requested_settings({**settings, 'settings_value_origin': origin})
    output.mkdir()
    # Preserve original bytes for mmCIF/native source publication as well as the
    # normalized PDB. They are not interchangeable scientific artifacts.
    durable_write(output / ('original_source' + source.suffix.lower()), source.read_bytes())
    return prepare_candidate(source=source, output_pdb=output/'canonical_source.pdb',
        request_path=output/'workflow_component_request_v3.json', metadata={k: metadata[k] for k in required},
        request_version=3, structure_map_path=output/'frustrampnn_structure_map_v1.json',
        settings_payload=canonical_bytes(settings), settings_sha256=requested_settings_sha256(requested),
        settings_value_origin=origin)


def run_group(directories, container, gpu, apptainer):
    import run_frustrampnn_component as component
    import run_frustrampnn_grouped_batch as grouped
    if len(directories) == 1:
        root = Path(directories[0])
        request, payloads = read_candidate(root)
        component.run_component(request=request, request_payload=payloads[0],
            source_structure=root/'canonical_source.pdb', structure_map=root/'frustrampnn_structure_map_v1.json',
            output_dir=Path('grouped_results')/request['candidate_id'], container=container,
            physical_gpu_id=gpu, apptainer=apptainer)
    else:
        manifest, batch = materialize_batch(directories, Path.cwd()/'authority')
        owner = Path.cwd()/'receipt_owner'/batch['execution_owner_job_id']
        owner.mkdir(parents=True)
        evidence = grouped.run_grouped_batch(batch_manifest_path=manifest, job_root=owner,
            container=str(container), physical_gpu_id=gpu, apptainer=apptainer, prepare_record=prepare_record)
        if any(r['status'] != 'succeeded' for r in evidence['records']):
            raise ValueError('required FrustraMPNN group failed')
    # Retain grouping-native evidence, not transient work/cache directories.
    receipts = Path('group_receipts')
    receipts.mkdir()
    for path in Path('receipt_owner').glob('*/frustrampnn/batches/*terminal*json'):
        durable_write(receipts/path.name, path.read_bytes())


def seal(prepared, bundles, output: Path, publish_root: Path, attempt: str):
    records = [read_candidate(Path(p))[0] for p in prepared]
    plan = plan_frustrampnn(records, records[0]['requested_settings'])
    by_id = {}
    for raw in bundles:
        root = Path(raw)
        payloads = validate_result_manifest(root, load_result_manifest(root))
        request = json.loads(payloads['workflow_component_request_v3.json'])
        terminal = json.loads(payloads['workflow_component_result_v3.json'])
        cid = request['candidate_id']
        if cid in by_id or terminal['status'] != 'succeeded':
            raise ValueError('duplicate or failed required result')
        by_id[cid] = (root, request, payloads)
    expected = {r['candidate_id']: r for r in records}
    if by_id.keys() != expected.keys():
        raise ValueError('required native result exact join is incomplete')
    for cid, (_, request, _) in by_id.items():
        if request != expected[cid]:
            raise ValueError('native result request authority conflicts')
    output.mkdir(parents=True, exist_ok=True)
    boundary = ComponentBoundary(GroupingLedger(output/'components.sqlite', attempt_id=attempt, plan=plan))
    boundary.ledger.check_active()
    original = {read_candidate(Path(p))[0]['candidate_id']: Path(p) for p in prepared}
    for ordinal, members in enumerate(plan.groups):
        refs = []
        for member in members:
            root, request, payloads = by_id[member.candidate_id]
            source = payloads['normalized_input.pdb']
            if hashlib.sha256(source).hexdigest() != request['source_artifact']['sha256']:
                originals = list(original[member.candidate_id].glob('original_source.*'))
                if len(originals) != 1:
                    raise ValueError('original native source publication bytes missing')
                source = originals[0].read_bytes()
            if hashlib.sha256(source).hexdigest() != request['source_artifact']['sha256']:
                raise ValueError('original source publication digest mismatch')
            _publish_source(payload=source, allowed_root=publish_root, relative_path=request['source_artifact']['relative_path'])
            marker = publish(source_bundle=root, allowed_root=publish_root,
                destination=publish_root/'frustrampnn/results'/member.candidate_id,
                marker=output/f'{member.candidate_id}.published.json')
            refs.append(marker)
            reconcile_tree(root, output/'bundles'/member.candidate_id, tree_authority(root), staging_root=output/'.staging')
        path = output/f'group_{ordinal:06d}.json'
        immutable_write(path, canonical_bytes({'component_id': plan.component_id(ordinal), 'results': refs}))
        boundary.result(ResultReference(plan.component_id(ordinal), path.name, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, 'bms.frustrampnn.native-group.v1'), output)
    boundary.join(output)
    receipt = {'schema_name': 'bms.frustrampnn.native-parent-terminal.v1', 'schema_version': 1,
        'parent_job_id': plan.parent_job_id, 'parent_workflow_id': plan.parent_workflow_id,
        'status': 'complete', 'requiredness': 'required', 'grouping_plan': plan.payload,
        'candidate_ids': [m.candidate_id for g in plan.groups for m in g]}
    receipt['receipt_sha256'] = digest(receipt)
    immutable_write(output/'terminal.json', canonical_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--metadata-base64', required=True)
    p.add_argument('--settings-base64', required=True)
    p.add_argument('--origin', required=True)
    p.add_argument('--output', type=Path, default=Path('prepared'))
    p = sub.add_parser('plan')
    p.add_argument('--candidate', action='append', type=Path, required=True)
    p.add_argument('--attempt', required=True)
    p = sub.add_parser('run')
    p.add_argument('--candidate', action='append', type=Path, required=True)
    p.add_argument('--container', type=Path, required=True)
    p.add_argument('--gpu', type=int, required=True)
    p.add_argument('--apptainer', default='apptainer')
    p = sub.add_parser('seal')
    p.add_argument('--candidate', action='append', type=Path, required=True)
    p.add_argument('--bundle', action='append', type=Path, required=True)
    p.add_argument('--publish-root', type=Path, required=True)
    p.add_argument('--attempt', required=True)
    args = parser.parse_args()
    if args.operation == 'prepare':
        decode = lambda s: json.loads(base64.b64decode(s, validate=True))
        prepare(args.source, decode(args.metadata_base64), decode(args.settings_base64), args.origin, args.output)
    elif args.operation == 'plan':
        materialize_groups(args.candidate, Path('groups'), attempt_id=args.attempt)
    elif args.operation == 'run':
        run_group(args.candidate, args.container, args.gpu, args.apptainer)
    else:
        seal(args.candidate, args.bundle, Path('joined'), args.publish_root, args.attempt)

if __name__ == '__main__':
    main()
