# BioModStack (BMS)

**A high-performance computational platform for protein design, structural bioinformatics, and laboratory data analysis.**

[![Ampere+ GPUs](https://img.shields.io/badge/GPU-Ampere%2B-76B900?logo=nvidia)](https://nvidia.com)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react)](https://react.dev)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python)](https://python.org)
[![Nextflow DSL2](https://img.shields.io/badge/Nextflow-DSL2-1DB954)](https://nextflow.io)

---

## Overview

BioModStack evolved from a ProteinDJ fork into a unified workstation for:

- **De Novo Protein Design** – RFantibody+, BindCraft, BoltzGen
- **Structural Validation** – Boltz-2, RoseTTAFold3, OpenMM physics refinement
- **Results Analysis** – Plotly-powered analytics dashboard with publication-quality charts

## Workflows

| Workflow | Description | Key Features |
|----------|-------------|--------------|
| **RFantibody+** | VHH nanobody & full Fv antibody design | IMGT framework protection, SAbDab scaffolds |
| **BindCraft** | De novo minibinder design via AF2 backprop | SWA parallelization, Boltz-2 validation |
| **BoltzGen** | Ligand-aware scaffold generation | Nanobody mode, CDR loop sampling, covalent constraints |
| **Mutagenesis** | Variant library generation | Shared MSA optimization, batch structure prediction |
| **Structure Prediction** | Single-sequence or complex prediction | Boltz-2, RoseTTAFold3 |

## Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/MolBioFreak/Protein-De-Novo-Modification-and-Design-Platform.git
cd Protein-De-Novo-Modification-and-Design-Platform

# 2. Start services
./start_ui.sh

# 3. Open browser
# http://localhost:5173 (Frontend)
# http://localhost:8100 (API)
```

## Architecture

```
├── main.nf                 # Nextflow entry point
├── modules/                # Nextflow process definitions
│   ├── rfantibody.nf       # RFantibody backbone generation
│   ├── boltz.nf            # Boltz-2 structure prediction
│   ├── openmm.nf           # Physics refinement
│   └── ...
├── workflows/              # Workflow compositions
├── platform/
│   ├── api/                # FastAPI backend
│   └── frontend/           # React 19 UI
├── scripts/                # Python utilities
└── apptainer/              # Container definitions
```

## GPU Requirements

**Minimum**: NVIDIA Ampere architecture (RTX 30-series) or newer  
**Recommended**: Ada Lovelace / Blackwell (RTX 40/50-series) with 16GB+ VRAM

| VRAM | Supported Workflows |
|------|---------------------|
| 24GB+ | All workflows at full throughput |
| 16GB | Structure prediction, lightweight design |
| 12GB | Limited batch sizes, single-sample inference |

> All containers built with PyTorch 2.5+ and CUDA 12.4 for Ampere/Ada/Blackwell compatibility.

## Key Features

### Visualization & Editing
- **PDBe Molstar** – 3D structural viewer with interactive epitope/residue selection
- **Open Vector Editor (OVE)** – React 19 port of Teselagen's DNA/RNA sequence editor
- **Plotly Analytics Dashboard** – 12+ interactive charts for design metrics

### Physics Refinement (OpenMM 8.4)
- L-BFGS energy minimization with configurable iterations
- MM-GBSA binding affinity scoring (ΔG)
- CDR-only mode for antibody workflows
- AMBER14SB / CHARMM36m force fields
- MACE-torch / TorchANI neural network potentials

### Structure Prediction
- **Boltz-2** – Binding affinity + structure prediction
- **RoseTTAFold3** – All-atom complex prediction
- **DiffDock** – Molecular docking
- **Uni-Dock** – GPU-accelerated AutoDock Vina

### Sequence Design
- **ProteinMPNN** – Backbone-to-sequence
- **FAMPNN** – Full-atom side chain modeling
- **LigandMPNN** – Ligand/metal/DNA-aware design
- **AntiFold** – Antibody-specific inverse folding

### GPU Orchestration
- VRAM-aware bin-packing across heterogeneous GPUs
- Dynamic job scheduling with priority queues
- Real-time utilization monitoring

## Container Management

Build containers with Apptainer:

```bash
cd apptainer
apptainer build boltz2.sif boltz2.def
apptainer build openmm.sif openmm.def
```

## Documentation

- [Installation Guide](docs/installation.md)
- [Workflow Modes](docs/modes.md)
- [Parameters Reference](docs/parameters.md)
- [Metrics Reference](docs/metrics.md)

## License

Academic use. See LICENSE for details.

---

*Originally forked from [PapenfussLab/ProteinDJ](https://github.com/PapenfussLab/proteindj). Extensively modified and rebranded as BioModStack (January 2026).*
