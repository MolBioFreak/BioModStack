"""Artifact-bound receipts for the existing RF3/RFD3 filters, not final results."""
import hashlib
import json
from pathlib import Path

STAGES = {'rf3_prediction_filter', 'rfd3_backbone_filter'}
DISPOSITIONS = ('passed', 'rejected_threshold', 'unevaluable_missing', 'invalid_evidence')


def artifact(root, path):
    raw = path.read_bytes()
    return {'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def snapshot_inputs(filter_instance, root):
    """Parse the exact copied bytes retained in the task receipt, not mutable inputs."""
    inputs = root / 'inputs'
    inputs.mkdir(parents=True, exist_ok=False)
    sources = filter_instance.find_structure_files()
    paths = set(sources)
    for source in sources:
        metadata = filter_instance.find_metadata_file(source)
        if metadata is not None:
            paths.add(metadata)
    for source in sorted(paths):
        target = inputs / source.name
        if target.exists():
            raise ValueError('ambiguous filter input basename')
        target.write_bytes(source.read_bytes())
    filter_instance.input_dir = inputs


def publish_invocation(filter_instance, results, jsonl_path, root, stage_id, job_id, task_id):
    if stage_id not in STAGES:
        raise ValueError('unsupported filter stage')
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
    if len(rows) != len(results):
        raise ValueError('filter outcome set changed')
    counts = dict.fromkeys(DISPOSITIONS, 0)
    (root / 'passed').mkdir()
    for row, result in zip(rows, results):
        source = Path(result['file'])
        metadata = filter_instance.find_metadata_file(source)
        published = None
        if result['passed']:
            output = Path(result['published_file'])
            target = root / 'passed' / output.name
            if target.exists():
                raise ValueError('ambiguous filter publication basename')
            target.write_bytes(output.read_bytes())
            published = artifact(root, target)
        row['artifacts'] = {'structure': artifact(root, source),
                            'metadata': artifact(root, metadata) if metadata else None,
                            'published_structure': published}
        # Retain invalid native JSON too, without pretending it was parsed.
        row['source_sha256'] = row['artifacts']['metadata']['sha256'] if metadata else None
        counts[row['disposition']] += 1
    counts['input_count'] = len(rows)
    outcomes = root / 'outcomes.jsonl'
    outcomes.write_text(''.join(json.dumps(row, allow_nan=False) + '\n' for row in rows))
    # Existing JSONL consumers see the same full dispositions and bindings.
    jsonl_path.write_bytes(outcomes.read_bytes())
    receipt = {'schema': 'bms.rf-filter.invocation.v1', 'stage_id': stage_id,
               'job_id': job_id, 'task_id': task_id, 'counts': counts,
               'outcomes': artifact(root, outcomes)}
    (root / 'invocation.json').write_text(json.dumps(receipt, allow_nan=False, sort_keys=True))
