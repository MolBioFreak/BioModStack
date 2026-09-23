#!/usr/bin/env python3
"""Selected-candidate, sequence-only complex cofold with ESMFold2.

The candidate structure is a sequence source only. Neither candidate coordinates,
interface-derived pockets nor templates enter the predictor. Target sequence comes
from the declared independent target structure, not the candidate complex.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from run_esmfold2_inference import parse_pdb_polymer_components


_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _source(path: Path, chain_ids: list[str], *, role: str) -> list[dict]:
    if not chain_ids or len(set(chain_ids)) != len(chain_ids) or any(not isinstance(x, str) or not x for x in chain_ids):
        raise ValueError(f"{role} chains must be distinct explicit chain IDs")
    components = parse_pdb_polymer_components(path, chain_ids=chain_ids, include_dna_rna=False)
    by_id = {component['id']: component for component in components if component['type'] == 'protein'}
    if set(by_id) != set(chain_ids):
        raise ValueError(f"{role} protein chains disagree: requested {chain_ids}, observed {sorted(by_id)}")
    return [by_id[chain] for chain in chain_ids]


def compile_selected_inputs(manifest: dict, staged_dir: Path, target_pdb: Path) -> list[dict]:
    """Compile exact selected identities into sequence-only native SPI components."""
    if set(manifest) != {'schema_version', 'target_chains', 'candidates'} or manifest['schema_version'] != 1:
        raise ValueError('Expected blind pose selection manifest v1')
    rows = manifest['candidates']
    if not isinstance(rows, list) or not rows:
        raise ValueError('Select at least one candidate')
    target_chains = manifest['target_chains']
    if not isinstance(target_chains, list):
        raise ValueError('target_chains must be a list')
    target = _source(target_pdb, target_chains, role='target')
    seen = set()
    compiled = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'candidate_key', 'source_pdb', 'binder_chains'}:
            raise ValueError('Candidate needs candidate_key, source_pdb, binder_chains')
        key, name, chains = row['candidate_key'], row['source_pdb'], row['binder_chains']
        if not isinstance(key, str) or not _KEY.fullmatch(key) or key in seen:
            raise ValueError('Invalid or duplicate candidate key')
        if not isinstance(name, str) or not name.endswith('.pdb') or Path(name).name != name:
            raise ValueError('source_pdb must be a staged PDB basename')
        path = staged_dir / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'Missing staged candidate: {name}')
        if not isinstance(chains, list):
            raise ValueError('binder_chains must be a list')
        binder = _source(path, chains, role='binder')
        if set(chains) & set(target_chains):
            raise ValueError('Binder and target chain IDs overlap in native complex')
        seen.add(key)
        components = [
            {'type': 'protein', 'id': c['id'], 'sequence': c['sequence'], 'source': role}
            for role, group in [('binder_sequence_from_candidate', binder), ('target_sequence_from_declared_target', target)]
            for c in group
        ]
        compiled.append({
            'candidate_key': key,
            'source_pdb': name,
            'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'target_sha256': hashlib.sha256(target_pdb.read_bytes()).hexdigest(),
            'binder_chains': chains,
            'target_chains': target_chains,
            'components': components,
            'conditioning': 'sequence_only_complex_cofold_no_templates_no_interface_restraints',
        })
    return compiled


def run(manifest_path: Path, staged_dir: Path, target_pdb: Path, output_dir: Path, *,
        model_variant: str, model_id_or_path: str, num_loops: int, num_sampling_steps: int,
        num_diffusion_samples: int, seed: int | None, device: str,
        runner: Path) -> dict:
    compiled = compile_selected_inputs(json.loads(manifest_path.read_text()), staged_dir, target_pdb)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for item in compiled:
        key = item['candidate_key']
        sample_dir = output_dir / key
        sample_dir.mkdir(exist_ok=False)
        component_file = sample_dir / 'components.json'
        component_file.write_text(json.dumps(item['components'], indent=2) + '\n')
        command = [sys.executable, str(runner), '--complex-components-file', str(component_file),
                   '--sequence-name', key, '--output-dir', str(sample_dir),
                   '--model-variant', model_variant, '--num-loops', str(num_loops),
                   '--num-sampling-steps', str(num_sampling_steps),
                   '--num-diffusion-samples', str(num_diffusion_samples),
                   '--device', device, '--local-files-only', 'true']
        if model_id_or_path:
            command += ['--model-id-or-path', model_id_or_path]
        if seed is not None:
            command += ['--seed', str(seed)]
        subprocess.run(command, check=True)
        native = json.loads((sample_dir / 'manifest.json').read_text())
        samples = native.get('samples')
        if (native.get('workflow') != 'esmfold2' or native.get('sequence_name') != key
                or not isinstance(samples, list) or len(samples) != num_diffusion_samples
                or native.get('sample_count') != len(samples)):
            raise ValueError(f'Native sample roster disagrees for {key}')
        seen_samples = set()
        for sample in samples:
            sample_id = sample.get('sample_id')
            if (not isinstance(sample_id, str) or not sample_id.startswith(key + '_')
                    or not sample_id[len(key) + 1:].isdigit() or sample_id in seen_samples):
                raise ValueError(f'Native sample identity disagrees for {key}')
            seen_samples.add(sample_id)
            cif, metrics = sample.get('cif'), sample.get('metrics')
            if (cif != sample_id + '.cif' or metrics != sample_id + '.metrics.json'
                    or not (sample_dir / cif).is_file() or not (sample_dir / metrics).is_file()):
                raise ValueError(f'Native sample artifacts disagree for {sample_id}')
            native_metrics = json.loads((sample_dir / metrics).read_text())
            if native_metrics.get('sample_id') != sample_id or native_metrics.get('cif') != cif:
                raise ValueError(f'Native metrics disagree for {sample_id}')
            records.append({**{k: item[k] for k in ('candidate_key', 'source_pdb', 'source_sha256',
                                                    'target_sha256', 'binder_chains', 'target_chains',
                                                    'conditioning')},
                            'sample_id': sample_id, 'cif': f'{key}/{cif}', 'metrics': f'{key}/{metrics}',
                            'raw_metrics': native_metrics, 'classification': 'unclassified'})
    receipt = {'schema_version': 1, 'assessment': 'blind_pose_sequence_only_complex_cofold',
               'predictor': 'esmfold2', 'target_pdb': target_pdb.name,
               'requested_settings': {'model_variant': model_variant, 'model_id_or_path': model_id_or_path,
                                      'num_loops': num_loops, 'num_sampling_steps': num_sampling_steps,
                                      'num_diffusion_samples': num_diffusion_samples, 'seed': seed},
               'records': records}
    (output_dir / 'blind_pose_receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection-manifest', required=True, type=Path)
    parser.add_argument('--candidate-dir', required=True, type=Path)
    parser.add_argument('--target-pdb', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--model-variant', required=True)
    parser.add_argument('--model-id-or-path', default='')
    parser.add_argument('--num-loops', type=int, required=True)
    parser.add_argument('--num-sampling-steps', type=int, required=True)
    parser.add_argument('--num-diffusion-samples', type=int, required=True)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    args = parser.parse_args()
    run(args.selection_manifest, args.candidate_dir, args.target_pdb, args.output_dir,
        model_variant=args.model_variant, model_id_or_path=args.model_id_or_path,
        num_loops=args.num_loops, num_sampling_steps=args.num_sampling_steps,
        num_diffusion_samples=args.num_diffusion_samples, seed=args.seed, device=args.device,
        runner=Path(__file__).with_name('run_esmfold2_inference.py'))


if __name__ == '__main__':
    main()
