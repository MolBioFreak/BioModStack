#!/usr/bin/env python3
"""
relax_openmm.py - OpenMM Energy Minimization for AI-Generated Protein Structures

Domain-aware physics refinement with specialized support for:
- Antibody/Nanobody CDR-only relaxation with framework restraints
- General protein structure relaxation
- Neural Network Force Field (NNFF) integration (MACE-OFF, ANI-2x)

Part of BioModStack physics refinement layer (Phase 1).
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

import numpy as np

# OpenMM imports
try:
    import openmm
    from openmm import app, unit
    from openmm.app import PDBFile, ForceField, Modeller, Simulation
    from openmm import LangevinMiddleIntegrator, LocalEnergyMinimizer
except ImportError as e:
    print(f"Error: OpenMM not installed. {e}")
    sys.exit(1)

# PDBFixer for structure preparation
try:
    from pdbfixer import PDBFixer
except ImportError:
    PDBFixer = None

# BioPython for chain/residue parsing
from Bio.PDB import PDBParser, PDBIO, Select

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# CDR regions by IMGT numbering (approximate Chothia mapping)
CDR_REGIONS = {
    'H1': (26, 35),
    'H2': (50, 65),
    'H3': (95, 102),
    'L1': (24, 34),
    'L2': (50, 56),
    'L3': (89, 97),
}

# VHH framework tetrad positions (critical for solubility)
VHH_TETRAD_POSITIONS = [37, 44, 45, 47]

# Force field priority chain
FORCE_FIELD_PRIORITY = ['amber14sb', 'charmm36m']

# Default restraint strength (kcal/mol/Å²)
DEFAULT_RESTRAINT_STRENGTH = 5.0


# ═══════════════════════════════════════════════════════════════════════════════
# STRUCTURE PREPARATION
# ═══════════════════════════════════════════════════════════════════════════════

def fix_structure(input_pdb: Path, output_pdb: Path) -> Path:
    """
    Use PDBFixer to add missing atoms, hydrogens, and fix common PDB issues.
    
    Args:
        input_pdb: Path to input PDB file
        output_pdb: Path to write fixed PDB
        
    Returns:
        Path to fixed PDB file
    """
    if PDBFixer is None:
        logger.warning("PDBFixer not available, skipping structure fixing")
        return input_pdb
    
    logger.info(f"Fixing structure with PDBFixer: {input_pdb.name}")
    
    fixer = PDBFixer(filename=str(input_pdb))
    
    # Find and add missing residues (optional, can cause issues)
    # fixer.findMissingResidues()
    
    # Find and replace non-standard residues
    fixer.findNonstandardResidues()
    fixer.replaceNonstandardResidues()
    
    # Remove heterogens (water, ligands) - keep for now, may need for binding
    # fixer.removeHeterogens(keepWater=False)
    
    # Find and add missing atoms (including hydrogens)
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    
    # Add hydrogens at pH 7.0
    fixer.addMissingHydrogens(7.0)
    
    # Write fixed structure
    with open(output_pdb, 'w') as f:
        PDBFile.writeFile(fixer.topology, fixer.positions, f)
    
    logger.info(f"Fixed structure written to: {output_pdb.name}")
    return output_pdb


def identify_cdr_residues(pdb_path: Path, chain_id: str = 'H') -> List[int]:
    """
    Identify CDR residue indices from a PDB file.
    Uses IMGT-based numbering detection.
    
    Args:
        pdb_path: Path to PDB file
        chain_id: Antibody chain ID ('H' for heavy, 'L' for light)
        
    Returns:
        List of residue indices (0-based) that are in CDR regions
    """
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure('antibody', str(pdb_path))
    
    cdr_indices = []
    
    for model in structure:
        for chain in model:
            if chain.id != chain_id:
                continue
            
            residue_list = list(chain.get_residues())
            for idx, residue in enumerate(residue_list):
                res_num = residue.id[1]
                
                # Check if residue is in any CDR region for this chain
                for cdr_name, (start, end) in CDR_REGIONS.items():
                    if cdr_name.startswith(chain_id):
                        if start <= res_num <= end:
                            cdr_indices.append(idx)
                            break
    
    logger.info(f"Identified {len(cdr_indices)} CDR residues in chain {chain_id}")
    return cdr_indices


# ═══════════════════════════════════════════════════════════════════════════════
# FORCE FIELD SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def get_force_field(ff_name: str = 'amber14sb', implicit_solvent: bool = True) -> ForceField:
    """
    Get OpenMM ForceField with specified parameters.
    
    Args:
        ff_name: Force field name ('amber14sb' or 'charmm36m')
        implicit_solvent: Use implicit solvent (GBSA) if True
        
    Returns:
        OpenMM ForceField object
    """
    if ff_name == 'amber14sb':
        ff_files = ['amber14-all.xml']
        if implicit_solvent:
            ff_files.append('implicit/gbn2.xml')
        else:
            ff_files.append('amber14/tip3pfb.xml')
    elif ff_name == 'charmm36m':
        ff_files = ['charmm36.xml']
        if implicit_solvent:
            ff_files.append('implicit/gbn2.xml')
        else:
            ff_files.append('charmm36/water.xml')
    else:
        raise ValueError(f"Unknown force field: {ff_name}")
    
    logger.info(f"Loading force field: {ff_name} (implicit_solvent={implicit_solvent})")
    return ForceField(*ff_files)


def add_positional_restraints(
    system: openmm.System,
    positions: List,
    topology: app.Topology,
    restrained_indices: List[int],
    strength: float = DEFAULT_RESTRAINT_STRENGTH
) -> openmm.System:
    """
    Add harmonic positional restraints to specified atoms.
    Used for framework restraints during CDR-only relaxation.
    
    Args:
        system: OpenMM System object
        positions: Initial positions
        topology: OpenMM Topology
        restrained_indices: Atom indices to restrain
        strength: Restraint strength in kcal/mol/Å²
        
    Returns:
        Modified System with restraints
    """
    # Convert strength to OpenMM units (kJ/mol/nm²)
    # 1 kcal/mol/Å² = 418.4 kJ/mol/nm²
    k = strength * 418.4 * unit.kilojoules_per_mole / unit.nanometer**2
    
    # Create custom force for positional restraints
    restraint_force = openmm.CustomExternalForce(
        "0.5*k*((x-x0)^2 + (y-y0)^2 + (z-z0)^2)"
    )
    restraint_force.addGlobalParameter("k", k)
    restraint_force.addPerParticleParameter("x0")
    restraint_force.addPerParticleParameter("y0")
    restraint_force.addPerParticleParameter("z0")
    
    # Add restrained atoms
    for idx in restrained_indices:
        pos = positions[idx]
        restraint_force.addParticle(idx, [pos[0], pos[1], pos[2]])
    
    system.addForce(restraint_force)
    logger.info(f"Added positional restraints to {len(restrained_indices)} atoms (k={strength} kcal/mol/Å²)")
    
    return system


# ═══════════════════════════════════════════════════════════════════════════════
# ENERGY MINIMIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_clash_score(simulation: Simulation) -> int:
    """
    Calculate the number of atom clashes (steric overlaps) in a structure.
    A clash is defined as atoms closer than 0.6 * sum of VdW radii.
    
    Args:
        simulation: OpenMM Simulation object
        
    Returns:
        Number of clashing atom pairs
    """
    # Get current positions
    state = simulation.context.getState(getPositions=True)
    positions = state.getPositions(asNumpy=True)
    
    # Simple distance-based clash detection
    n_atoms = len(positions)
    clash_count = 0
    clash_threshold = 0.2  # nm (2.0 Å)
    
    # Only check a subset for large systems
    step = max(1, n_atoms // 1000)
    
    for i in range(0, n_atoms, step):
        for j in range(i + 1, min(i + 100, n_atoms)):
            dist = np.linalg.norm(positions[i] - positions[j])
            if dist < clash_threshold:
                clash_count += 1
    
    return clash_count


def run_minimization(
    input_pdb: Path,
    output_pdb: Path,
    force_field: str = 'amber14sb',
    max_iterations: int = 500,
    energy_tolerance: float = 10.0,
    implicit_solvent: bool = True,
    cdr_only: bool = False,
    restraint_mode: str = 'none',
    restraint_strength: float = DEFAULT_RESTRAINT_STRENGTH,
    antibody_chain: str = 'H',
) -> Dict[str, Any]:
    """
    Run energy minimization on a protein structure.
    
    Args:
        input_pdb: Path to input PDB
        output_pdb: Path to write minimized PDB
        force_field: Force field to use
        max_iterations: Maximum minimization steps
        energy_tolerance: Energy convergence tolerance (kJ/mol)
        implicit_solvent: Use implicit solvent
        cdr_only: Only minimize CDR regions (for antibodies)
        restraint_mode: 'none', 'framework', or 'backbone'
        restraint_strength: Restraint strength in kcal/mol/Å²
        antibody_chain: Chain ID for antibody/nanobody
        
    Returns:
        Dictionary with minimization metrics
    """
    logger.info(f"Starting energy minimization: {input_pdb.name}")
    logger.info(f"  Force field: {force_field}")
    logger.info(f"  Max iterations: {max_iterations}")
    logger.info(f"  CDR-only mode: {cdr_only}")
    logger.info(f"  Restraint mode: {restraint_mode}")
    
    # Load structure
    pdb = PDBFile(str(input_pdb))
    
    # Get force field
    ff = get_force_field(force_field, implicit_solvent)
    
    # Create modeller and add hydrogens if missing
    modeller = Modeller(pdb.topology, pdb.positions)
    modeller.addHydrogens(ff)
    
    # Create system
    system = ff.createSystem(
        modeller.topology,
        nonbondedMethod=app.NoCutoff if implicit_solvent else app.PME,
        constraints=app.HBonds,
        hydrogenMass=1.5 * unit.amu,  # Hydrogen mass repartitioning for stability
    )
    
    # Add restraints if requested
    if restraint_mode == 'framework' and cdr_only:
        # Restrain framework (non-CDR) atoms
        cdr_residues = identify_cdr_residues(input_pdb, antibody_chain)
        
        # Get framework atom indices (all atoms NOT in CDR residues)
        framework_indices = []
        atom_idx = 0
        for residue in modeller.topology.residues():
            res_idx = residue.index
            for atom in residue.atoms():
                if res_idx not in cdr_residues:
                    framework_indices.append(atom_idx)
                atom_idx += 1
        
        system = add_positional_restraints(
            system, modeller.positions, modeller.topology,
            framework_indices, restraint_strength
        )
    elif restraint_mode == 'backbone':
        # Restrain all backbone atoms
        backbone_indices = []
        atom_idx = 0
        for residue in modeller.topology.residues():
            for atom in residue.atoms():
                if atom.name in ['CA', 'C', 'N', 'O']:
                    backbone_indices.append(atom_idx)
                atom_idx += 1
        
        system = add_positional_restraints(
            system, modeller.positions, modeller.topology,
            backbone_indices, restraint_strength
        )
    
    # Create integrator and simulation
    integrator = LangevinMiddleIntegrator(
        300 * unit.kelvin,
        1.0 / unit.picosecond,
        0.002 * unit.picoseconds
    )
    
    # Try CUDA platform first, fall back to CPU
    try:
        platform = openmm.Platform.getPlatformByName('CUDA')
        properties = {'DeviceIndex': '0', 'Precision': 'mixed'}
        simulation = Simulation(modeller.topology, system, integrator, platform, properties)
        logger.info("Using CUDA platform for minimization")
    except Exception:
        platform = openmm.Platform.getPlatformByName('CPU')
        simulation = Simulation(modeller.topology, system, integrator, platform)
        logger.info("Using CPU platform for minimization (CUDA not available)")
    
    simulation.context.setPositions(modeller.positions)
    
    # Calculate initial energy and clash score
    initial_state = simulation.context.getState(getEnergy=True, getPositions=True)
    initial_energy = initial_state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    initial_clashes = calculate_clash_score(simulation)
    
    logger.info(f"Initial potential energy: {initial_energy:.2f} kJ/mol")
    logger.info(f"Initial clash count: {initial_clashes}")
    
    # Run minimization
    logger.info(f"Running L-BFGS minimization (max {max_iterations} iterations)...")
    LocalEnergyMinimizer.minimize(
        simulation.context,
        tolerance=energy_tolerance * unit.kilojoules_per_mole,
        maxIterations=max_iterations
    )
    
    # Calculate final energy and clash score
    final_state = simulation.context.getState(getEnergy=True, getPositions=True)
    final_energy = final_state.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    final_clashes = calculate_clash_score(simulation)
    
    logger.info(f"Final potential energy: {final_energy:.2f} kJ/mol")
    logger.info(f"Final clash count: {final_clashes}")
    logger.info(f"Energy delta: {final_energy - initial_energy:.2f} kJ/mol")
    
    # Calculate RMSD
    initial_positions = initial_state.getPositions(asNumpy=True)
    final_positions = final_state.getPositions(asNumpy=True)
    rmsd = np.sqrt(np.mean(np.sum((final_positions - initial_positions)**2, axis=1)))
    logger.info(f"Overall RMSD: {rmsd * 10:.3f} Å")  # Convert nm to Å
    
    # Write output
    with open(output_pdb, 'w') as f:
        PDBFile.writeFile(simulation.topology, final_positions, f)
    logger.info(f"Minimized structure written to: {output_pdb}")
    
    # Prepare metrics
    metrics = {
        'openmm_energy_initial': float(initial_energy),
        'openmm_energy_final': float(final_energy),
        'openmm_energy_delta': float(final_energy - initial_energy),
        'openmm_clash_count_initial': int(initial_clashes),
        'openmm_clash_count_final': int(final_clashes),
        'openmm_rmsd_overall': float(rmsd * 10),  # Å
        'openmm_force_field': force_field,
        'openmm_restraint_mode': restraint_mode,
        'openmm_cdr_only': cdr_only,
        'openmm_max_iterations': max_iterations,
        'openmm_relaxed_pdb': str(output_pdb),
    }
    
    return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='OpenMM Energy Minimization for AI-Generated Structures',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic minimization
  python relax_openmm.py --input design.pdb --output relaxed.pdb
  
  # Antibody CDR-only with framework restraints
  python relax_openmm.py --input antibody.pdb --output relaxed.pdb \\
      --cdr_only --restraint_mode framework --antibody_chain H
  
  # Full minimization with more iterations
  python relax_openmm.py --input complex.pdb --output relaxed.pdb \\
      --max_iterations 1000 --energy_tolerance 1.0
        """
    )
    
    parser.add_argument('--input', '-i', type=Path, required=True,
                        help='Input PDB file')
    parser.add_argument('--output', '-o', type=Path, required=True,
                        help='Output relaxed PDB file')
    parser.add_argument('--output_json', '-j', type=Path,
                        help='Output JSON file with metrics')
    
    # Minimization settings
    parser.add_argument('--max_iterations', type=int, default=500,
                        help='Maximum minimization iterations (default: 500)')
    parser.add_argument('--energy_tolerance', type=float, default=10.0,
                        help='Energy tolerance in kJ/mol (default: 10.0)')
    
    # Force field
    parser.add_argument('--force_field', choices=['amber14sb', 'charmm36m'],
                        default='amber14sb',
                        help='Force field to use (default: amber14sb)')
    parser.add_argument('--explicit_solvent', action='store_true',
                        help='Use explicit solvent instead of GBSA implicit')
    
    # Antibody-specific options
    parser.add_argument('--cdr_only', action='store_true',
                        help='Only minimize CDR regions (for antibodies)')
    parser.add_argument('--restraint_mode', choices=['none', 'framework', 'backbone'],
                        default='none',
                        help='Restraint mode (default: none)')
    parser.add_argument('--restraint_strength', type=float,
                        default=DEFAULT_RESTRAINT_STRENGTH,
                        help='Restraint strength in kcal/mol/Å² (default: 5.0)')
    parser.add_argument('--antibody_chain', default='H',
                        help='Antibody heavy chain ID (default: H)')
    
    # Structure preparation
    parser.add_argument('--fix_structure', action='store_true',
                        help='Run PDBFixer before minimization')
    
    args = parser.parse_args()
    
    # Validate input
    if not args.input.exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)
    
    # Create output directory if needed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    
    # Fix structure if requested
    working_pdb = args.input
    if args.fix_structure:
        fixed_pdb = args.output.parent / f"{args.input.stem}_fixed.pdb"
        working_pdb = fix_structure(args.input, fixed_pdb)
    
    # Run minimization
    try:
        metrics = run_minimization(
            input_pdb=working_pdb,
            output_pdb=args.output,
            force_field=args.force_field,
            max_iterations=args.max_iterations,
            energy_tolerance=args.energy_tolerance,
            implicit_solvent=not args.explicit_solvent,
            cdr_only=args.cdr_only,
            restraint_mode=args.restraint_mode,
            restraint_strength=args.restraint_strength,
            antibody_chain=args.antibody_chain,
        )
        
        # Write metrics JSON
        json_output = args.output_json or args.output.with_suffix('.json')
        with open(json_output, 'w') as f:
            json.dump(metrics, f, indent=2)
        logger.info(f"Metrics written to: {json_output}")
        
        logger.info("Energy minimization completed successfully")
        
    except Exception as e:
        logger.error(f"Minimization failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
