# Project Status: 2025 De Novo Protein Design System

## Current State

This repository contains a complete copy of ProteinDJ v1.1 (from PapenfussLab/proteindj), uploaded November 15, 2025.

### What's Here Now

**Pipeline Infrastructure:**
- Nextflow DSL2 workflow system
- Apptainer/Singularity container orchestration
- GPU-aware task scheduling and batching
- Resume/caching capability
- 8 RFdiffusion design modes (monomer/binder × denovo/foldcond/motifscaff/partialdiff)

**Modules (Current):**
- RFdiffusion: Backbone generation
- ProteinMPNN: Sequence design
- Full-Atom MPNN (FAMPNN): Sequence design with side chains
- AlphaFold2-Initial-Guess: Structure prediction (2021)
- Boltz: Structure prediction (version unclear - likely Boltz-1)
- PyRosetta: Analysis and metrics

**Configuration:**
- Works on HPC clusters with SLURM
- Apptainer containers from cloud storage
- Model weights downloaded separately (~15GB)
- 4 GPU default, 8 CPUs per GPU

## Project Goals

Build a state-of-the-art protein design system for workstation deployment by extending ProteinDJ with:

1. **Modern prediction models** - Replace outdated AF2-Initial-Guess with Boltz-2, Chai-1, ColabFold
2. **Enhanced sequence design** - Add LigandMPNN for ligand/metal/DNA binding
3. **New pipeline stages** - Add DiffDock (docking) and OpenMM (MD validation)
4. **Experimental features** - Test Genie 2 as RFdiffusion alternative
5. **Workstation optimization** - GPU-tolerant configuration (easy scale up/down)

## Work To Be Done

### Phase 1: Core Model Upgrades

**Replace outdated folding models:**
- Remove: af2.nf module (AlphaFold2-Initial-Guess from 2021)
- Add: boltz2.nf module (Boltz-2 with binding affinity prediction)
- Add: chai1.nf module (Chai-1 with PLM embeddings)
- Add: colabfold.nf module (fast MSA-based prediction)

**Add ligand-aware sequence design:**
- Add: ligandmpnn.nf module (63% recovery vs 50% for small molecules)
- Keep: proteinmpnn.nf and fampnn.nf (existing tools still useful)

### Phase 2: New Capabilities

**Docking validation:**
- Add: diffdock.nf module (protein-ligand docking)
- Create new pipeline stage between prediction and analysis

**MD simulation validation:**
- Add: openmm.nf module (molecular dynamics)
- Create new pipeline stage for stability assessment

### Phase 3: Experimental Extensions

**Alternative backbone generation:**
- Add: genie2.nf module (0.96 designability vs RFdiffusion's 0.63)
- Implement as experimental option alongside RFdiffusion
- Support multi-motif scaffolding

### Phase 4: Workstation Profile

**Create workstation-specific configuration:**
- Local Docker container support (vs cloud Apptainer)
- Simple GPU scaling (change one number: params.gpus)
- Local model storage paths
- Optimized batch sizes for consumer GPUs

## Deferred/Out of Scope

- AlphaFold3: Not openly available
- AlphaProteo: Requires DeepMind approval
- BindCraft: Requires 32GB GPU, complex integration
- EvoBind2: Different use case (peptides vs proteins)

## Success Criteria

The system is complete when:

1. All Phase 1 models are integrated and tested
2. Example workflows run successfully on target workstation
3. GPU count can be changed via single parameter
4. Pipeline resumes correctly after failures
5. Documentation covers all new modules and modes
