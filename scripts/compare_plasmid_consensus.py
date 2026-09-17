#!/usr/bin/env python3
"""Retain the read-guided consensus beside an assembly input without overwriting either."""
from __future__ import annotations
import argparse
import json
import shutil
from pathlib import Path
from verify_construct import read_single_fasta, exact_circular_equivalence, sha256_file


def prepare_comparison(bundle: Path, read_consensus: Path, output: Path) -> dict:
    if not bundle.is_dir() or bundle.is_symlink() or any(p.is_symlink() for p in bundle.rglob('*')):
        raise ValueError('comparison source bundle must contain regular retained artifacts')
    if output.exists():
        raise ValueError('comparison output already exists; refusing to overwrite evidence')
    _,read_sequence=read_single_fasta(read_consensus)
    _,assembled_sequence=read_single_fasta(bundle/'observed_consensus.fasta')
    state=json.loads((bundle/'observed_state.json').read_text())
    if state.get('observed_sha256')!=sha256_file(bundle/'observed_consensus.fasta'):
        raise ValueError('assembly identity does not match its bundle')
    shutil.copytree(bundle,output)
    retained=output/'read_guided_consensus.fasta'
    shutil.copyfile(read_consensus,retained)
    comparison={
        'path':retained.name,'sha256':sha256_file(retained),
        'method':'samtools_bayesian_reference_guided',
        'evidence_relationship':'same_read_population_different_consensus_method',
    }
    state['read_guided_comparison']=comparison
    (output/'observed_state.json').write_text(json.dumps(state,sort_keys=True,indent=2)+'\n')
    result={
        'schema':'biomodstack.consensus_comparison.v1',
        'assembly_sha256':state['observed_sha256'],'read_guided_sha256':comparison['sha256'],
        'exact_circular_agreement':exact_circular_equivalence(assembled_sequence,read_sequence) is not None,
        'independent_biological_replication':False,
    }
    (output/'consensus_comparison.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--read-consensus',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    prepare_comparison(args.bundle,args.read_consensus,args.out_dir)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
