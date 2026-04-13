#!/usr/bin/env python3
"""
run_af2_backprop.py - AF2 backpropagation CDR refinement using ColabDesign

Optimizes antibody CDR sequences to maximize AlphaFold-Multimer binding confidence.
Uses gradient descent through the AlphaFold network to refine sequences.

Usage:
    python run_af2_backprop.py \
        --complex_pdb complex.pdb \
        --params_dir /af2_params \
        --binder_chain H \
        --output refined.pdb
"""

import argparse
import os
import sys
from pathlib import Path


def run_af2_backprop(
    complex_pdb: Path,
    params_dir: Path,
    binder_chain: str,
    target_chain: str,
    soft_iters: int,
    temp_iters: int,
    hard_iters: int,
    num_recycles: int,
    learning_rate: float,
    use_multimer: bool,
    num_models: int,
    loss_plddt: float,
    loss_pae: float,
    loss_contact: float,
    output_pdb: Path,
):
    """
    Run AF2 backpropagation to refine antibody CDR sequences.
    
    Uses ColabDesign's 'binder' protocol with 3-stage optimization:
    1. Soft optimization - continuous logits
    2. Temperature annealing - gradual discretization  
    3. Hard optimization - discrete sequences
    """
    try:
        from colabdesign import mk_afdesign_model
    except ImportError:
        print("ERROR: ColabDesign not installed.")
        print("Install with: uv pip install git+https://github.com/sokrypton/ColabDesign.git@v1.1.1")
        sys.exit(1)
    
    print(f"=== AF2 Backprop CDR Refinement ===")
    print(f"Complex PDB: {complex_pdb}")
    print(f"Binder chain: {binder_chain}")
    print(f"Target chain: {target_chain}")
    print(f"Use Multimer: {use_multimer}")
    print(f"Num Models: {num_models}")
    print(f"Num Recycles: {num_recycles}")
    print(f"Learning Rate: {learning_rate}")
    print(f"Iterations: soft={soft_iters}, temp={temp_iters}, hard={hard_iters}")
    print(f"Loss Weights: pLDDT={loss_plddt}, PAE={loss_pae}, Contact={loss_contact}")
    
    # Set up data directory for AF2 params
    os.environ['ALPHAFOLD_DATA_DIR'] = str(params_dir)
    
    # Determine model names based on num_models and use_multimer
    if use_multimer:
        model_names = [f"model_{i}_multimer_v3" for i in range(1, num_models + 1)]
    else:
        model_names = [f"model_{i}" for i in range(1, num_models + 1)]
    
    print(f"Using models: {model_names}")
    
    # Create AfDesign model with binder protocol
    print("\nInitializing AfDesign model...")
    model = mk_afdesign_model(
        protocol="binder",
        data_dir=str(params_dir),
        use_multimer=use_multimer,
        num_recycles=num_recycles,
        model_names=model_names
    )
    
    # Set custom loss weights
    model.set_weights({
        "plddt": loss_plddt,
        "pae": loss_pae,
        "con": loss_contact,  # Interface contacts
    })
    
    # Set learning rate
    model.set_optimizer(learning_rate=learning_rate)
    
    # Load complex and specify chains
    print("Loading complex structure...")
    model.prep_inputs(
        pdb_filename=str(complex_pdb),
        chain=target_chain,        # Target chain(s)
        binder_chain=binder_chain, # Antibody chain to redesign
        hotspot=None,              # Let AF2 find interface
        rm_aa="C"                  # Avoid cysteines (optional, prevents disulfides)
    )
    
    # Run 3-stage optimization
    print("\n--- Stage 1: Soft optimization ---")
    model.design_soft(soft_iters)
    
    print("\n--- Stage 2: Temperature annealing ---")
    model.design_3stage(
        soft_iters=0,           # Already done
        temp_iters=temp_iters,
        hard_iters=0            # Do separately
    )
    
    print("\n--- Stage 3: Hard optimization ---")
    model.design_hard(hard_iters)
    
    # Get results
    print("\n=== Optimization Results ===")
    metrics = model.get_loss()
    print(f"pLDDT: {metrics.get('plddt', 'N/A'):.3f}")
    print(f"pTM: {metrics.get('ptm', 'N/A'):.3f}")
    print(f"iPTM: {metrics.get('i_ptm', 'N/A'):.3f}")
    print(f"PAE: {metrics.get('pae', 'N/A'):.3f}")
    
    # Get optimized sequence
    seq = model.get_seqs()[0] if model.get_seqs() else "UNKNOWN"
    print(f"\nOptimized sequence ({len(seq)} residues):")
    print(seq[:80] + ("..." if len(seq) > 80 else ""))
    
    # Save refined structure
    print(f"\nSaving refined structure to: {output_pdb}")
    model.save_pdb(str(output_pdb))
    
    # Save sequence to FASTA
    fasta_path = output_pdb.with_suffix('.fasta')
    with open(fasta_path, 'w') as f:
        f.write(f">{output_pdb.stem}_refined\n{seq}\n")
    print(f"Saved sequence to: {fasta_path}")
    
    # Save metrics to JSON
    import json
    metrics_path = output_pdb.with_suffix('.json')
    with open(metrics_path, 'w') as f:
        json.dump({
            'plddt': float(metrics.get('plddt', 0)),
            'ptm': float(metrics.get('ptm', 0)),
            'i_ptm': float(metrics.get('i_ptm', 0)),
            'pae': float(metrics.get('pae', 0)),
            'sequence': seq,
            'soft_iters': soft_iters,
            'temp_iters': temp_iters,
            'hard_iters': hard_iters,
            'num_recycles': num_recycles,
            'learning_rate': learning_rate,
            'use_multimer': use_multimer,
            'num_models': num_models,
            'loss_weights': {
                'plddt': loss_plddt,
                'pae': loss_pae,
                'contact': loss_contact,
            }
        }, f, indent=2)
    print(f"Saved metrics to: {metrics_path}")
    
    print("\n=== AF2 Backprop complete ===")
    return True


def main():
    parser = argparse.ArgumentParser(
        description='AF2 backpropagation CDR refinement using ColabDesign'
    )
    parser.add_argument('--complex_pdb', required=True, type=Path,
                        help='Input complex PDB (target + antibody)')
    parser.add_argument('--params_dir', required=True, type=Path,
                        help='Path to AlphaFold2 parameters directory')
    parser.add_argument('--binder_chain', default='H',
                        help='Antibody chain ID to redesign (default: H)')
    parser.add_argument('--target_chain', default='T',
                        help='Target chain ID (default: T)')
    parser.add_argument('--soft_iters', type=int, default=100,
                        help='Soft optimization iterations (default: 100)')
    parser.add_argument('--temp_iters', type=int, default=100,
                        help='Temperature annealing iterations (default: 100)')
    parser.add_argument('--hard_iters', type=int, default=10,
                        help='Hard optimization iterations (default: 10)')
    parser.add_argument('--num_recycles', type=int, default=3,
                        help='AF2 recycling iterations (default: 3)')
    parser.add_argument('--learning_rate', type=float, default=0.1,
                        help='Gradient descent learning rate (default: 0.1)')
    parser.add_argument('--use_multimer', type=lambda x: x.lower() == 'true', default=True,
                        help='Use AlphaFold-Multimer (default: true)')
    parser.add_argument('--num_models', type=int, default=1,
                        help='Number of AF2 models to ensemble (default: 1)')
    parser.add_argument('--loss_plddt', type=float, default=0.1,
                        help='Weight for pLDDT loss (default: 0.1)')
    parser.add_argument('--loss_pae', type=float, default=0.1,
                        help='Weight for PAE loss (default: 0.1)')
    parser.add_argument('--loss_contact', type=float, default=0.5,
                        help='Weight for interface contact loss (default: 0.5)')
    parser.add_argument('--output', required=True, type=Path,
                        help='Output refined PDB path')
    
    args = parser.parse_args()
    
    if not args.complex_pdb.exists():
        raise FileNotFoundError(f"Complex PDB not found: {args.complex_pdb}")
    if not args.params_dir.exists():
        raise FileNotFoundError(f"AF2 params directory not found: {args.params_dir}")
    
    success = run_af2_backprop(
        complex_pdb=args.complex_pdb,
        params_dir=args.params_dir,
        binder_chain=args.binder_chain,
        target_chain=args.target_chain,
        soft_iters=args.soft_iters,
        temp_iters=args.temp_iters,
        hard_iters=args.hard_iters,
        num_recycles=args.num_recycles,
        learning_rate=args.learning_rate,
        use_multimer=args.use_multimer,
        num_models=args.num_models,
        loss_plddt=args.loss_plddt,
        loss_pae=args.loss_pae,
        loss_contact=args.loss_contact,
        output_pdb=args.output,
    )
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
