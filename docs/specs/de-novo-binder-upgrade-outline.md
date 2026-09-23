# De Novo Binder Design: Upgrade Outline

**Status:** Updated to Christian's three concurrent work tranches. This document fixes product scope; it does not claim implementation or authorize scientific campaigns, deployment or worker rental.

**Specification:** [De Novo Binder Design Upgrade](de-novo-binder-design-upgrade.md). **Dispatch plan:** [Concurrent implementation SOW](de-novo-binder-concurrent-implementation-plan.md), including complete subagent prompts, ownership and pass/fail gates.

**Reviewed source:** BMS `96923d9470d86219451d6caf88f63308a369c007`; BindCraft2 `d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f` (package version `1.0.1`). Implementation must start from current `origin/test` and reconcile relevant changes against this evidence baseline.

## The product direction is fixed

Upgrade the entire current de novo nanobody workflow into **agnostic De Novo Binder Design**. Keep all existing generators available through their supported capabilities and add **fully integrated BC2 as another first-class option**. Upgrade and generalize the existing refinement loop so candidates from any of those generators can enter it through a common, model-aware selection boundary. Running that loop is optional for the operator; delivering the upgraded loop is mandatory.

The user chooses binder format and objective, chooses a compatible generator, runs generation, reviews its native results, and either stops or selects candidates for refinement. After each completed round the user can select a compatible subset and requeue chosen refinement and/or assessment operations, preserving all parent/descendant evidence. This structure is Christian's direction, not an open architectural question.

Local execution, **real remote-bridge execution of the whole selected workflow**, fast warm startup, solid per-model results and repair of existing workflow defects are all part of the same upgrade. LigandMPNN target amino-acid/structural-context compatibility and blind pose verification are separate, independently selectable post-round assessments. An ordinary sequence-redesign option does not satisfy the LigandMPNN role.

The three concurrent tranches are: **Phase 1**, generalize and repair the entire existing workflow/refinement/core/remote bridge; **Phase 2**, integrate full native BC2 as a first-class peer option; **Phase 3**, implement the experimental LigandMPNN validator. Phase numbers are scope groupings, not an instruction to wait for each preceding phase to finish.

There is no reduced “BC2 core” completion claim that excludes the launcher, existing generators, refinement loop, remote execution or agreed analyses. Engineering milestones may be sequenced and integrated separately, but they do not reduce the completion scope. Only Christian can remove or defer an agreed deliverable.

The conversational label “version 2” is not a product name, code namespace, schema suffix or reason to rename historical jobs.

## Problems being addressed

1. **The product is hard-coded around nanobodies.** Heavy/light-chain roles, CDRs, framework assumptions, VHH protocols and handwritten requests leak into otherwise general tasks. The launcher and refinement entrypoints must become modality-aware, retaining antibody-specific controls only where meaningful.
2. **BC2 is missing.** Its complete native campaign surface must be integrated, including unscaffolded/scaffolded formats, conformational objectives, multi-target/detarget inputs, presets, advanced scientific settings, sweeps, adaptive attempts, native outputs and resume behavior. A VHH-only wrapper or raw-JSON-only editor is not sufficient.
3. **Existing execution contains real defects.** Hidden profile overrides, unknown-validator fallback, synthetic sequence fallback, implicit redesign, chain-role substitution, sample loss and stale validation labels can change or misrepresent the requested work. These defects are in scope for correction, not grandfathered into the new workflow.
4. **Candidate identity and continuation are fragile.** Filename/stem inference, incomplete source ownership, inconsistent selection materialization and generic result flattening cannot reliably retain states, samples and multiple refinement rounds. Optional analysis must not roll back usable generator results.
   The current iteration API also admits only antibody-looking roots and launches an antibody-specific refinement job; generic continuation needs a server-side route, not just an unlocked button.
5. **Refinement mechanisms are entangled.** Side-chain repack, anchor analysis, partial-flow backbone refinement, sequence redesign, prediction and diagnostic analysis need independent selection and truthful output identity. Generalizing the loop must not falsely generalize an antibody-specific checkpoint.
6. **Remote operation is not just an image deployment.** The same scientific request must execute through existing local and bridge owners, using only selected dependencies, shared assets, persistent compatible caches, precise resume semantics and reopenable returned results. Warm-start overhead must be measured and unnecessary repeated work removed.
7. **Documentation confuses implementation with qualification.** Generic constrained FA-MPNN, a Caliby parent-workflow path and substantial FrustraMPNN/result infrastructure already exist. They should be reused and qualified rather than reimplemented. Project catalogue unavailability is not proof that a core-job runner is absent.

## Required changes

### Agnostic launcher with every existing generator plus BC2

Preserve RFantibody, BoltzGen and seeded PPIFlow as distinct choices and routes. Reuse existing general de novo/RFD3 capabilities where applicable rather than displacing or duplicating them. BC2 is a peer option, not an obligatory stage, replacement generator or add-on restricted to VHH.

Select modality/objective first; expose the selected model's relevant controls and real compatibility constraints. Selecting a new modality must not silently coerce an old request. A seeded route remains visibly seeded. Existing jobs, templates and results remain interpretable. The disabled historical `binder_design.yaml` template is not this product and must not be enabled as a shortcut.

### Complete BC2 integration

Use one model-owned typed schema with browser/agent parity and pinned native preset resolution. Preserve native campaign stages, workers, sweeps, adaptive behavior, acceptance and ranking. A dedicated Nextflow entrypoint invokes the native campaign; it must not duplicate the scientific engine.

Register the complete selected runtime dependency closure for local and remote execution. Preserve shared weights, GPU isolation and persistent compilation cache. BC2 itself does not require external MSA search.

### Upgraded optional refinement loop

Candidates from every generator enter the same selection/continuation experience, subject to actual artifact and model compatibility rather than generator-name whitelists.

Deliver general constrained sequence redesign, independently selectable repack and anchor analysis, supported PPIFlow partial flow, independently selected prediction/validation, the **full existing global FrustraMPNN implementation** on selected binder structures, Caliby qualification and integration, separate pairwise cross-chain contact-frustration work, and optional experimental LigandMPNN target-context compatibility. Keep each operation's settings and results model-owned. The target-designed binder's sequence/structure may encode useful target-specific design history, so its local FrustraMPNN landscape is worth inspecting; native inference still parses each chain separately and does not directly see a partner or score interchain contacts. Intrachain domains remain in the binder input; artificially linking binder and target into one chain is future research, not a workaround in this release. FrustraMPNN's preprint/GitHub implement *single-residue* predictions and describe pairwise contact prediction as future work; pairwise FrustratometeR mutational mode has published interface precedent, whereas a configurational-first claim is not established by that preprint. The cross-chain method/reference still requires an explicit scientific choice, not silent removal from scope.

Each optional check presents raw evidence *before* user-defined cutoffs; selecting one does not force the other. Operators inspect results, set and save/version cutoffs, and can explicitly reclassify retained raw results. For an exact candidate/context, valid failure of both *selected, classified* blind-recovery and LigandMPNN-context checks means computational rejection only under the selected dual-failure policy. Mixed evidence remains mixed; unclassified, skipped, errored or inconclusive checks are not scientific failures. Preserve raw evidence and the unchanged candidate. LigandMPNN's supplied-geometry compatibility evidence is not independent pose recovery or proof of binding.

Preserve parents and all round/sample/state identities. A changed sequence or structure creates a descendant with fresh validation state. The user can repeat the loop, compare parent and descendants, and stop without being forced through another model. Qualify the selected LigandMPNN checkpoint for the claimed protein-interface task before allowing a binary diagnostic verdict; ordinary chemistry-context design remains a separate Foundry obligation, not proof of this check.

### Per-model results and reliable selection

Reuse existing Job/Design, scientific artifacts, native result adapters and review infrastructure. Add BC2-native attempt/draw/retained-sequence/state records and make campaign/per-attempt settings visible from job details; do not invent a universal binder score or parallel candidate database. BC2 handoff acceptance needs a real BC2 candidate, not an unrelated generator's refinement result.

Keep native CIF/mmCIF authoritative. `Design.pdb_path` already stores those formats despite its legacy name. Fix actual PDB-only consumers; convert only at an operation that truly requires it. No fabricated structures for failed or structureless attempts.

### Local/remote parity and removal of unnecessary work

Use the same compiled scientific invocation and model result adapter for either placement. Provision selected immutable assets once and reuse them. Off-stages neither execute nor acquire dependencies. Measure provisioning, warm launch, native compilation, resume and result return separately. Retain ownership/corruption checks at real boundaries without adding duplicate validation systems.

## Implementation and completion

Eight bounded implementation lanes cover launcher/requests, refinement/blind checks, selection/results, existing alternatives/analyses, BC2 native execution, BC2 publication, bridge/runtime/performance, and LigandMPNN validation. They work concurrently with sole-writer file ownership and one parent integrator. The linked SOW supplies copy-ready prompts and explicit pass/fail conditions. Reuse the existing review evidence; no ceremonial second review is required merely to inspect code. Freeze concrete shared interfaces before dependent production edits; present the pairwise complex-contact method/reference choice early. Check independence, input leakage and model applicability during implementation; user-defined diagnostic cutoffs follow inspected results, not a pre-run approval gate. The experimental LigandMPNN diagnostic still needs an executable route under Foundry; its existing chemistry-mode registry row is not that route.

Acceptance covers the **whole workflow**: all existing generators, full native BC2, modality-aware controls, repeated optional refinement, repaired defects, per-model results, local execution, remote execution and measured warm behavior. An unavailable in-scope operation is unfinished work, not a completed feature because a disabled selector explains it.

Simplify the machinery, not the product. Reuse global configuration/result facilities and preserve their applicable guarantees without turning this upgrade into an unrelated platform rewrite. Our installed/downloaded models must run through the existing remote bridge for our jobs; this is not third-party hosted-service access merely because the GPU worker is rented. Review licensing/audience controls separately *if* offering BMS to third parties, not as a blocker to internal remote execution.
