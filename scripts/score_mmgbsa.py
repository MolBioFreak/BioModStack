#!/usr/bin/env python3
"""
score_mmgbsa.py - MM-GBSA Binding Free Energy Calculation

Calculates binding free energy using Molecular Mechanics with 
Generalized Born Surface Area (MM-GBSA) implicit solvation.

Supports multiple scoring modes:
- interface: Antibody-antigen interface delta G
- stability: Single chain folding stability
- both: Full decomposition for comprehensive analysis

Part of BioModStack physics refinement layer (Phase 1).
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

# OpenMM imports
try:
    import openmm
    from openmm import app, unit
    from openmm.app import PDBFile, ForceField, Modeller, Simulation
    from openmm import LangevinMiddleIntegrator
except ImportError as e:
    print(f"Error: OpenMM not installed. {e}")
    sys.exit(1)

# PDBFixer for structure preparation
try:
    from pdbfixer import PDBFixer
except ImportError:
    PDBFixer = None

# BioPython for chain manipulation
from Bio.PDB import PDBParser, PDBIO, Select

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CHAIN EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

class ChainSelect(Select):
    """BioPython Select class to extract specific chains."""
    
    def __init__(self, chain_ids: List[str]):
        self.chain_ids = chain_ids
    
    def accept_chain(self, chain):
        return chain.id in self.chain_ids


def extract_chains(input_pdb: Path, output_pdb: Path, chain_ids: List[str]) -> Path:
    """
    Extract specific chains from a PDB file.
    
    Args:
        input_pdb: Input PDB path
        output_pdb: Output PDB path
        chain_ids: List of chain IDs to extract
        
    Returns:
        Path to output PDB
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure('complex', str(input_pdb))
    
    io = PDBIO()
    io.set_structure(structure)
    io.save(str(output_pdb), ChainSelect(chain_ids))
    
    return output_pdb


def get_chain_ids(pdb_path: Path) -> List[str]:
    """Get all chain IDs from a PDB file."""
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure('structure', str(pdb_path))
    
    chain_ids = []
    for model in structure:
        for chain in model:
            chain_ids.append(chain.id)
    
    return chain_ids


# ═══════════════════════════════════════════════════════════════════════════════
# ENERGY CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_energy(
    pdb_path: Path,
    force_field: str = 'amber14sb',
    minimize: bool = True,
    min_iterations: int = 100,
) -> Dict[str, float]:
    """
    Calculate the potential energy of a structure.
    
    Args:
        pdb_path: Path to PDB file
        force_field: Force field name
        minimize: Whether to minimize before energy calculation
        min_iterations: Minimization iterations
        
    Returns:
        Dictionary with energy components
    """
    # Load structure
    pdb = PDBFile(str(pdb_path))
    
    # Get force field with implicit solvent (GBSA)
    if force_field == 'amber14sb':
        ff = ForceField('amber14-all.xml', 'implicit/gbn2.xml')
    else:
        ff = ForceField('charmm36.xml', 'implicit/gbn2.xml')
    
    # Create modeller and add hydrogens
    modeller = Modeller(pdb.topology, pdb.positions)
    modeller.addHydrogens(ff)
    
    # Create system
    system = ff.createSystem(
        modeller.topology,
        nonbondedMethod=app.NoCutoff,
        constraints=app.HBonds,
    )
    
    # Create integrator and simulation
    integrator = LangevinMiddleIntegrator(
        300 * unit.kelvin,
        1.0 / unit.picosecond,
        0.002 * unit.picoseconds
    )
    
    # Try CUDA platform first
    try:
        platform = openmm.Platform.getPlatformByName('CUDA')
        properties = {'DeviceIndex': '0', 'Precision': 'mixed'}
        simulation = Simulation(modeller.topology, system, integrator, platform, properties)
    except Exception:
        platform = openmm.Platform.getPlatformByName('CPU')
        simulation = Simulation(modeller.topology, system, integrator, platform)
    
    simulation.context.setPositions(modeller.positions)
    
    # Optional minimization
    if minimize:
        openmm.LocalEnergyMinimizer.minimize(
            simulation.context,
            maxIterations=min_iterations
        )
    
    # Get energy
    state = simulation.context.getState(getEnergy=True)
    potential_energy = state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    
    # Try to get energy decomposition
    energies = {
        'total_potential': potential_energy,
    }
    
    # Get force-specific energies
    for i, force in enumerate(system.getForces()):
        force.setForceGroup(i)
    
    # Recalculate with force groups
    simulation.context.reinitialize(preserveState=True)
    
    force_names = {
        'HarmonicBondForce': 'bond',
        'HarmonicAngleForce': 'angle',
        'PeriodicTorsionForce': 'torsion',
        'NonbondedForce': 'nonbonded',
        'CustomGBForce': 'gbsa',
        'GBSAOBCForce': 'gbsa',
    }
    
    for i, force in enumerate(system.getForces()):
        force_type = force.__class__.__name__
        if force_type in force_names:
            state = simulation.context.getState(getEnergy=True, groups={i})
            energy = state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
            energies[force_names[force_type]] = energy
    
    return energies


def calculate_mmgbsa(
    complex_pdb: Path,
    binder_chains: List[str],
    target_chains: List[str],
    work_dir: Path,
    force_field: str = 'amber14sb',
    minimize: bool = True,
) -> Dict[str, Any]:
    """
    Calculate MM-GBSA binding free energy.
    
    ΔG_bind = G_complex - G_binder - G_target
    
    Args:
        complex_pdb: Path to complex PDB
        binder_chains: Chain IDs of the binder (e.g., ['H'] for nanobody)
        target_chains: Chain IDs of the target (e.g., ['A'] for antigen)
        work_dir: Working directory for intermediate files
        force_field: Force field name
        minimize: Whether to minimize structures
        
    Returns:
        Dictionary with MM-GBSA results
    """
    logger.info(f"Calculating MM-GBSA for: {complex_pdb.name}")
    logger.info(f"  Binder chains: {binder_chains}")
    logger.info(f"  Target chains: {target_chains}")
    
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract individual components
    binder_pdb = work_dir / 'binder.pdb'
    target_pdb = work_dir / 'target.pdb'
    
    extract_chains(complex_pdb, binder_pdb, binder_chains)
    extract_chains(complex_pdb, target_pdb, target_chains)
    
    # Calculate energies
    logger.info("Calculating complex energy...")
    complex_energy = calculate_energy(complex_pdb, force_field, minimize)
    
    logger.info("Calculating binder energy...")
    binder_energy = calculate_energy(binder_pdb, force_field, minimize)
    
    logger.info("Calculating target energy...")
    target_energy = calculate_energy(target_pdb, force_field, minimize)
    
    # Calculate binding free energy
    dg_bind = (
        complex_energy['total_potential'] -
        binder_energy['total_potential'] -
        target_energy['total_potential']
    )
    
    # Convert to kcal/mol
    dg_bind_kcal = dg_bind / 4.184
    
    logger.info(f"ΔG_bind: {dg_bind:.2f} kJ/mol ({dg_bind_kcal:.2f} kcal/mol)")
    
    # Energy decomposition
    decomposition = {}
    for key in ['bond', 'angle', 'torsion', 'nonbonded', 'gbsa']:
        if key in complex_energy and key in binder_energy and key in target_energy:
            delta = (
                complex_energy.get(key, 0) -
                binder_energy.get(key, 0) -
                target_energy.get(key, 0)
            )
            decomposition[key] = delta / 4.184  # kcal/mol
    
    results = {
        'mmgbsa_dg_bind': float(dg_bind_kcal),
        'mmgbsa_dg_bind_kj': float(dg_bind),
        'mmgbsa_complex_energy': float(complex_energy['total_potential']),
        'mmgbsa_binder_energy': float(binder_energy['total_potential']),
        'mmgbsa_target_energy': float(target_energy['total_potential']),
        'mmgbsa_decomposition': decomposition,
        'mmgbsa_force_field': force_field,
        'mmgbsa_binder_chains': binder_chains,
        'mmgbsa_target_chains': target_chains,
    }
    
    return results


def calculate_stability(
    structure_pdb: Path,
    force_field: str = 'amber14sb',
    minimize: bool = True,
) -> Dict[str, Any]:
    """
    Calculate folding stability (single chain/structure).
    
    Args:
        structure_pdb: Path to structure PDB
        force_field: Force field name
        minimize: Whether to minimize
        
    Returns:
        Dictionary with stability metrics
    """
    logger.info(f"Calculating stability for: {structure_pdb.name}")
    
    energies = calculate_energy(structure_pdb, force_field, minimize)
    
    # Normalize by residue count
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure('structure', str(structure_pdb))
    
    n_residues = 0
    for model in structure:
        for chain in model:
            n_residues += len(list(chain.get_residues()))
    
    energy_per_residue = energies['total_potential'] / n_residues if n_residues > 0 else 0
    
    results = {
        'stability_total_energy': float(energies['total_potential']),
        'stability_energy_per_residue': float(energy_per_residue),
        'stability_n_residues': n_residues,
        'stability_force_field': force_field,
    }
    
    return results


def calculate_ddg(
    mutant_pdb: Path,
    wildtype_pdb: Path,
    binder_chains: List[str],
    target_chains: List[str],
    work_dir: Path,
    force_field: str = 'amber14sb',
) -> Dict[str, Any]:
    """
    Calculate ΔΔG for mutagenesis validation.
    
    ΔΔG = ΔG_mutant - ΔG_wildtype
    
    Negative ΔΔG indicates improved binding.
    
    Args:
        mutant_pdb: Path to mutant complex PDB
        wildtype_pdb: Path to wild-type complex PDB
        binder_chains: Binder chain IDs
        target_chains: Target chain IDs
        work_dir: Working directory
        force_field: Force field name
        
    Returns:
        Dictionary with ΔΔG results
    """
    logger.info("Calculating ΔΔG (Mutant - WildType)")
    
    # Calculate MM-GBSA for both
    mutant_results = calculate_mmgbsa(
        mutant_pdb, binder_chains, target_chains,
        work_dir / 'mutant', force_field
    )
    
    wildtype_results = calculate_mmgbsa(
        wildtype_pdb, binder_chains, target_chains,
        work_dir / 'wildtype', force_field
    )
    
    # Calculate ΔΔG
    ddg = mutant_results['mmgbsa_dg_bind'] - wildtype_results['mmgbsa_dg_bind']
    
    logger.info(f"ΔG_mutant: {mutant_results['mmgbsa_dg_bind']:.2f} kcal/mol")
    logger.info(f"ΔG_wildtype: {wildtype_results['mmgbsa_dg_bind']:.2f} kcal/mol")
    logger.info(f"ΔΔG: {ddg:.2f} kcal/mol")
    
    if ddg < 0:
        logger.info("  → Mutation IMPROVES binding")
    elif ddg > 0:
        logger.info("  → Mutation WEAKENS binding")
    else:
        logger.info("  → Mutation has NEUTRAL effect")
    
    results = {
        'openmm_ddg_mutation': float(ddg),
        'openmm_dg_mutant': float(mutant_results['mmgbsa_dg_bind']),
        'openmm_dg_wildtype': float(wildtype_results['mmgbsa_dg_bind']),
        'openmm_delta_interface': float(ddg),  # Alias for database field
        'mutant_decomposition': mutant_results['mmgbsa_decomposition'],
        'wildtype_decomposition': wildtype_results['mmgbsa_decomposition'],
    }
    
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='MM-GBSA Binding Free Energy Calculation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Scoring Modes:
  interface  - Calculate binding ΔG between binder and target chains
  stability  - Calculate single structure folding stability
  both       - Calculate both interface and stability
  ddg        - Calculate ΔΔG between mutant and wildtype

Examples:
  # Antibody-antigen binding energy
  python score_mmgbsa.py --mode interface --complex complex.pdb \\
      --binder_chains H --target_chains A
  
  # Nanobody stability
  python score_mmgbsa.py --mode stability --complex nanobody.pdb
  
  # Mutagenesis ΔΔG
  python score_mmgbsa.py --mode ddg --complex mutant.pdb --wildtype wt.pdb \\
      --binder_chains H --target_chains A
        """
    )
    
    parser.add_argument('--mode', choices=['interface', 'stability', 'both', 'ddg'],
                        default='interface',
                        help='Scoring mode (default: interface)')
    parser.add_argument('--complex', '-c', type=Path, required=True,
                        help='Input complex/structure PDB file')
    parser.add_argument('--wildtype', '-w', type=Path,
                        help='Wild-type PDB for ΔΔG calculation')
    parser.add_argument('--output', '-o', type=Path,
                        help='Output JSON file')
    parser.add_argument('--work_dir', type=Path, default=Path('.'),
                        help='Working directory for intermediate files')
    
    # Chain specification
    parser.add_argument('--binder_chains', type=str, default='H',
                        help='Binder chain IDs (comma-separated, default: H)')
    parser.add_argument('--target_chains', type=str, default='A',
                        help='Target chain IDs (comma-separated, default: A)')
    
    # Calculation settings
    parser.add_argument('--force_field', choices=['amber14sb', 'charmm36m'],
                        default='amber14sb',
                        help='Force field (default: amber14sb)')
    parser.add_argument('--no_minimize', action='store_true',
                        help='Skip minimization before energy calculation')
    
    args = parser.parse_args()
    
    # Validate input
    if not args.complex.exists():
        logger.error(f"Complex file not found: {args.complex}")
        sys.exit(1)
    
    if args.mode == 'ddg' and (not args.wildtype or not args.wildtype.exists()):
        logger.error("Wild-type PDB required for ΔΔG mode")
        sys.exit(1)
    
    # Parse chain IDs
    binder_chains = [c.strip() for c in args.binder_chains.split(',')]
    target_chains = [c.strip() for c in args.target_chains.split(',')]
    
    # Create work directory
    args.work_dir.mkdir(parents=True, exist_ok=True)
    
    results = {}
    
    try:
        if args.mode == 'interface':
            results = calculate_mmgbsa(
                args.complex, binder_chains, target_chains,
                args.work_dir, args.force_field, not args.no_minimize
            )
        
        elif args.mode == 'stability':
            results = calculate_stability(
                args.complex, args.force_field, not args.no_minimize
            )
        
        elif args.mode == 'both':
            interface_results = calculate_mmgbsa(
                args.complex, binder_chains, target_chains,
                args.work_dir, args.force_field, not args.no_minimize
            )
            stability_results = calculate_stability(
                args.complex, args.force_field, not args.no_minimize
            )
            results = {**interface_results, **stability_results}
        
        elif args.mode == 'ddg':
            results = calculate_ddg(
                args.complex, args.wildtype,
                binder_chains, target_chains,
                args.work_dir, args.force_field
            )
        
        # Write results
        output_path = args.output or args.complex.with_suffix('.mmgbsa.json')
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"Results written to: {output_path}")
        
        # Print summary
        if 'mmgbsa_dg_bind' in results:
            logger.info(f"Binding ΔG: {results['mmgbsa_dg_bind']:.2f} kcal/mol")
        if 'openmm_ddg_mutation' in results:
            logger.info(f"ΔΔG: {results['openmm_ddg_mutation']:.2f} kcal/mol")
        if 'stability_energy_per_residue' in results:
            logger.info(f"Stability: {results['stability_energy_per_residue']:.2f} kJ/mol/residue")
        
        logger.info("MM-GBSA scoring completed successfully")
        
    except Exception as e:
        logger.error(f"MM-GBSA calculation failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
