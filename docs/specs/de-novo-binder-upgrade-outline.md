# De Novo Binder Design: Upgrade Outline

**Status:** Revised after Astra's holistic review and Christian's scope clarification. This document fixes the product scope; it does not authorize implementation, scientific campaigns, deployment or worker rental.

**Specification:** [De Novo Binder Design Upgrade](de-novo-binder-design-upgrade.md).

**Reviewed source:** BMS `96923d9470d86219451d6caf88f63308a369c007`; BindCraft2 `d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f` (package version `1.0.1`). Implementation must start from current `origin/test` and reconcile relevant changes against this evidence baseline.

## The product direction is fixed

Upgrade the entire current de novo nanobody workflow into **agnostic De Novo Binder Design**. Keep all existing generators available through their supported capabilities and add **fully integrated BC2 as another first-class option**. Upgrade and generalize the existing refinement loop so candidates from any of those generators can enter it through a common, model-aware selection boundary. Running that loop is optional for the operator; delivering the upgraded loop is mandatory.

The user chooses binder format and objective, chooses a compatible generator, runs generation, reviews its native results, and either stops or selects candidates for refinement. Refined candidates can be assessed, compared, selected and refined again. This structure is Christian's direction, not an open architectural question.

Local execution, remote-bridge execution, fast warm startup, solid per-model results and repair of existing workflow defects are all part of the same upgrade. LigandMPNN is an in-scope optional refinement method; its detailed scientific use remains the next discussion item. It is not an uncommitted later expansion.

There is no reduced “BC2 core” completion claim that excludes the launcher, existing generators, refinement loop, remote execution or agreed analyses. Engineering milestones may be sequenced and integrated separately, but they do not reduce the completion scope. Only Christian can remove or defer an agreed deliverable.

The conversational label “version 2” is not a product name, code namespace, schema suffix or reason to rename historical jobs.

## Problems being addressed

1. **The product is hard-coded around nanobodies.** Heavy/light-chain roles, CDRs, framework assumptions, VHH protocols and handwritten requests leak into otherwise general tasks. The launcher and refinement entrypoints must become modality-aware, retaining antibody-specific controls only where meaningful.
2. **BC2 is missing.** Its complete native campaign surface must be integrated, including unscaffolded/scaffolded formats, conformational objectives, multi-target/detarget inputs, presets, advanced scientific settings, sweeps, adaptive attempts, native outputs and resume behavior. A VHH-only wrapper or raw-JSON-only editor is not sufficient.
3. **Existing execution contains real defects.** Hidden profile overrides, unknown-validator fallback, synthetic sequence fallback, implicit redesign, chain-role substitution, sample loss and stale validation labels can change or misrepresent the requested work. These defects are in scope for correction, not grandfathered into the new workflow.
4. **Candidate identity and continuation are fragile.** Filename/stem inference, incomplete source ownership, inconsistent selection materialization and generic result flattening cannot reliably retain states, samples and multiple refinement rounds. Optional analysis must not roll back usable generator results.
5. **Refinement mechanisms are entangled.** Side-chain repack, anchor analysis, partial-flow backbone refinement, sequence redesign, prediction and diagnostic analysis need independent selection and truthful output identity. Generalizing the loop must not falsely generalize an antibody-specific checkpoint.
6. **Remote operation is not just an image deployment.** The same scientific request must execute through existing local and bridge owners, using only selected dependencies, shared assets, persistent compatible caches, precise resume semantics and reopenable returned results. Warm-start overhead must be measured and unnecessary repeated work removed.
7. **Documentation confuses implementation with qualification.** Generic constrained FA-MPNN, a Caliby parent-workflow path and substantial FrustraMPNN/result infrastructure already exist. They should be reused and qualified rather than reimplemented. Project catalogue unavailability is not proof that a core-job runner is absent.

## Required changes

### Agnostic launcher with every existing generator plus BC2

Preserve RFantibody, BoltzGen and seeded PPIFlow as distinct choices and routes. Reuse existing general de novo/RFD3 capabilities where applicable rather than displacing or duplicating them. BC2 is a peer option, not an obligatory stage, replacement generator or add-on restricted to VHH.

Select modality/objective first; expose the selected model's relevant controls and real compatibility constraints. Selecting a new modality must not silently coerce an old request. A seeded route remains visibly seeded. Existing jobs, templates and results remain interpretable.

### Complete BC2 integration

Use one model-owned typed schema with browser/agent parity and pinned native preset resolution. Preserve native campaign stages, workers, sweeps, adaptive behavior, acceptance and ranking. A dedicated Nextflow entrypoint invokes the native campaign; it must not duplicate the scientific engine.

Register the complete selected runtime dependency closure for local and remote execution. Preserve shared weights, GPU isolation and persistent compilation cache. BC2 itself does not require external MSA search.

### Upgraded optional refinement loop

Candidates from every generator enter the same selection/continuation experience, subject to actual artifact and model compatibility rather than generator-name whitelists.

Deliver general constrained sequence redesign, independently selectable repack and anchor analysis, supported PPIFlow partial flow, independently selected prediction/validation, binder-local FrustraMPNN, Caliby qualification and integration, the agreed complex-contact-frustration work, and LigandMPNN as an optional refinement method. Keep each operation's settings and results model-owned. LigandMPNN's exact scientific options and the cross-chain-analysis method require explicit scientific decisions, not silent removal from scope.

Preserve parents and all round/sample/state identities. A changed sequence or structure creates a descendant with fresh validation state. The user can repeat the loop, compare parent and descendants, and stop without being forced through another model.

### Per-model results and reliable selection

Reuse existing Job/Design, scientific artifacts, native result adapters and review infrastructure. Add BC2-native attempt/draw/retained-sequence/state records; do not invent a universal binder score or parallel candidate database.

Keep native CIF/mmCIF authoritative. `Design.pdb_path` already stores those formats despite its legacy name. Fix actual PDB-only consumers; convert only at an operation that truly requires it. No fabricated structures for failed or structureless attempts.

### Local/remote parity and removal of unnecessary work

Use the same compiled scientific invocation and model result adapter for either placement. Provision selected immutable assets once and reuse them. Off-stages neither execute nor acquire dependencies. Measure provisioning, warm launch, native compilation, resume and result return separately. Retain ownership/corruption checks at real boundaries without adding duplicate validation systems.

## Implementation and completion

Subagents implement bounded workstreams with explicit file ownership and one parent integrator. The existing holistic review supplies evidence; no ceremonial second review is required merely to inspect code. Concrete unresolved contracts must be resolved before dependent implementation, and consequential scientific choices remain Christian's decisions.

Acceptance covers the **whole workflow**: all existing generators, full native BC2, modality-aware controls, repeated optional refinement, repaired defects, per-model results, local execution, remote execution and measured warm behavior. An unavailable in-scope operation is unfinished work, not a completed feature because a disabled selector explains it.

Simplify the machinery, not the product. Reuse global configuration/result facilities and preserve their applicable guarantees without turning this upgrade into an unrelated platform rewrite. Licensing restrictions on third-party invocable BC2 functionality remain distinct from authorized internal local/private-remote use.
