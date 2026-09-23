#!/usr/bin/env python3
"""Experimental, Foundry-owned protein interface context ablation (PDB only).

Invoke inside foundry.sif with a typed JSON request path and output directory.
No result is a binding verdict; sampled patch recovery is not sequence likelihood.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path

CHECKPOINT = Path('/foundry/checkpoints/ligandmpnn_v_32_010_25.pt')
CHECKPOINT_SHA256 = '161cd264061fda9680cbb940255522ae42f2966c552d045d87913d9452a80970'
VERSION = '0.1.9'
BACKBONE = {'N', 'CA', 'C', 'O'}
AA = dict(zip('ALA ARG ASN ASP CYS GLU GLN GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL'.split(),
              'ARNDCEQGHILKMFPSTWYV'))
FIELDS = {'candidate_id', 'round_id', 'source_sha256', 'structure_path', 'binder_chain',
          'target_chain', 'target_patch', 'seed', 'samples', 'temperature'}


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate(request):
    if type(request) is not dict or set(request) != FIELDS:
        raise ValueError(f'request must have exactly {sorted(FIELDS)}')
    for key in ('candidate_id', 'round_id'):
        if type(request[key]) is not str or not request[key].strip():
            raise ValueError(f'{key} must be a nonempty string')
    for key in ('binder_chain', 'target_chain'):
        if type(request[key]) is not str or len(request[key]) != 1 or not request[key].isalnum():
            raise ValueError(f'{key} must be one alphanumeric PDB chain')
    if request['binder_chain'] == request['target_chain']:
        raise ValueError('binder and target must be distinct chains')
    if type(request['source_sha256']) is not str or len(request['source_sha256']) != 64 or any(c not in '0123456789abcdef' for c in request['source_sha256']):
        raise ValueError('source_sha256 must be lowercase SHA-256 hex')
    if type(request['samples']) is not int or not 1 <= request['samples'] <= 16:
        raise ValueError('samples must be integer 1..16')
    if type(request['seed']) is not int or not 0 <= request['seed'] <= 2**31 - 1:
        raise ValueError('seed must be nonnegative 31-bit integer')
    if type(request['temperature']) not in (float, int) or not math.isfinite(request['temperature']) or not 0.01 <= request['temperature'] <= 2:
        raise ValueError('temperature must be finite in 0.01..2')
    patch = request['target_patch']
    if type(patch) is not list or not 1 <= len(patch) <= 32 or any(type(x) is not str or not x.startswith(request['target_chain']) or len(x) < 2 for x in patch) or len(set(patch)) != len(patch):
        raise ValueError('target_patch must be unique explicit target-chain residue IDs (1..32)')
    # Native ID grammar also accepts insertion codes; do not silently truncate them.
    import re
    if any(not re.fullmatch(re.escape(request['target_chain']) + r'-?\d+[A-Za-z]?', x) for x in patch):
        raise ValueError('invalid native residue ID')
    if digest(request['structure_path']) != request['source_sha256']:
        raise ValueError('candidate structure digest mismatch')


def prepare(source, patch, binder_chain):
    """Keep original backbone/context; strip every held-out side chain and identity."""
    residues = {}
    lines = []
    chains = set()
    for line in Path(source).read_text().splitlines(keepends=True):
        if line.startswith(('MODEL ', 'ANISOU', 'HETATM', 'LINK  ')):
            raise ValueError('ambiguous model/nonprotein/anisotropic/covalent input: unsupported PDB')
        if not line.startswith('ATOM  '):
            continue  # omit trailers and unsupported records from the staged derivative
        if len(line) < 54 or line[16] not in (' ', 'A') or line[54:60].strip() not in ('', '1.00'):
            raise ValueError('alternate locations or partial occupancy unsupported')
        chain = line[21]
        if not chain.isalnum():
            raise ValueError('PDB needs explicit chain identity')
        chains.add(chain)
        key = f'{chain}{line[22:26].strip()}{line[26].strip()}'
        atom = line[12:16].strip()
        name = line[17:20].strip()
        if key not in residues:
            residues[key] = {'name': name, 'atoms': set(), 'chain': chain}
        if residues[key]['name'] != name or atom in residues[key]['atoms']:
            raise ValueError('duplicate/inconsistent PDB residue atom')
        residues[key]['atoms'].add(atom)
        lines.append((line, key, atom))
    if not lines or binder_chain not in chains:
        raise ValueError('missing protein/binder chain')
    for key, record in residues.items():
        if record['name'] not in AA or not BACKBONE <= record['atoms']:
            raise ValueError(f'noncanonical or incomplete backbone: {key}')
    if not set(patch) <= residues.keys() or any(residues[k]['chain'] == binder_chain for k in patch):
        raise ValueError('patch missing or wrong chain')
    reference = {k: AA[residues[k]['name']] for k in patch}
    def render(with_binder):
        result = []
        for line, key, atom in lines:
            if not with_binder and residues[key]['chain'] == binder_chain:
                continue
            if key in reference:
                if atom not in BACKBONE:
                    continue
                line = line[:17] + 'UNK' + line[20:]
            result.append(line)
        return ''.join(result) + 'END\n'
    return reference, render(True), render(False)


def run_native(path, patch, seed, samples, temperature):
    # The only inference engine is the installed Foundry MPNNInferenceEngine.
    from mpnn.inference_engines.mpnn import MPNNInferenceEngine
    from mpnn.utils.inference import MPNNInferenceInput
    options = {'structure_path': str(path), 'name': 'context', 'designed_residues': patch,
               'atomize_side_chains': True, 'initialize_sequence_embedding_with_ground_truth': False,
               'decode_type': 'auto_regressive', 'causality_pattern': 'auto_regressive',
               'seed': seed, 'batch_size': samples, 'number_of_batches': 1,
               'temperature': temperature}
    native_input = MPNNInferenceInput.from_atom_array_and_dict(atom_array=None, input_dict=options)
    # Verify native token mask and supplied sequence identity after parsing, not only PDB text.
    array = native_input.atom_array
    for key in patch:
        matched = MPNNInferenceInput._mask_from_ids(array, [key])
        if not matched.any() or set(array.res_name[matched]) != {'UNK'} or not array.mpnn_designed_residue_mask[matched].all():
            raise ValueError(f'native preprocessing did not hide complete patch: {key}')
        # Atomworks pads absent CCD atoms as zero-occupancy NaN ghosts. They
        # must not become visible features or carry the original side-chain pose.
        for atom, occupancy, coord in zip(array.atom_name[matched], array.occupancy[matched], array.coord[matched]):
            if atom in BACKBONE:
                if occupancy <= 0 or not all(math.isfinite(float(v)) for v in coord):
                    raise ValueError(f'native backbone absent: {key}')
            elif occupancy != 0 or any(math.isfinite(float(v)) for v in coord):
                raise ValueError(f'native side chain visible: {key}')
    engine = MPNNInferenceEngine(model_type='ligand_mpnn', checkpoint_path=str(CHECKPOINT),
                                 is_legacy_weights=True, write_fasta=False, write_structures=False)
    outputs = engine.run(input_dicts=[options])
    if len(outputs) != samples:
        raise ValueError('native sample count mismatch')
    rows = []
    for output in outputs:
        array = output.atom_array
        identities = {}
        for key in patch:
            matched = MPNNInferenceInput._mask_from_ids(array, [key])
            names = set(array.res_name[matched])
            if len(names) != 1 or next(iter(names)) not in AA:
                raise ValueError(f'native output residue mismatch: {key}')
            identities[key] = AA[next(iter(names))]
        rows.append({'batch_idx': output.output_dict['batch_idx'],
                     'design_idx': output.output_dict['design_idx'],
                     'sampled_patch': identities,
                     'native': {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                                for k, v in output.output_dict.items()
                                if k not in ('checkpoint_path', 'designed_sequence')},
                     'native_full_sequence': output.output_dict['designed_sequence']})
    return rows


def execute(request, out):
    validate(request)
    if importlib.metadata.version('rc-foundry') != VERSION or not CHECKPOINT.is_file() or digest(CHECKPOINT) != CHECKPOINT_SHA256:
        raise RuntimeError('installed Foundry version/checkpoint differs from verified capability')
    out = Path(out)
    if out.exists():
        raise FileExistsError('output directory must be new (no overwrite/retry contamination)')
    out.mkdir(parents=True)
    patch = request['target_patch']
    reference, full, ablated = prepare(request['structure_path'], patch, request['binder_chain'])
    (out / 'masked_complex.pdb').write_text(full)
    (out / 'masked_without_binder.pdb').write_text(ablated)
    conditions = {}
    for name, pdb in [('supplied_complex', 'masked_complex.pdb'), ('without_binder', 'masked_without_binder.pdb')]:
        rows = run_native(out / pdb, patch, request['seed'], request['samples'], request['temperature'])
        for row in rows:
            row['patch_exact_matches'] = sum(row['sampled_patch'][k] == reference[k] for k in patch)
            row['patch_residue_count'] = len(patch)
        conditions[name] = {'masked_input_sha256': digest(out / pdb), 'samples': rows}
    result = {'schema': 'bms.ligandmpnn.interface-context.experimental.v1',
              'status': 'completed_unclassified', 'qualification': 'unqualified',
              'candidate_id': request['candidate_id'], 'round_id': request['round_id'],
              'source_sha256': request['source_sha256'],
              'checkpoint_sha256': CHECKPOINT_SHA256, 'foundry_version': VERSION,
              'model_type': 'ligand_mpnn', 'method': 'masked_whole_patch_sampling_with_binder_ablation',
              'fixed_binder_chain': request['binder_chain'], 'target_chain': request['target_chain'],
              'target_patch': patch, 'reference_patch': reference,
              'visible_context': 'all supplied protein backbones and fixed residue side chains; assessed patch backbone only',
              'held_out': 'all assessed patch identities and side-chain atoms; no ground-truth sequence initialization',
              'seed': request['seed'], 'samples': request['samples'], 'temperature': request['temperature'],
              'metric': 'per-sample exact residue matches over entire specified patch (count; not affinity)',
              'native_metrics_note': 'Foundry native recovery compares to UNK, not the held-out reference, and native ligand-interface fields refer to nonprotein ligand context. Neither is a protein-interface score.',
              'comparator': 'same target backbone and masked patch with binder chain removed; no native ligand-interface metric claimed',
              'conditions': conditions}
    (out / 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request_json')
    parser.add_argument('output_directory')
    args = parser.parse_args()
    result = execute(json.loads(Path(args.request_json).read_text()), args.output_directory)
    print(json.dumps({'status': result['status'], 'result': str(Path(args.output_directory) / 'result.json')}))


if __name__ == '__main__':
    main()
