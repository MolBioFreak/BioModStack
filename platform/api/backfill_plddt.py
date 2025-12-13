#!/usr/bin/env python3
"""
Backfill residue_plddt for existing designs in the database.

This script updates designs that have a pdb_path but no residue_plddt data,
using Biotite to extract per-residue B-factors from both PDB and CIF files.
"""

import asyncio
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import select, update
from database import engine, async_session, Design
from services.structure_utils import get_residue_plddt


async def backfill_residue_plddt():
    """Update all designs missing residue_plddt."""
    
    async with async_session() as session:
        # Find designs with pdb_path but no residue_plddt
        result = await session.execute(
            select(Design).where(
                Design.pdb_path.isnot(None),
                Design.residue_plddt.is_(None)
            )
        )
        designs = result.scalars().all()
        
        print(f"Found {len(designs)} designs missing residue_plddt")
        
        updated = 0
        for design in designs:
            try:
                pdb_path = Path(design.pdb_path)
                if not pdb_path.exists():
                    print(f"  [SKIP] {design.name}: File not found: {pdb_path}")
                    continue
                
                avg_plddt, per_residue = get_residue_plddt(pdb_path)
                
                if per_residue:
                    design.residue_plddt = per_residue
                    updated += 1
                    print(f"  [OK] {design.name}: {len(per_residue)} residues, avg={avg_plddt:.1f}")
                else:
                    print(f"  [WARN] {design.name}: No B-factors found")
                    
            except Exception as e:
                print(f"  [ERR] {design.name}: {e}")
        
        await session.commit()
        print(f"\nUpdated {updated}/{len(designs)} designs")


if __name__ == "__main__":
    asyncio.run(backfill_residue_plddt())
