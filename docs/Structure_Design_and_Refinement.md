# Structure prediction, design and simulation

Choose the workflow by the result you need. **Binder design** and **de novo protein design/redesign** are separate starting points, even when both can use the same prediction or analysis tool. If the work belongs to a Project, open its saved setup first so you can return to the Job and its results from the same experiment. See [Projects, Jobs and results](Project_to_Result.md).

## Binder design

Use this family when the question is about a binder and its target. **BindCraft2 (BC2)** is a native campaign workflow, marked experimental in its [BMS configuration](../platform/api/config/models/bindcraft2.yaml). A campaign, its ranking, and a selected candidate are different records. Reopen the campaign to review its candidates; carry the selected candidate and source Job into any subsequent refinement. Ranking an existing campaign is not a new generation run.

**BoltzGen** is a separate [binder generation workflow](../workflows/boltzgen_generation.nf), not a BC2 stage. The [De Novo Binder launcher](../platform/frontend/src/lib/launcherCatalog.ts) also offers workflow-specific PPIFlow and RFantibody paths. Use the modes exposed by the selected engine. Selection, refinement and GROMACS simulation are separate actions, not automatic stages that every binder completes. For an existing result, use the [results guide](Results_and_Analysis.md) to distinguish the campaign from a particular structure or round.

## De novo protein design and redesign

Use this family to generate a new protein or work from an existing structure. **RFD4-Proteina** is the suite to use for new work. Its [task workspace](../platform/frontend/src/components/Rfd4TaskWorkspace.tsx) and [native workflow](../workflows/rfd4_proteina.nf) distinguish generation, refinement and search from evaluation. In the [model configuration](../platform/api/config/models/rfd4_proteina.yaml), `generate` includes generation or refinement; `evaluate` evaluates native structures rather than creating another design. When reopening a result, retain the native output identity as well as the Job ID, especially if the run produced multiple samples.

Other implemented choices have their own roles: the experimental De Novo workbench includes DISCO and La-Proteina generation, while the [La-Proteina evaluator](../workflows/laproteina_evaluation.nf) operates on retained samples. [Complexa](../workflows/complexa.nf) has its own generation and evaluation contract. These are not aliases for RFD4-Proteina. A sequence method offered for a selected structure is not another top-level design workflow.

**Older results:** RFD3 is being phased out. Its name and the disabled `protein_local_redesign` ID can still occur in saved Jobs; neither is the recommended entry for new work. RF3 is not a current structure-prediction choice.

## Structure prediction

Use Structure Prediction when you already have the molecular inputs and want a predicted structure. Choose [Protenix](../platform/api/config/models/protenix.yaml), [Boltz-2](../platform/api/config/models/boltz2.yaml), [NVIDIA Fold-CP](../platform/api/config/models/boltz_cp_experimental.yaml) or [ESMFold2](../platform/api/config/models/esmfold2.yaml) according to the inputs and compute available. Fold-CP is a backend within this workflow. The older `esmfold2_experimental` ID reopens as ESMFold2, not as a second predictor.

Review the selected prediction and its own confidence artifacts in the [structure prediction results workspace](../platform/frontend/src/components/StructurePredictionWorkspace.tsx). A folding MSA is an input to a prediction, not a family-alignment result. A structure prediction used to check a design is not itself a binder generation run or experimental confirmation of binding.

## Conformational mapping and molecular dynamics

- **Conformational mapping** compares structural hypotheses. Its [typed workflow](../workflows/conformational_mapping.nf) distinguishes Protenix ensembles, ConforNets hypotheses and registered structure imports. Review a result in the context of its source. An imported structure is not a new prediction; an ensemble is not a time-resolved trajectory.
- **GROMACS molecular dynamics** produces trajectories, energies and checkpoints. Use the [MD guide](Molecular_Dynamics_Suite.md) for guided versus prepared inputs, feature availability, playback and restart. A saved structure or a short relaxation is not a completed MD simulation.
- **FrustraMPNN** is structure analysis, not sequence redesign. Where a parent workflow requests it, inspect its analysis for the selected structure rather than looking for a separate design launcher.

## Availability and further reading

An experimental workflow here has a BMS entry point and implementation. The label does not mean the required weights, runtime, worker or result artifacts are present on this installation. For missing dependencies see [Operations](Operations.md); for result identity and remote retrieval see [Results and analysis](Results_and_Analysis.md). Internal child Jobs record stages of a parent; they are not separate products to launch. The [model-control policy](Model_Configuration_Operator_Control_and_Agent_Parity.md) describes integration requirements, not a list of independently supported products.
