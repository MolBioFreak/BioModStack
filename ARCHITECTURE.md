# System Architecture: BioModStack

## Overview

BioModStack is a Nextflow-based computational biology platform featuring modern prediction models, ligand-aware sequence design, and physics-based validation stages.

## Pipeline Stages

```
Stage 1: BACKBONE GENERATION
  Input: Design parameters (contigs, hotspots, etc.)
  Process: Generate protein backbone structures
  Options:
    - RFdiffusion (default, 8 modes)
    - Genie 2 (experimental, higher designability)
  Output: PDB files + metadata JSON

Stage 2: SEQUENCE DESIGN
  Input: Backbone PDB files
  Process: Design amino acid sequences
  Options:
    - ProteinMPNN (default)
    - Full-Atom MPNN (side chain modeling)
    - LigandMPNN (for ligand/metal/DNA binding)
  Output: Sequence PDB files + scores

Stage 3: STRUCTURE PREDICTION
  Input: Sequence PDB files
  Process: Predict 3D structure, validate design
  Options:
    - Boltz-2 (binding affinity + structure)
    - Chai-1 (language model enhanced)
    - ColabFold (fast, MSA-based)
  Output: Predicted structure PDBs + confidence metrics

Stage 4: DOCKING (NEW)
  Input: Predicted structures + ligand
  Process: Predict protein-ligand binding pose
  Tool: DiffDock
  Output: Docked complexes + scores
  When: Optional, if ligand provided

Stage 5: MD VALIDATION (NEW)
  Input: Predicted or docked structures
  Process: Molecular dynamics simulation
  Tool: OpenMM
  Output: Trajectories + energy profiles
  When: Optional, for stability assessment

Stage 6: ANALYSIS
  Input: Final structures from previous stages
  Process: Calculate metrics, generate reports
  Tool: PyRosetta + custom scripts
  Output: CSV with all metrics, filtered results
```

## Directory Structure

```
Protein-De-Novo-Modification-and-Design-Platform/
├── main.nf                       # Main workflow entry point
├── nextflow.config               # Configuration + profiles
│
├── modules/                      # Process definitions
│   ├── rfdiffusion.nf           # [KEEP] RFdiffusion processes
│   ├── genie2.nf                # [ADD] Genie 2 processes
│   ├── proteinmpnn.nf           # [KEEP] ProteinMPNN processes
│   ├── fampnn.nf                # [KEEP] FAMPNN processes
│   ├── ligandmpnn.nf            # [ADD] LigandMPNN processes
│   ├── af2.nf                   # [KEEP] AlphaFold2 processes
│   ├── boltz.nf                 # [UPDATE] Replace with Boltz-2
│   ├── chai1.nf                 # [ADD] Chai-1 processes
│   ├── colabfold.nf             # [ADD] ColabFold processes
│   ├── diffdock.nf              # [ADD] DiffDock processes
│   ├── openmm.nf                # [ADD] OpenMM processes
│   ├── analysis.nf              # [KEEP] PyRosetta analysis
│   ├── publish.nf               # [KEEP] Results publishing
│   ├── compress.nf              # [KEEP] File compression
│   └── combine_metadata.nf      # [KEEP] Metadata aggregation
│
├── workflows/                    # Workflow compositions
│   ├── rfdiffusion.nf           # [KEEP] RFdiffusion sub-workflow
│   └── extended_pipeline.nf     # [ADD] Full extended workflow
│
├── apptainer/                    # Container definitions
│   ├── rfdiffusion.def          # [KEEP] RFdiffusion container
│   ├── genie2.def               # [ADD] Genie 2 container
│   ├── proteinmpnn.def          # [KEEP] via dl_binder_design.def
│   ├── fampnn.def               # [KEEP] FAMPNN container
│   ├── ligandmpnn.def           # [ADD] LigandMPNN container
│   ├── boltz2.def               # [UPDATE] Ensure Boltz-2 version
│   ├── chai1.def                # [ADD] Chai-1 container
│   ├── colabfold.def            # [ADD] ColabFold container
│   ├── diffdock.def             # [ADD] DiffDock container
│   ├── openmm.def               # [ADD] OpenMM container
│   └── pyrosetta_tools.def      # [KEEP] Analysis tools
│
├── scripts/                      # Python helper scripts
│   ├── filter_*.py              # [KEEP + ADD] Filtering scripts
│   ├── align_*.py               # [KEEP + ADD] Alignment scripts
│   ├── prep_*.py                # [KEEP + ADD] Preparation scripts
│   └── metadata_converter.py    # [KEEP] Metadata handling
│
├── lib/                          # Groovy libraries
│   └── Utils.groovy             # [KEEP + EXTEND] Helper functions
│
├── models/                       # Model weights (gitignored)
│   ├── rfd/                     # RFdiffusion checkpoints
│   ├── genie2/                  # [ADD] Genie 2 weights
│   ├── boltz/                   # [UPDATE] Boltz-2 weights
│   └── chai1/                   # [ADD] Chai-1 weights
│
└── docs/                         # Documentation
    ├── installation.md          # Installation guide
    ├── modes.md                 # Design modes guide
    ├── parameters.md            # Parameter reference
    ├── metrics.md               # Metrics and metadata guide
    ├── scaffolds.md             # Scaffold generation guide
    ├── WORKSTATION_SETUP.md     # Workstation setup
    └── OpenMM_Integration_Plan.md # OpenMM integration plan
```

## Configuration System

### Parameters

**Essential:**
- `rfd_mode`: Design mode (monomer_denovo, binder_denovo, etc.)
- `rfd_num_designs`: Number of backbone designs to generate
- `seqs_per_design`: Sequences per backbone design
- `out_dir`: Output directory path

**Method Selection:**
- `backbone_method`: 'rfdiffusion' or 'genie2'
- `seq_method`: 'mpnn', 'fampnn', or 'ligandmpnn'
- `pred_method`: 'boltz', 'chai1', or 'colabfold'
- `run_docking`: Boolean, enable DiffDock stage
- `run_md`: Boolean, enable OpenMM stage

**GPU Configuration:**
- `gpus`: Number of GPUs (4 default)
- `cpus_per_gpu`: CPUs per GPU task (8 default)
- `gpu_model`: GPU type for SLURM ('A30' default)
- `gpu_queue`: SLURM queue/partition

### Profiles

**Available:**
- Design modes: monomer_denovo, binder_denovo, antibody_denovo, etc.
- Execution: apptainer, singularity
- `workstation`: Local execution, simplified paths
- `experimental`: Enable experimental features

### Process Labels

**Resource Labels:**
- `gpu`: Requires GPU, gets 1 GPU + cpus_per_gpu
- Default: CPU-only, gets cpus + memory_cpu

**Container Labels:**
- `RFDiffusion`: RFdiffusion container + models
- `Genie2`: Genie 2 container + weights
- `MPNN`: ProteinMPNN container
- `FAMPNN`: Full-Atom MPNN container
- `LigandMPNN`: LigandMPNN container
- `Boltz`: Boltz-2 container + models
- `Chai1`: Chai-1 container + weights
- `ColabFold`: ColabFold container
- `DiffDock`: DiffDock container
- `OpenMM`: OpenMM container
- `pyrosetta_tools`: PyRosetta for analysis/filtering

## Data Flow

### Channels

Nextflow channels carry data between stages:

```groovy
// Stage 1 output
rfd_pdbs_jsons = Channel of [List<PDB>, List<JSON>]

// After filtering
filt_rfd_pdbs_jsons = Channel of [List<PDB>, List<JSON>]

// Stage 2 output
seq_pdbs = Channel of List<PDB>

// After filtering
filt_seq_pdbs = Channel of List<PDB>

// Stage 3 output
pred_pdbs_jsons = Channel of [List<PDB>, List<JSON>]

// After filtering
analysis_input_pdbs = Channel of List<PDB>

// Final
final_pdbs = Channel of List<PDB>
```

### Batching

BioModStack uses intelligent batching:

**GPU-aware batching:**
- Splits work across available GPUs
- Uses `Utils.rebatchGPU()` to assign batch IDs
- Each batch gets one GPU via round-robin

**Size-aware batching:**
- Large proteins get smaller batches (fewer per GPU)
- Uses `Utils.rebatchGPUByNumRes()` for prediction

**CPU batching:**
- Filters/analysis use large batches (50-200 files)
- Uses `Utils.rebatchTuples()` for efficient processing

### Metadata

Metadata flows through topic channels:

```groovy
// Topic channels collect metadata from all processes
channel.topic('metadata_ch_fold')       // Fold-level metadata
channel.topic('metadata_ch_fold_seq')   // Sequence-level metadata

// Combined at end into all_designs.csv
CombineMetadata(metadata_fold, metadata_fold_seq)
  .csv
  .collectFile(name: "all_designs.csv")
```

## Container Strategy

### Remote Containers

Containers can be built locally or pulled via Apptainer:
```
apptainer/
├── rfdiffusion.def → rfdiffusion.sif
├── boltz2.def → boltz2.sif
├── openmm.def → openmm.sif
├── fampnn.def → fampnn.sif
└── pyrosetta_tools.def → pyrosetta_tools.sif
```

### Extended (Workstation)

Use local Docker images:
```
images/
├── rfdiffusion/     (keep baseline version)
├── genie2/          (new)
├── proteinmpnn/     (keep baseline version)
├── fampnn/          (keep baseline version)
├── ligandmpnn/      (new)
├── boltz2/          (your existing Boltz-2)
├── chai1/           (your existing Chai-1)
├── colabfold/       (your existing ColabFold)
├── diffdock/        (your existing DiffDock)
├── openmm/          (your existing OpenMM)
└── metrics/         (PyRosetta tools)
```

### Binding Strategy

Apptainer uses `--bind` to mount host paths into containers:

```bash
--bind /host/path:/container/path
--nv                                    # GPU access
```

**Model weights:** Mounted at fixed paths inside container
**Scripts:** Mounted to /scripts for Python access
**Working dir:** Mounted for input/output

## GPU Tolerance

### Current GPU Assignment

```groovy
process {
    withLabel: 'gpu' {
        clusterOptions = '--gres=gpu:${params.gpu_model}:1'
        cpus = "${params.cpus_per_gpu}"
        memory = "${params.memory_gpu}"
    }
}
```

**For SLURM:** Uses `--gres=gpu:A30:1` to request 1 GPU per task

**For local/Docker:** Will use `--gpus device=N` with round-robin assignment

### Scaling

To change GPU count:

```groovy
// nextflow.config
params.gpus = 4  // Change to 2, 6, 8, etc.
```

Nextflow automatically:
- Adjusts parallel task limit (`maxForks = params.gpus`)
- Rebalances batches across GPUs
- No code changes needed

## Failure Handling

### Resume

```bash
nextflow run main.nf -resume
```

Nextflow caches completed tasks, only reruns failed/incomplete steps.

### Filters

Each stage can filter designs:
- RFD: Secondary structure, radius of gyration
- Sequence: MPNN score, FAMPNN PSCE
- Prediction: pLDDT, PAE, RMSD, binding metrics

Failed designs are logged but don't stop pipeline.

### Placeholders

If all designs filtered out:
```groovy
.ifEmpty(file("${projectDir}/lib/placeholder.pdb"))
```

Ensures downstream stages always have input.

## Extension Points

### Adding New Backbone Generator

1. Create `modules/newmodel.nf` with RunNewModel process
2. Add container label in nextflow.config
3. Modify main.nf Stage 1 to support new option
4. Add model weights path parameter

### Adding New Predictor

1. Create `modules/newpred.nf` with RunNewPred process
2. Add PrepNewPred if input format differs from PDB
3. Add FilterNewPred with appropriate metrics
4. Add container label in nextflow.config
5. Modify main.nf Stage 3 to support new pred_method

### Adding New Pipeline Stage

1. Create `modules/newstage.nf` with process definitions
2. Add to main.nf after appropriate stage
3. Make optional via `params.run_newstage` boolean
4. Connect input/output channels
5. Add to PublishResults for final report

## Testing Strategy

Test profiles are available:
```bash
nextflow run main.nf -profile test,monomer_denovo
```

Runs with minimal designs (4 designs × 2 sequences) for quick validation.

- Test each module independently
- Test end-to-end with test profile
- Verify GPU scaling (2, 4, 6 GPUs)
- Verify resume works after interruption
