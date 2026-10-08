#!/usr/bin/env python3
"""
Generate Boltz-2 YAML for protein-DNA complex prediction.

Creates YAML input files for predicting protein-DNA complexes with Boltz-2.
This is used as an optional upstream step before RFantibody when users
provide sequences instead of pre-computed structures.

Usage:
    python prep_complex_yaml.py \\
        --protein-seq "MQNSH..." \\
        --dna-seq "GAATTC..." \\
        --output complex.yaml
"""

import argparse
import yaml
from pathlib import Path


def generate_complex_yaml(
    protein_seq: str,
    dna_seq: str = None,
    rna_seq: str = None,
    protein_id: str = "A",
    dna_id: str = "B",
    rna_id: str = "C",
    use_msa: bool = False,
    msa_path: str = None,
) -> dict:
    """
    Generate Boltz-2 YAML configuration for protein-DNA/RNA complex.
    
    Args:
        protein_seq: Protein amino acid sequence (1-letter codes)
        dna_seq: Optional DNA sequence (ATGC)
        rna_seq: Optional RNA sequence (AUGC)
        protein_id: Chain ID for protein (default A)
        dna_id: Chain ID for DNA (default B)
        rna_id: Chain ID for RNA (default C)
        use_msa: Whether to use MSA for protein
        msa_path: Path to pre-computed MSA file
        
    Returns:
        Dictionary representing Boltz YAML configuration
    """
    sequences = []
    
    # Add protein entity
    protein_entity = {
        'protein': {
            'id': [protein_id],
            'sequence': protein_seq.upper().replace(" ", "").replace("\n", ""),
        }
    }
    
    # MSA handling
    if use_msa and msa_path:
        protein_entity['protein']['msa'] = msa_path
    else:
        protein_entity['protein']['msa'] = 'empty'
    
    sequences.append(protein_entity)
    
    # Add DNA entity if provided
    if dna_seq:
        dna_entity = {
            'dna': {
                'id': [dna_id],
                'sequence': dna_seq.upper().replace(" ", "").replace("\n", ""),
            }
        }
        sequences.append(dna_entity)
    
    # Add RNA entity if provided
    if rna_seq:
        rna_entity = {
            'rna': {
                'id': [rna_id],
                'sequence': rna_seq.upper().replace(" ", "").replace("\n", ""),
            }
        }
        sequences.append(rna_entity)
    
    return {'sequences': sequences}


def main():
    parser = argparse.ArgumentParser(
        description='Generate Boltz-2 YAML for protein-DNA/RNA complex prediction',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument('--protein-seq', required=True,
                        help='Protein sequence (1-letter amino acid codes)')
    parser.add_argument('--dna-seq', default=None,
                        help='DNA sequence (ATGC)')
    parser.add_argument('--rna-seq', default=None,
                        help='RNA sequence (AUGC)')
    parser.add_argument('--protein-id', default='A',
                        help='Chain ID for protein')
    parser.add_argument('--dna-id', default='B',
                        help='Chain ID for DNA')
    parser.add_argument('--rna-id', default='C',
                        help='Chain ID for RNA')
    parser.add_argument('--msa-path', default=None,
                        help='Path to pre-computed MSA file')
    parser.add_argument('--output', '-o', required=True,
                        help='Output YAML file path')
    
    args = parser.parse_args()
    
    # Validate input
    if not args.dna_seq and not args.rna_seq:
        print("Note: No DNA or RNA sequence provided. Predicting protein only.")
    
    # Generate YAML
    yaml_config = generate_complex_yaml(
        protein_seq=args.protein_seq,
        dna_seq=args.dna_seq,
        rna_seq=args.rna_seq,
        protein_id=args.protein_id,
        dna_id=args.dna_id,
        rna_id=args.rna_id,
        use_msa=bool(args.msa_path),
        msa_path=args.msa_path,
    )
    
    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w') as f:
        yaml.dump(yaml_config, f, sort_keys=False, default_flow_style=False)
    
    print(f"Generated: {output_path}")
    print(f"  Entities: {len(yaml_config['sequences'])}")
    for entity in yaml_config['sequences']:
        entity_type = list(entity.keys())[0]
        seq_len = len(entity[entity_type]['sequence'])
        chain_id = entity[entity_type]['id'][0]
        print(f"    - {entity_type} (chain {chain_id}): {seq_len} residues")


if __name__ == '__main__':
    main()
