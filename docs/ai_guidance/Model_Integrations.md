Model Integrations
==================

Purpose
-------
Canonical list of integrated models/tools, internal documentation links, and
external code/paper references when documented in this repo. Update this file
whenever a model is added/removed or its integration changes.

Registry Models (platform/api/config/models/*.yaml)
---------------------------------------------------
Each entry is backed by a YAML definition loaded by `platform/api/model_registry.py`.

1) **af2 (AlphaFold2)**
   - Internal: `docs/installation.md`, `docs/parameters.md`, `docs/WORKSTATION_SETUP.md`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

2) **rfdiffusion**
   - Internal: `docs/modes.md`, `docs/installation.md`, `docs/parameters.md`
   - External code: https://github.com/RosettaCommons/RFdiffusion
   - Paper/Preprint: not referenced in repo

3) **proteinmpnn**
   - Internal: `docs/parameters.md`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

4) **fampnn (Full-Atom MPNN)**
   - Internal: `docs/FAMPNN_CONSTRAINTS_UPDATE.md`, `docs/parameters.md`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

5) **boltz2**
   - Internal: `docs/installation.md`, `docs/parameters.md`
   - External code: https://huggingface.co/boltz-community/boltz-2
   - Paper/Preprint: not referenced in repo

6) **rf3 (RosettaFold3 / Foundry)**
   - Internal: `docs/foundry_discrepancies.md`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

7) **ligandmpnn**
   - Internal: not referenced in repo
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

8) **bindcraft**
   - Internal: `docs/parameters.md`, `workflows/bindcraft_design.nf`
   - External code: https://github.com/martinpacesa/BindCraft
   - Paper/Preprint: not referenced in repo

9) **diffdock**
   - Internal: `platform/diffdock_ui.py`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

10) **unidock**
   - Internal: `docs/parameters.md`
   - External code: https://github.com/dptech-corp/Uni-Dock
   - Paper/Preprint: not referenced in repo

11) **boltzgen**
   - Internal: `modules/boltzgen.nf`, `workflows/bindcraft_design.nf` (orchestration)
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

12) **oligo_design (RFDpoly)**
   - Internal: `docs/OligoDesigner_Implementation_Plan.md`, `workflows/oligo_design.nf`
   - External code: https://github.com/RosettaCommons/RFDpoly
   - Paper/Preprint: https://www.biorxiv.org/content/10.1101/2025.10.01.679929v1
   - Documentation: https://rosettacommons.github.io/RFDpoly/

13) **mutagenesis**
   - Internal: `docs/FrustraMPNN_Integration_Plan.md`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

14) **antibody_denovo**
   - Internal: `workflows/antibody_denovo.nf`, `docs/RFA_PPIFlow_Implementation_Plan_Final.md`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

15) **antibody_design**
   - Internal: `workflows/antibody_design.nf`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

16) **docking (meta)**
   - Internal: `platform/api/config/models/docking.yaml`
   - External code: not referenced in repo
   - Paper/Preprint: not referenced in repo

Child/Hidden Registry Models
----------------------------
- `rfantibody_child`, `fampnn_child`, `antibody_child`, `boltzgen_child`
- Purpose: internal orchestration for multi-job workflows.

Workflow / Module Integrations (not registry-backed)
----------------------------------------------------
These are invoked in Nextflow workflows but do not appear as registry models.

- **RFantibody** (`modules/rfantibody.nf`)
  - Internal: `docs/RFA_PPIFlow_Implementation_Plan_Final.md`
  - External code: https://github.com/RosettaCommons/RFantibody
  - Paper/Preprint: not referenced in repo

- **PPIFlow** (`modules/ppiflow.nf`)
  - Internal: `docs/RFA_PPIFlow_Implementation_Plan_Final.md`
  - External code: https://github.com/Mingchenchen/PPIFlow
  - Paper/Preprint: https://www.biorxiv.org/content/10.64898/2026.01.19.700484

- **AntiFold** (`modules/antifold.nf`)
  - Internal: `workflows/antibody_denovo.nf`
  - External code: not referenced in repo
  - Paper/Preprint: not referenced in repo

- **AntiBERTy** (`modules/antiberty.nf`)
  - Internal: `workflows/antibody_denovo.nf`
  - External code: not referenced in repo
  - Paper/Preprint: not referenced in repo

- **ThermoMPNN** (`modules/thermompnn.nf`)
  - Internal: `workflows/antibody_denovo.nf`
  - External code: not referenced in repo
  - Paper/Preprint: not referenced in repo

- **IgGM** (`modules/iggm.nf`)
  - Internal: `workflows/antibody_denovo.nf`
  - External code: not referenced in repo
  - Paper/Preprint: not referenced in repo

- **ImmuneBuilder** (`modules/immunebuilder.nf`)
  - Internal: `workflows/antibody_design.nf`
  - External code: not referenced in repo
  - Paper/Preprint: not referenced in repo

- **FrustraMPNN** (`modules/frustrampnn.nf`)
  - Internal: `docs/FrustraMPNN_Integration_Plan.md`
  - External code: not referenced in repo
  - Paper/Preprint: not referenced in repo

- **ANARCII / ANARCI** (`modules/utils/anarci.nf`)
  - Internal: used by antibody workflows
  - External code: https://github.com/oxpig/ANARCI
  - Paper/Preprint: not referenced in repo

- **OpenMM (planned/optional)** (`docs/OpenMM_Integration_Plan.md`)
  - External code: https://github.com/openmm/openmm-ml
  - Related tooling: https://github.com/ACEsuit/mace-off, https://github.com/aiqm/torchani, https://github.com/openmm/pdbfixer

Utility Dependencies (not models)
---------------------------------
- **MMseqs2 / ColabFold DB**: local MSA generation (`scripts/run_local_msa.py`, `scripts/batch_msa.py`)
- **Apptainer/Singularity containers**: `apptainer/*.sif`, configured via `params.container_dir`

How to Add a New Model (Standardized Methodology)
-------------------------------------------------
1) **Model registry YAML**
   - Add `platform/api/config/models/<model_id>.yaml`
   - Required fields: `id`, `name`, `version`, `category`, `description`, `container`
   - Define `modes`, `params`, `inputs`, `outputs` as needed

2) **Nextflow module/workflow**
   - Add `modules/<model_id>.nf` (process definition)
   - Add `workflows/<model_id>.nf` if it is a top-level workflow
   - Wire into `main.nf` if it participates in the central pipeline

3) **Containers / weights**
   - Ensure container exists in `params.container_dir`
   - Mount weights via `params.weights_root` or model-specific params

4) **API + UI**
   - Verify `platform/api/model_registry.py` picks up the YAML
   - Add UI integration (templates, defaults, param panels) if user-facing
   - Add any scheduler rules (GPU/CPU labels) if required

5) **Docs**
   - Update `docs/parameters.md` and any relevant integration plan
   - Update `docs/ai_guidance/Model_Integrations.md` (this file)
   - If new env paths are required, update `docs/ai_guidance/Centralization_and_Standardization.md`

6) **Testing**
   - Add a minimal run path (small input) for validation
   - Confirm ingestion outputs are parsed and visible in UI
