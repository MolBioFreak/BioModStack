# BioModStack (BMS)

**A high-performance computational platform for protein design, structural bioinformatics, and laboratory data analysis.**

[![RTX 5090 Ready](https://img.shields.io/badge/GPU-RTX%205090-76B900?logo=nvidia)](https://nvidia.com)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react)](https://react.dev)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-3776AB?logo=python)](https://python.org)
[![Nextflow DSL2](https://img.shields.io/badge/Nextflow-DSL2-1DB954)](https://nextflow.io)

---

## Overview

BioModStack evolved from a ProteinDJ fork into a unified workstation for:

- **De Novo Protein Design** – RFantibody+, BindCraft, BoltzGen
- **Structural Validation** – Boltz-2, RoseTTAFold3, OpenMM physics refinement
- **Laboratory Analytics** – qPCR (QuantStudio 5), HPLC data processing with MIQE compliance
- **AI Orchestration** – Mixture-of-Morons (M-o-M) ensemble for OCR/document analysis

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
│   ├── openmm.nf           # Physics refinement (NEW)
│   └── ...
├── workflows/              # Workflow compositions
├── platform/
│   ├── api/                # FastAPI backend
│   └── frontend/           # React 19 UI
├── scripts/                # Python utilities
└── apptainer/              # Container definitions
```

## GPU Requirements

| GPU | VRAM | Supported Workflows |
|-----|------|---------------------|
| RTX 5090 | 32GB | All (priority) |
| RTX 3090 | 24GB | All |
| RTX 5060 Ti | 16GB | Structure prediction, lightweight design |

## Key Features

### Physics Refinement (OpenMM)
- Energy minimization with L-BFGS
- MM-GBSA binding affinity scoring
- CDR-only mode for antibody workflows
- AMBER14SB / CHARMM36m force fields

### Analytics Dashboard
- 12+ Plotly charts for design metrics
- pLDDT profiles, PAE heatmaps
- Parallel coordinates for multi-dimensional filtering
- Publication-quality exports

### Laboratory Data Processing
- **qPCR**: QuantStudio 5 EDS parsing, ΔΔCt, absolute quantification
- **HPLC**: Peak deconvolution, calibration curves
- MIQE-compliant QC and RDML export

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
