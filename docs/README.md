# BioModStack handbook

**New to BMS?** Use the [workspace guide](Workspaces.md) to find the right screen. If you maintain BMS, start with the [service and module map](Services.md). For a first Development installation, follow [Nonproduction Installation](Nonproduction_Installation.md), not a model-specific setup or an old deployment plan.

## What are you trying to do?

| Task | Start here | What the guide covers |
|---|---|---|
| Organize an experiment or find a previous run | [Projects, Jobs and results](Project_to_Result.md) | Which ID to keep, what is saved where, and how to reopen the result |
| Find a screen, including an old bookmark | [Workspace guide](Workspaces.md) | Current routes, redirects and optional features |
| Understand the installation | [Operator troubleshooting](Operations.md) · [Platform overview](Platform_Overview.md) · [Desktop and shells](Desktop_Runtime_and_Shell_Architecture.md) | Hosted UI, managed services, host execution and storage |
| Prepare tools or workflow dependencies | [Tool installer](Tool_Installer.md) · [Selective provisioning](Selective_Workflow_Provisioning.md) | Tool availability versus the dependencies of a selected workflow |
| Design a binder for a target | [Binder design](Structure_Design_and_Refinement.md#binder-design) | BindCraft2 campaigns, BoltzGen generation and selected candidate review |
| Generate or redesign a protein | [De novo protein design and redesign](Structure_Design_and_Refinement.md#de-novo-protein-design-and-redesign) | RFD4-Proteina generation, refinement, search and evaluation |
| Predict a structure or compare conformations | [Structure prediction](Structure_Design_and_Refinement.md#structure-prediction) · [Conformational mapping](Structure_Design_and_Refinement.md#conformational-mapping-and-molecular-dynamics) | Predictions and ensembles, distinct from trajectories |
| Run or review a GROMACS simulation | [Molecular dynamics](Molecular_Dynamics_Suite.md) | Setup, checkpoints and trajectory playback |
| Edit a molecule or design a construct | [Molecular Biology Toolkit](Lab_Automation_MolBio_and_Sequencing.md#molecular-biology-toolkit) | Sequence revisions, primers, PCR, digest and assembly |
| Review an ONT run | [Nanopore and NGS](Lab_Automation_MolBio_and_Sequencing.md#nanopore--ngs-surface) | Basecalling, QC, methylation, clone validation, reads and signal |
| Compare family alignments or inspect statistics | [Workspace guide](Workspaces.md) · [Results and analysis](Results_and_Analysis.md) | Alignment library and comparison, result selection and the statistics workspace |
| Inspect results or Remote execution | [Results and analysis](Results_and_Analysis.md) · [Remote workers and cache](Remote_Worker_Execution_and_Cache.md) | Job details, artifact identity, publication and explicit retrieval |
| Understand the instrument integration | [BioXP control plane](BioXP_Compact_Control_Plane.md) | What BMS presents and what only the robot can attest or authorize |
| Change a service, model or client | [Service map](Services.md) · [Request traces](Service_traces.md) · [API](../platform/api/README.md) · [Frontend](../platform/frontend/README.md) | Entry points, data ownership and callers |
| Maintain or release BMS | [Repository maintenance](Repository_Maintenance.md) · [Development deployment](development-deployment.md) | Branches, managed release and how deployment differs from a commit |

## How to read the other documents

[Model configuration and agent parity](Model_Configuration_Operator_Control_and_Agent_Parity.md) defines a requirement, not a certificate that every model satisfies it. [Integration guidance](ai_guidance/Model_Integrations.md) is for contributors. [Specifications](specs/) and [plans](plans/) describe intended changes; use implementation and subsequent acceptance evidence before treating them as shipped. [Reports](reports/), [qualification records](qualification/) and [OEM investigations](oem/) describe specific checks or observations, not instructions for a different release or instrument. The [BNS deployment guide](BNS_NGS_Runtime_Deployment.md) concerns a separate edition.

A route in source, an installed dependency, a successful offline test and a result observed on a live system are different claims. Use the guide that addresses the claim you need to verify.
