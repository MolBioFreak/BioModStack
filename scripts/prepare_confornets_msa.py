#!/usr/bin/env python3
"""Adapt verified shared MSA custody to pinned ConforNets/OpenFold3 assets.

ConforNets 4df561a / OpenFold3 cc8bf9d consume query_msa.json, not Protenix
input JSON. This adapter only changes native representation; provider selection,
requests, cache, transfer and cancellation remain with the shared service.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))

from biomodstack_msa_handoff import hydrate_prepared_protenix_task, validate_a3m


def native_roster(request: dict, assets: Path) -> tuple[dict, list]:
    """Bind the actual produced query to the requested monomer, without renaming."""
    query_path = assets / request['benchmark'] / 'test_cases' / request['test_case'] / 'query' / (request['query_id'] + '.json')
    query_path.resolve(strict=True).relative_to(assets.resolve(strict=True))
    query = json.loads(query_path.read_bytes())
    expected = {'molecule_type': 'PROTEIN', 'chain_ids': [request['chain_id']], 'sequence': request['sequence']}
    if query != {'chains': [expected]}:
        raise ValueError('ConforNets prepared MSA requires the exact requested monomer query')
    payload = [{'name': request['query_id'], 'sequences': [{'proteinChain': {
        'sequence': request['sequence'], 'count': 1, 'id': request['chain_id'],
    }}]}]
    return query, payload


def _input_identity(root: Path, benchmark: str) -> dict:
    """Compare immutable inputs; upstream's generated batch is an output, not input."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Prepared ConforNets assets are not a regular directory')
    result = {}
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('Prepared ConforNets assets contain an unsafe entry')
        if relative.parts[:2] == (benchmark, 'batch'):
            continue
        result[relative.as_posix()] = None if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _publish(stage: Path, output: Path, benchmark: str) -> None:
    """Same no-replace atomic publication pattern as managed configuration files."""
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1) == 0:
        return
    error = ctypes.get_errno()
    if error != errno.EEXIST:
        raise OSError(error, os.strerror(error), str(output))
    if _input_identity(output, benchmark) != _input_identity(stage, benchmark):
        raise ValueError('Existing prepared ConforNets assets conflict with verified input identity')


def prepare(request: dict, assets: Path, output: Path, *, source: Path | None = None,
            sha256: str | None = None) -> Path:
    query, payload = native_roster(request, assets)
    if request['params'].get('skip_msa'):
        raise ValueError('Prepared-MSA service cannot replace an explicit skip_msa request')
    if source is None:
        from component_adapter import await_external_service
        source, sha256 = await_external_service('protenix:generated_msa', payload)
    hydrated = hydrate_prepared_protenix_task(payload, source, sha256)
    chain = hydrated[0]['sequences'][0]['proteinChain']
    # Validate every role before producing any native feature input.
    alignments = []
    for role, native_key, wire_key in (
        ('main', 'main_msa_file_paths', 'unpairedMsaPath'),
        ('paired', 'paired_msa_file_paths', 'pairedMsaPath'),
    ):
        path = chain.get(wire_key)
        if not path:
            if role == 'main':
                raise ValueError('Prepared ConforNets query lacks its main MSA')
            continue
        data = validate_a3m(Path(path), request['sequence'])
        # Pinned OF3 removes lowercase insertions, but not '.' insertion padding.
        if any('.' in line for line in data.decode().splitlines()
               if line and not line.startswith(('>', '#'))):
            raise ValueError('Pinned OpenFold3 does not support dot-padded A3M rows')
        alignments.append((role, native_key, data))
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.' + output.name + '-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'assets'
        shutil.copytree(assets, stage)
        bench = stage / request['benchmark']
        # This is the exact ordinary upstream query.json, regenerated identically.
        (bench / 'query.json').write_text(json.dumps({'seeds': [42], 'queries': {request['query_id']: query}}, indent=2))
        query = copy.deepcopy(query)
        native_chain = query['chains'][0]
        for role, native_key, data in alignments:
            # OF3 derives representative identity from the parent A3M directory.
            relative = Path(request['benchmark']) / 'msa' / request['query_id'] / (role + '.a3m')
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            native_chain[native_key] = [str(output / relative)]
        (bench / 'query_msa.json').write_text(json.dumps({'seeds': [42], 'queries': {request['query_id']: query}}, indent=2))
        # Critical custody identity: identical sequences with changed provider
        # settings/roster are not permission to reuse an earlier native feature input.
        (bench / 'prepared_msa_identity.json').write_text(json.dumps({
            'manifest_sha256': sha256, 'native_input': payload,
        }, sort_keys=True))
        _input_identity(stage, request['benchmark'])
        _publish(stage, output, request['benchmark'])
    return output / request['benchmark'] / 'query_msa.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--assets-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--prepared-inputs', type=Path)
    parser.add_argument('--prepared-sha256')
    args = parser.parse_args()
    if bool(args.prepared_inputs) != bool(args.prepared_sha256):
        parser.error('prepared inputs and digest must be supplied together')
    prepare(json.loads(args.request.read_bytes()), args.assets_dir, args.output_dir,
            source=args.prepared_inputs, sha256=args.prepared_sha256)


if __name__ == '__main__':
    main()
