# Service and module map

Use this page to find the code that owns a record or decision. The names below link to **API modules**, not independently running daemons. The [API entry point](../platform/api/main.py) determines mounts and feature gates. The [platform overview](Platform_Overview.md) explains process boundaries.

## Projects and experiment records

Projects organize work. The experiment store owns setups, revisions, launch contexts and retained resource references; the compute store owns the Job. For a stale Project status, trace the binding and projection before inspecting the scheduler.

[Project lifecycle](Project_to_Result.md) · [global experiment services](../platform/api/services/global_experiments/).

API modules: [projects](../platform/api/routers/projects.py), [project_manager](../platform/api/routers/project_manager.py), [experiment_workspaces](../platform/api/routers/experiment_workspaces.py), [molbio_ngs_experiments](../platform/api/routers/molbio_ngs_experiments.py), [ngs_molbio_n5](../platform/api/routers/ngs_molbio_n5.py).

## Launch, queue and placement

Start at the selected model and request, then distinguish admission, queue state, execution target and the attempt that actually ran. A successful preview is not an admitted Job. For remote work, provisioning, transfer, dispatch and result retrieval are separate operations.

[Scheduler](../platform/api/services/gpu_orchestrator.py) · [host adapter](../platform/api/services/workflow_adapter.py) · [remote execution services](../platform/api/services/remote_execution/).

API modules: [models](../platform/api/routers/models.py), [templates](../platform/api/routers/templates.py), [inputs](../platform/api/routers/inputs.py), [jobs](../platform/api/routers/jobs.py), [queue](../platform/api/routers/queue.py), [gpu](../platform/api/routers/gpu.py), [execution_targets](../platform/api/routers/execution_targets.py).

## Results, analysis and custody

Job state, Design rows, analysis records and artifact bytes answer different questions. Follow the producing Job and the selected result identity through publication to the reader. Importing external output is not a new local computation.

[Results guide](Results_and_Analysis.md) · [ingester](../platform/api/services/result_ingester.py) · [scientific artifacts](../platform/api/services/scientific_artifacts/) · [external imports](../platform/api/services/external_imports/).

API modules: [files](../platform/api/routers/files.py), [designs](../platform/api/routers/designs.py), [analyses](../platform/api/routers/analyses.py), [analytics](../platform/api/routers/analytics.py), [viewer_resources](../platform/api/routers/viewer_resources.py), [external_imports](../platform/api/routers/external_imports.py), [payload_ownership_audit](../platform/api/routers/payload_ownership_audit.py).

## Structure workflows and selected continuations

Use the model-specific request and reader when investigating a selected design, continuation or metric. The presence of a model in the registry does not establish runtime readiness.

[Structure workflows](Structure_Design_and_Refinement.md) · [configuration policy](Model_Configuration_Operator_Control_and_Agent_Parity.md) · [Frustra services](../platform/api/services/frustrampnn/).

API modules: [boltz_api_jobs](../platform/api/routers/boltz_api_jobs.py), [boltzgen](../platform/api/routers/boltzgen.py), [binder_blind_pose](../platform/api/routers/binder_blind_pose.py), [binder_continuation](../platform/api/routers/binder_continuation.py), [ligandmpnn_interface_context](../platform/api/routers/ligandmpnn_interface_context.py), [sequence_native](../platform/api/routers/sequence_native.py), [protonpottsmpnn](../platform/api/routers/protonpottsmpnn.py), [protonpottsmpnn_prediction](../platform/api/routers/protonpottsmpnn_prediction.py), [plr_results](../platform/api/routers/plr_results.py), [shape_blueprint](../platform/api/routers/shape_blueprint.py), [frustrampnn](../platform/api/routers/frustrampnn.py), [frameworks](../platform/api/routers/frameworks.py), [rcsb](../platform/api/routers/rcsb.py).

## Dynamics, conformational mapping and molecular workbenches

Separate preparation and validation from execution, then read the result using its own analysis or geometry record. Do not infer a completed dynamics run from a structure displayed in a viewer.

[MD guide](Molecular_Dynamics_Suite.md) · [MD services](../platform/api/services/md/) · [conformational mapping](../platform/api/services/conformational_mapping/) · [Studio runtime](AtomWorks_Managed_Runtime.md).

API modules: [molecular_dynamics](../platform/api/routers/molecular_dynamics.py), [md_results](../platform/api/routers/md_results.py), [conformational_mapping](../platform/api/routers/conformational_mapping.py), [atomworks_studio](../platform/api/routers/atomworks_studio.py), [molecular_construction](../platform/api/routers/molecular_construction.py), [molecular_geometry](../platform/api/routers/molecular_geometry.py), [smiles_converter](../platform/api/routers/smiles_converter.py).

## Sequence records, libraries and Mol Bio

The editor, shared library and Project attachment are not interchangeable. For a reopen issue, retain the sequence and revision identity rather than looking up the latest sequence with the same name.

[Mol Bio workspace](Lab_Automation_MolBio_and_Sequencing.md) · [assembly services](../platform/api/services/assembly/).

API modules: [nucleotide_sequences](../platform/api/routers/nucleotide_sequences.py), [user_sequences](../platform/api/routers/user_sequences.py), [user_templates](../platform/api/routers/user_templates.py), [molbio_ops](../platform/api/routers/molbio_ops.py), [molbio_golden_gate_design](../platform/api/routers/molbio_golden_gate_design.py), [molbio_gibson_suggest](../platform/api/routers/molbio_gibson_suggest.py), [molbio_restriction](../platform/api/routers/molbio_restriction.py), [molbio_relatives](../platform/api/routers/molbio_relatives.py), [molbio_branches](../platform/api/routers/molbio_branches.py), [molbio_end_operations](../platform/api/routers/molbio_end_operations.py), [molbio_share](../platform/api/routers/molbio_share.py), [rna_structure](../platform/api/routers/rna_structure.py), [ribocentre](../platform/api/routers/ribocentre.py).

## Alignments and NGS

Family alignment results and folding MSA preparation have different owners. For sequencing, distinguish device observations, run records, barcode units and the retained signal or alignment opened by the viewer. The ONT run module also supplies the barcode router mounted under Jobs.

[Family alignment services](../platform/api/services/family_msa/) · [alignment core](../packages/bms-alignment-core/README.md) · [NGS review](Lab_Automation_MolBio_and_Sequencing.md).

API modules: [family_msa_results](../platform/api/routers/family_msa_results.py), [msa](../platform/api/routers/msa.py), [sequence_qc](../platform/api/routers/sequence_qc.py), [ngs_alignment_sessions](../platform/api/routers/ngs_alignment_sessions.py), [ont_devices](../platform/api/routers/ont_devices.py), [ont_runs](../platform/api/routers/ont_runs.py), [ont_signal_workbench](../platform/api/routers/ont_signal_workbench.py).

## Instrument integration

The BMS integration is feature-gated. Connection, methods, workflows, camera and operator controls are typed surfaces. BMS connection state does not establish physical readiness; robot-side admission and observations remain authoritative.

[BioXP contract](BioXP_Compact_Control_Plane.md) · [BMS-side services](../platform/api/services/bioxp/).

API modules: [bioxp](../platform/api/routers/bioxp/).

## Installation, telemetry and shell delivery

Use the managed launcher and install profile for the selected runtime. A working dashboard shows that the control plane is available, not that each workflow dependency is installed. Mobile updates and desktop launchers deliver or open the hosted UI; they do not own scientific results.

[Installation](Nonproduction_Installation.md) · [service manager](../biomodstack_services.py) · [tools](Tool_Installer.md) · [shell architecture](Desktop_Runtime_and_Shell_Architecture.md).

API modules: [system](../platform/api/routers/system.py), [telemetry](../platform/api/routers/telemetry.py), [tools_installer](../platform/api/routers/tools_installer.py), [dev_issues](../platform/api/routers/dev_issues.py), [mobile_apk_updates](../platform/api/routers/mobile_apk_updates.py), [mobile_ui_updates](../platform/api/routers/mobile_ui_updates.py).

For worked examples that cross these boundaries, see [Trace a request across services](Service_traces.md).

## Shared libraries and execution code

The [browser client](../platform/frontend/README.md) consumes API records. The [sequence utilities](../packages/sequence-utils/README.md), [range utilities](../packages/range-utils/README.md) and [format parsers](../packages/bio-parsers/README.md) are libraries, not API services. The [alignment core](../packages/bms-alignment-core/README.md) documents its own implementation limits.

For a dispatch problem, trace the [workflow registry](../platform/api/services/workflow_adapter_registry.py) and [Nextflow service](../platform/api/services/nextflow.py) to the selected [workflow](../workflows/) and [process module](../modules/). For missing dependencies, use [selective provisioning](Selective_Workflow_Provisioning.md); for a result that cannot be reopened, start at the producing record and its reader rather than changing the workflow.
