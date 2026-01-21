# OpenMM Integration Audit — 2026-01-21

## Summary

This document summarizes the OpenMM physics refinement integration into BioModStack, comparing the original plan with what was actually implemented.

---

## Implementation Status

| Component | Planned | Status | Notes |
|-----------|---------|--------|-------|
| **Container** | `openmm.sif` (CUDA 12.1 + OpenMM 8.4) | ✅ Complete | Built with pdbfixer from GitHub (not PyPI) |
| **Backend Module** | `modules/openmm.nf` | ✅ Complete | 4 processes + workflow |
| **Frontend UI** | `PhysicsRefinementPanel.tsx` | ✅ Complete | Reusable component with tooltips |
| **Template Integration** | 4 templates | ✅ Complete | AntibodyDenovo, BindCraft, BoltzGen, Mutagenesis |
| **Backend API Wiring** | `jobs.py` params → Nextflow | ❌ Pending | Parameters not yet passed to Nextflow command |
| **Results Viewer** | New columns/charts | ❌ Pending | MM-GBSA, ΔG, clash count not yet exposed |
| **Database Migration** | OpenMM fields | ❌ Pending | Schema changes not applied |

---

## What Was Implemented

### Phase 1: Container (`apptainer/openmm.def`)
- Base: `nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04`
- Python packages: `openmm`, `pdbfixer`, `openmm-ml`, `mace-torch`, `torchani`, `mdtraj`, `biopython`
- Size: ~4.5 GB

### Phase 4: Frontend (`PhysicsRefinementPanel.tsx`)
Reusable component integrated into all design templates:
- **Master toggle**: `openmm_enabled`
- **Compute tiers**: Fast, Standard, Full (with presets)
- **Force field**: AMBER14SB, MACE-OFF, ANI-2x
- **CDR-only mode**: For antibody/nanobody workflows
- **MM-GBSA**: Interface scoring (Full tier only)
- **Advanced settings**: Max iterations, tolerance, restraint strength, implicit solvent, platform

---

## What Remains (Blockers)

### Critical: Backend API Wiring
The frontend collects all params but `jobs.py` doesn't yet:
1. Extract `physicsSettings` from job params
2. Convert to Nextflow CLI args (`--openmm_enabled`, `--openmm_compute_tier`, etc.)
3. Pass to `nextflow run` command

### Recommended Next Steps
1. **Wire backend** - ~2 hours
2. **Add Results Viewer columns** - ~2 hours  
3. **Run end-to-end test** - ~1 hour
4. **Database migration** - ~30 min

---

## Files Modified

| File | Change |
|------|--------|
| `apptainer/openmm.def` | New container definition |
| `modules/openmm.nf` | New Nextflow module |
| `main.nf` | Added OpenMM import |
| `workflows/bindcraft_design.nf` | OpenMM integration after Boltz-2 |
| `platform/frontend/src/components/PhysicsRefinementPanel.tsx` | New component |
| `platform/frontend/src/components/AntibodyDenovoTemplate.tsx` | Panel integration |
| `platform/frontend/src/components/BindCraftTemplate.tsx` | Panel integration |
| `platform/frontend/src/components/BoltzGenTemplate.tsx` | Panel integration |
| `platform/frontend/src/components/MutagenesisTemplate.tsx` | Panel integration |

---

## References

- Original Plan: `docs/OpenMM_Integration_Plan.md`
- Knowledge Item: `molecular_dynamics_and_refinement_research`
- Container location: `apptainer/openmm.sif` (after build)
