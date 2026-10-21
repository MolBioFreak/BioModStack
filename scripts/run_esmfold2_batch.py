#!/usr/bin/env python3
"""ESMFold2 batch owner: reuse seeded eval models, not unseeded RNG streams."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from run_esmfold2_inference import build_parser, compile_workflow_request, main as predict, parse_pdb_polymer_components


def run_requests(requests: list[dict]) -> None:
    cache = {}
    for item in requests:
        argv = item['argv']
        args = build_parser().parse_args(argv)
        previous = os.environ.pop('BMS_ESMFOLD2_EFFECTIVE_SETTINGS', None)
        try:
            if item.get('receipt'):
                os.environ['BMS_ESMFOLD2_EFFECTIVE_SETTINGS'] = item['receipt']
            if args.seed is None:
                subprocess.run([sys.executable, str(Path(__file__).with_name('run_esmfold2_inference.py')), *argv], check=True)
            else:
                predict(argv, model_cache=cache)
        finally:
            os.environ.pop('BMS_ESMFOLD2_EFFECTIVE_SETTINGS', None)
            if previous is not None:
                os.environ['BMS_ESMFOLD2_EFFECTIVE_SETTINGS'] = previous


def validation_requests(pdbs: list[str], msa_files: list[str], settings: dict, raw_root: Path) -> list[dict]:
    """Use authored settings and assign staged alignments by exact query sequence.

    Candidate PDB sequences are the design product. Independent chemical context
    in the same document is retained; no coordinates become templates. A supplied
    component document is authoritative instead of being combined with the PDB.
    """
    from esm.utils.msa import MSA
    defaults = dict(model_variant='fast', num_loops=1, num_sampling_steps=25,
                    num_diffusion_samples=1, device='cuda', local_files_only=True)
    def model_value(key):
        for alias in ('esmfold2_validation_' + key, 'esmf_' + key, 'esmfold2_' + key, key):
            if settings.get(alias) is not None:
                return settings[alias]
        return None
    keys = vars(build_parser().parse_args(['--output-dir', 'unused']))
    for key in keys:
        value = model_value(key)
        if value is not None:
            defaults[key] = value
    if settings.get('esmfold2_validation_variant') is not None:
        defaults['model_variant'] = settings['esmfold2_validation_variant']
    alignments = {}
    supplied_msa = [*msa_files]
    if defaults.get('msa_path'):
        supplied_msa.append(defaults['msa_path'])
    for name in dict.fromkeys(supplied_msa):
        path = Path(name)
        if path.suffix.lower() not in {'.a3m', '.sto', '.stockholm'}:
            continue
        reader = MSA.from_stockholm if path.suffix.lower() in {'.sto', '.stockholm'} else MSA.from_a3m
        msa = reader(path)
        if msa.sequences:
            alignments.setdefault(msa.sequences[0].replace('-', '').upper(), str(path))
    requests = []
    for name in pdbs:
        path = Path(name)
        output = raw_root / path.stem
        output.mkdir(parents=True, exist_ok=True)
        request = dict(defaults, core_protein_scientific_contract=1,
                       sequence_name=path.stem, output_dir=str(output))
        components = model_value('complex_components')
        if components is None and defaults.get('complex_components_json'):
            components = json.loads(defaults['complex_components_json'])
        if components is None and defaults.get('complex_components_file'):
            components = json.loads(Path(defaults['complex_components_file']).read_text())
        if components is None:
            components = parse_pdb_polymer_components(path,
                chain_ids=defaults.get('pdb_chain_ids'),
                include_dna_rna=defaults.get('pdb_include_dna_rna', True), include_ligands=True)
        components = json.loads(json.dumps(components))
        rows = components.get('components', components.get('sequences')) if isinstance(components, dict) else components
        staged = {}
        for row in rows:
            if row.get('type') == 'protein' and 'msa' not in row and not row.get('msa_path'):
                if row.get('sequence') in alignments:
                    row['msa_path'] = alignments[row['sequence']]
            if row.get('msa_path'):
                staged[row['msa_path']] = row['msa_path']
            for key in ('msa_format', 'msa_max_sequences', 'msa_remove_insertions'):
                if key in defaults:
                    row.setdefault(key, defaults[key])
        request['complex_components'] = components
        for key in ('covalent_bonds', 'distogram_conditioning'):
            value = model_value(key)
            if value is not None:
                request[key] = value
        # Component-owned sources must not also enter via a primary sequence.
        request.pop('msa_path', None)
        request.pop('pdb_sequence_path', None)
        request.pop('sequence', None)
        request.pop('complex_components_json', None)
        request.pop('complex_components_file', None)
        argv, receipt = compile_workflow_request(request, staged)
        receipt_path = output / 'effective_settings.json'
        receipt_path.write_text(json.dumps(receipt, indent=2))
        requests.append({'argv': argv, 'receipt': str(receipt_path.resolve())})
    return requests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request-file')
    parser.add_argument('--validation-inputs')
    parser.add_argument('--raw-root', default='raw_predictions')
    args = parser.parse_args()
    if args.validation_inputs:
        payload = json.loads(Path(args.validation_inputs).read_text())
        requests = validation_requests(payload['pdbs'], payload['msa_files'], payload['settings'], Path(args.raw_root))
    else:
        requests = json.loads(Path(args.request_file).read_text())
    run_requests(requests)


if __name__ == '__main__':
    main()
