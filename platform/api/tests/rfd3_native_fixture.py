"""TEST native files passed through the real manifest producer, not inference."""
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.rfd3_local_redesign.contract import build_request, canonical_json, request_sha256, write_request


def write_native_result(tmp_path, *, job_id, request_id, trajectories=False):
    source = tmp_path / 'inputs' / f'{job_id}-source.pdb'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 10.00           C\nEND\n')
    request = build_request(dict(input_structure=str(source), redesign_mode='partial_diffusion',
        design_chains=['A'], redesign_ranges='A1',
        source_residue_identities=[{'chain_id': 'A', 'residues': [{'res_num': 1, 'insertion_code': '', 'residue_name': 'GLY'}]}],
        num_designs=1, sequence_policy='skip', dump_trajectories=trajectories, write_full_json=True),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    root = tmp_path / 'results' / job_id
    collected = root / 'collected' / 'protein_local_redesign'
    collected.mkdir(parents=True)
    native_root = root / 'run' / 'rfd3'
    native_root.mkdir(parents=True)
    native_request = root / 'requests' / 'request.json'
    native_request.parent.mkdir()
    write_request(native_request, request)
    candidate = native_root / 'protein_local_redesign_0_0_model_0.cif.gz'
    from tests.test_core_protein_candidates import TOY_CIF
    candidate.write_bytes(gzip.compress(TOY_CIF.encode(), mtime=0))
    metadata = native_root / 'protein_local_redesign_0_0_model_0.json'
    metadata.write_text(json.dumps({'summary_confidences': {'test': 0.75}}))
    design_id = 'protein_local_redesign_0'
    native = dict(request['rfd3'], input=source.name)
    native_input = collected / 'rfd3_input_protein_local_redesign_0.json'
    native_input.write_text(canonical_json({design_id: native}) + '\n')
    receipt = collected / 'rfd3_preparation_receipt.json'
    receipt.write_text(json.dumps(dict(schema='bms.rfd3.local-redesign.preparation-receipt.v1',
        request_sha256=request_sha256(request), native_input_sha256=hashlib.sha256(canonical_json({design_id: native}).encode()).hexdigest(),
        runtime_input={'path': source.name, 'sha256': request['input']['sha256']}, design_id=design_id,
        redesign_mode=request['redesign_mode'], sequence_policy=request['sequence_policy'],
        sequence_design={'state': 'not_requested'}, native_rfd3=native)))
    log = native_root / 'producer.log'; log.write_text('TEST producer log\n')
    index = native_root / 'metadata.jsonl'; index.write_text('{}\n')
    output = collected / 'rfd3_result_manifest.json'
    command = [sys.executable, str(Path(__file__).resolve().parents[3] / 'scripts/rfd3_local_redesign/build_result_manifest.py'),
        '--request', str(native_request), '--cif-file', str(candidate), '--json-file', str(metadata),
        '--native-input', str(native_input), '--native-input-storage-path', str(native_input),
        '--preparation-receipt', str(receipt), '--preparation-receipt-storage-path', str(receipt),
        '--log-file', str(log), '--metadata-jsonl', str(index), '--output', str(output),
        '--storage-root', str(native_root), '--request-storage-path', str(native_request),
        '--source-file', str(source), '--source-storage-path', str(source)]
    if trajectories:
        folder = native_root / 'trajectories'; folder.mkdir()
        for role in ('denoised', 'noisy'):
            path = folder / f'protein_local_redesign_0_0_{role}_model_0.cif.gz'
            path.write_bytes(candidate.read_bytes())
            command.extend(['--trajectory-file', str(path)])
    subprocess.run(command, check=True)
    manifest = json.loads(output.read_text())
    return request, request_sha256(request), manifest['manifest_sha256'], root
