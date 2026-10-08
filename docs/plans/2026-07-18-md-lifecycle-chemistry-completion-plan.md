# Molecular Dynamics Lifecycle and Chemistry Completion Plan

> **For Hermes:** Execute only an approved phase using the `subagent-driven-development` and `test-driven-development` skills. Do not commit, migrate, deploy, or restart services without explicit operator authorization.

**Goal:** Complete the first-class BioModStack Molecular Dynamics product by adding durable lifecycle control and useful MD-specific operational surfaces, while replacing unsafe free-form force-field/water selection with a versioned, installed, validated chemistry-profile catalog.

**Architecture:** BMS remains the authoritative control plane. A durable MD parent owns scheduler-visible preparation, replica-attempt, and finalization children. Chemistry is resolved before scheduling from a versioned profile that binds force field, water, ions, topology tooling, nonbonded policy, and allowed overrides. Installed assets, runtime-probed availability, validated scientific lanes, and user-selectable lanes are separate states.

**Tech stack:** FastAPI, SQLAlchemy, Pydantic/JSON Schema, React/TypeScript, TanStack Query, Nextflow 25.10.1, GROMACS 2025.3, OpenMM 8.5.2/CUDA, AmberTools/ParmEd for AMBER-family preparation, versioned force-field bundles, Apptainer, pytest, Node test runner.

**Relationship to master specification:** This is the implementation-ready completion tranche for `docs/plans/2026-07-18-first-class-molecular-dynamics-product-spec.md`. It does not replace the master architecture. Where the master spec and this tranche differ, the stricter fail-closed requirement applies until the documents are reconciled and independently reviewed.

---

## 1. Current truth and immediate correction

### 1.1 Proven product slice

The current working implementation has demonstrated:

- a dedicated MD launcher that emits a structured `bms.md.job.v1` request;
- backend normalization and materialization before scheduling;
- a bounded parent coordinator and independently scheduled one-GPU replica child;
- a real 1AKI GROMACS run producing trajectory/checkpoint/manifests;
- an exact OpenMM 8.5.2 CUDA image and a production-only prepared-system run;
- partial-failure aggregation before terminal parent failure.

This proves the launch/execution spine. It does **not** prove complete lifecycle semantics, scientific chemistry coverage, or all-GPU acceptance.

### 1.2 Current force-field inventory is not sufficient

The canonical GROMACS 2025.3 SIF currently exposes these stock `pdb2gmx` directories:

- `amber03.ff`
- `amber94.ff`
- `amber96.ff`
- `amber99.ff`
- `amber99sb.ff`
- `amber99sb-ildn.ff`
- `amberGS.ff`
- `charmm27.ff`
- `gromos43a1.ff`, `gromos43a2.ff`, `gromos45a3.ff`, `gromos53a5.ff`, `gromos53a6.ff`, `gromos54a7.ff`
- `oplsaa.ff`

The current launcher also advertises `charmm36-jul2022`, but that directory is not present in the inspected GROMACS image. That is a release blocker: UI options must be derived from the deployed capability catalog, not hard-coded aspirations.

`amber99sb-ildn` + TIP3P remains acceptable only for the 1AKI infrastructure smoke lane. It is not the default scientific profile for new protein, protein–DNA, ligand, membrane, or DRT4 work.

### 1.3 Key chemistry decision

BMS will **not** expose force field, water, ion model, ligand model, and low-level nonbonded settings as arbitrary independent dropdowns.

It will expose a curated **chemistry profile**. A profile binds an internally compatible set of:

- biopolymer force-field family and exact parameter release/hash;
- water model and matched ion parameter set;
- residue/terminus/protonation templates;
- ligand/cofactor parameterization method where supported;
- lipid/carbohydrate/nucleic-acid extensions where supported;
- nonbonded combination rules, cutoffs, switching/dispersion policy, PME settings, and constraints;
- preparation toolchain/container identity;
- engine compatibility;
- allowed user overrides;
- validated use cases and explicit exclusions.

Users may select among several installed profiles. They may not construct scientifically incoherent combinations such as ff19SB + arbitrary water/ions or CHARMM36m protein + GAFF2 ligand without a validated bridge.

---

## 2. Availability and assurance model

Every chemistry profile has four independent states:

| State | Meaning | May appear in normal launcher? |
|---|---|---|
| `installed` | Parameter assets are present and checksummed in the preparation runtime. | No |
| `runtime_validated` | The assets parse, prepare a fixture, and produce a closed topology accepted by the target engine. | Experimental inventory only |
| `scientifically_validated` | A named validation lane and frozen acceptance plan passed for an explicit system class, composition, protocol, ionic condition, and observable scope. | Yes, only for that scope |
| `selectable` | Operator has enabled the profile and deployed capability probe confirms exact assets. | Yes |

Additional assurance values:

- `smoke_fixture` — infrastructure only;
- `external_unreviewed` — uploaded/prepared topology; execution may complete, but chemistry is not endorsed;
- `curated_profile` — known profile and supported component inventory;
- `approved_pack` — system-specific reviewed parameter pack, such as a DRT4 state.

A profile can be installed without being selectable. A user-visible profile must report its exact assurance, validation lane/version and scope, engine compatibility, and unsupported chemistry. Passing one fixture never validates a profile globally for all proteins, nucleic acids, membranes, mutations, or compositions.

---

## 3. Chemistry profile catalog

### 3.1 Phase-A selectable profiles

| Profile ID | Use case | Bound chemistry | Initial status |
|---|---|---|---|
| `gmx_amber99sb_ildn_tip3p_smoke_v1` | 1AKI and infrastructure fixtures only | AMBER99SB-ILDN, TIP3P, matched monovalent ions | Selectable only in `smoke_auto`; visibly labeled legacy/smoke |
| `amber_ff19sb_opc_protein_v1` | Soluble folded proteins and mutation studies | ff19SB, OPC, matched OPC ions | First modern protein candidate; select only after fixture and ensemble gates |
| `amber_ff19sb_ol15_opc_protein_dna_v1` | Protein–DNA candidate | ff19SB protein + OL15 DNA + OPC + an exact pinned OPC-compatible monovalent-ion release/species | Candidate; not presumed universally primary |
| `amber_ff19sb_bsc1_opc_protein_dna_v1` | Protein–DNA sensitivity/control lane | ff19SB protein + parmbsc1 DNA + OPC + an exact pinned OPC-compatible monovalent-ion release/species | Independent candidate; not silently substituted for OL15 |
| `amber_ff19sb_ol21_opc_protein_dna_v1` | Protein–DNA candidate | ff19SB protein + OL21 DNA + OPC + an exact pinned OPC-compatible monovalent-ion release/species | Install/evaluate alongside OL15 and parmbsc1; promotion depends on named-system evidence |
| `charmm36m_tip3p_protein_v1` | Soluble protein alternative/control | CHARMM36m protein + CHARMM-modified TIP3P + exact CHARMM ion/NBFIX policy | Installed and validated independently before selection |

Scientific basis for the primary AMBER protein baseline: the ff19SB publication recommends OPC for ff19SB protein simulations. Protein–DNA selection remains a validation question rather than a universal winner; OL15, parmbsc1, OL21, and CHARMM-family candidates must be evaluated on named systems with frozen observables. The catalog records the exact water-specific ion parameter release and ion species, not the ambiguous phrase “matched ions.”

### 3.2 Phase-B installed candidates

These should be installed in the versioned catalog but hidden from normal selection until their specific lanes pass:

| Profile ID | Intended use | Gate |
|---|---|---|
| `amber_ff19sb_ol3_opc_rna_v1` | RNA-only candidate | Split validation by duplex/structured RNA class; exact termini/ions; divalent ions rejected without a reviewed pack |
| `amber_ff19sb_ol3_opc_protein_rna_v1` | Protein–RNA candidate | Separate protein–RNA fixture and interaction validation; no inheritance from RNA-only validation |
| `charmm36m_c36dna_charmmtip3p_protein_dna_v1` | Protein–DNA alternate model | Exact C36m protein + C36 DNA release + CHARMM TIP3P + ion/NBFIX/CUFIX policy; DNA stability and interaction validation |
| `charmm36m_c36lipid_charmmtip3p_membrane_protein_v1` | Membrane proteins/GPCRs | Exact C36m protein + exact C36 lipid release + CHARMM TIP3P + ion/NBFIX policy; composition-specific bilayer validation |
| `amber_ff19sb_lipid21_opc_membrane_v1` | AMBER-family membrane proteins | Lipid21 composition/tooling validation and engine parity |
| `amber_ff19sb_glycam06_opc_glycoprotein_v1` | Glycoproteins/carbohydrates | Exact GLYCAM06 revision, anomer/linkage coverage, protein–glycan linker patches, water-specific evidence, and system-specific validation |
| `opls_aam_protein_water_v1` | Protein sensitivity studies | Exact OPLS-AA/M assets and dedicated validation; stock `oplsaa.ff` is not accepted as OPLS-AA/M |

### 3.3 Ligand and cofactor parameter packs

Ligand parameterization is a subprofile bound to the parent force-field family:

- **AMBER family:** GAFF2 with explicit charge method (`AM1-BCC` default candidate; RESP only when curated inputs/provenance exist). Persist tautomer, protonation, stereochemistry, charge, tool versions, penalty/warning output, and parameter hashes.
- **CHARMM family:** CGenFF with exact release and penalty report. High-penalty terms trigger `review_required`; they are never auto-approved.
- **OpenFF/Sage:** install as a future candidate for OpenMM-native small-molecule work, but do not combine with protein families or convert to GROMACS until an explicit compatibility and conversion lane passes.
- **Known cofactors/nucleotides:** use named reviewed packs, not generic ligand generation when validated biopolymer/cofactor parameters exist.

No ligand tool may silently drop atoms, change formal charge, choose a tautomer, or proceed with unmatched parameters.

### 3.4 Metals and catalytic centers

Monovalent bulk ions use the exact ion set matched to the selected water model.

Divalent structural/catalytic metals are separate curated packs. Each pack binds exact ion and oxidation state, water model, surrounding protein/nucleic-acid/ligand atom types, donor-specific interactions, engine implementation, and validated conversion path. For Mg²⁺, Mn²⁺, Zn²⁺, Ca²⁺, or mixed sites, the user must select or receive a reviewed model:

- validated nonbonded 12-6 model;
- validated 12-6-4 model whose pair-specific C4 terms pass force/energy parity after any conversion and on the target engine;
- bonded metal-center model (for example, a reviewed MCPB.py-derived pack), explicitly excluding coordination-exchange/residence claims;
- explicit geometry-restraint structural hypothesis, explicitly excluding coordination-occupancy/residence claims;
- externally supplied reviewed topology.

A generic bulk-ion parameter is not accepted as a catalytic-metal model. DRT4 must preregister joint combinations of **dCTP model × dCTP protonation/net charge × Mn model × water/ion model** for its two Mn²⁺ ions per active site; varying Mn alone is not an adequate sensitivity design.

### 3.5 DRT4 profiles

DRT4 will use system-specific `approved_pack` records rather than a generic dropdown:

- `drt4_9vdp_wt_product_dna_tyr125_linkage_<version>` — product-state pack; the Tyr125–5′-phosphate linkage, post-reaction protonation/charge, and custom bonded terms are mandatory or the pack remains blocked
- `drt4_9vdo_wt_dna_dctp_2mn_<version>`
- `drt4_9vdv_d240a_d241a_<version>`
- separately versioned matched in-silico mutant packs
- any deliberately noncovalent 9VDP interpretation is a separate preregistered sensitivity hypothesis and cannot be labeled the deposited product state

Each pack freezes the exact biological assembly, construct numbering, DNA sequence/termini, dCTP state, Mn model, protonation, missing-atom modeling, waters/ions, mutations, covalent links, source checksums, topology closure, and reviewer decision. Classical MD never infers or forms the Tyr–O–phosphate bond.

---

## 4. Parameter exposure policy

### 4.1 User-selectable in the standard launcher

- chemistry profile;
- input structure or reviewed prepared-system bundle;
- biological assembly/model and chain/component inclusion;
- pH target and protonation mode (`automatic_review_required`, `explicit`, or approved pack);
- explicit histidine/protonation overrides when the selected profile supports them;
- disulfide and terminus review/overrides;
- box geometry and padding within profile bounds;
- salt concentration and neutralization within profile bounds;
- temperature and pressure;
- replica count and seed policy;
- minimization/NVT/NPT/production duration within lane limits;
- output trajectory, energy, log, and checkpoint cadence;
- engine when the profile supports more than one validated engine.

### 4.2 Profile-locked by default

- water and ion parameter files;
- combination rules and 1–4 scaling;
- PME method/error tolerance/grid policy;
- Lennard-Jones cutoffs, switching, long-range correction;
- bond constraints and LINCS/SHAKE settings;
- default timestep;
- thermostat/barostat algorithm and coupling constants;
- pressure compressibility;
- coupling groups and center-of-mass removal;
- hydrogen-mass repartitioning policy;
- virtual-site policy;
- residue template naming/mapping;
- engine-specific implementation details.

These settings are part of the chemistry/protocol profile because changing them can alter the physical model. They must not be presented as harmless generic knobs.

Membrane profiles additionally lock semi-isotropic pressure coupling, xy/z compressibility, leaflet composition, water thickness, builder/orientation provenance, lateral-area/bilayer QC, and composition-specific ion policy. One lipid fixture cannot validate all membrane compositions.

### 4.3 Advanced bounded overrides

A profile may explicitly permit lane-specific bounded overrides. The following are illustrative only and must never become global limits:

- temperature/pressure ranges validated for that lane;
- padding and finite-size criteria validated for that system charge/shape;
- salt range and species validated for that profile/system class;
- output cadence limits;
- restraints on named reviewed selections;
- timestep 2 fs by default; 4 fs only in a separately validated HMR profile;
- membrane-only directional box controls and semi-isotropic pressure policy.

Every override is schema-validated, appears in the resolved manifest, changes the protocol hash, and invalidates a curated lane if outside that lane's registered range.

### 4.4 Expert prepared-bundle lane

Experts may submit a recursively closed prepared topology/coordinate bundle. BMS validates structure/topology consistency and engine compatibility but labels it `external_unreviewed` unless it references an approved pack hash. A successful run does not turn that bundle into a selectable chemistry profile.

---

## 5. Versioned chemistry contracts

### 5.1 New job contract

Introduce `bms.md.job.v2` rather than overloading the current free-form `preparation.force_field` and `water_model` strings.

Required fields:

```json
{
  "schema": "bms.md.job.v2",
  "chemistry": {
    "profile_id": "amber_ff19sb_ol15_opc_protein_dna_v1",
    "profile_sha256": "<resolved by server>",
    "assurance_requested": "curated_profile",
    "protonation": {"ph": 7.0, "mode": "automatic_review_required", "overrides": []},
    "components": [],
    "ligand_packs": [],
    "metal_packs": [],
    "allowed_overrides": {}
  },
  "protocol": {
    "profile_id": "explicit_solvent_2fs_npt_v1",
    "temperature_k": 300.0,
    "pressure_bar": 1.0,
    "stage_durations": {},
    "output_policy": {}
  }
}
```

The server resolves and persists:

- exact profile asset hashes;
- profile/protocol version;
- preparation and engine image identity;
- water/ion and all included parameter files;
- effective topology/preprocessed topology hash;
- allowed/used overrides;
- validation lane and assurance;
- component inventory and unmatched-template report.

`bms.md.job.v1` remains readable for retained jobs and the smoke lane. The first-class launcher moves to v2 after capability/catalog APIs exist.

### 5.2 Catalog API

Add bounded read-only endpoints:

- `GET /api/molecular-dynamics/capabilities`
- `GET /api/molecular-dynamics/chemistry-profiles`
- `GET /api/molecular-dynamics/chemistry-profiles/{profile_id}`

Responses distinguish installed, runtime-validated, scientifically validated, operator-enabled, and engine-compatible. They include no host-secret paths and do not expose unvalidated profiles as launchable.

### 5.3 Stable errors

- `MD_CHEMISTRY_PROFILE_UNKNOWN`
- `MD_CHEMISTRY_PROFILE_UNAVAILABLE`
- `MD_CHEMISTRY_PROFILE_NOT_VALIDATED`
- `MD_CHEMISTRY_COMBINATION_UNSUPPORTED`
- `MD_CHEMISTRY_COMPONENT_UNMATCHED`
- `MD_LIGAND_PARAMETERS_REQUIRED`
- `MD_METAL_MODEL_REQUIRED`
- `MD_PARAMETER_REVIEW_REQUIRED`
- `MD_PROTOCOL_OVERRIDE_UNSUPPORTED`
- `MD_PREPARED_BUNDLE_INCOMPLETE`

All fail before scheduler, database child creation, filesystem materialization, or engine launch where the invalidity is knowable at ingress.

---

## 6. Force-field packaging and provenance

### 6.1 Runtime separation

Create a dedicated immutable preparation image rather than stuffing every preparation tool into the execution images:

- GROMACS/`pdb2gmx` and preprocessing tools;
- pinned AmberTools and ParmEd for AMBER-family preparation/conversion;
- pinned CHARMM36m/CGenFF-compatible assets and conversion tooling where license permits redistribution;
- optional OpenMMForceFields/OpenFF tooling in a separate candidate layer;
- profile catalog manifests and checksums;
- chemistry reconciliation/validation CLI.

GROMACS and OpenMM execution images remain narrow. Preparation emits a recursively closed bundle containing all required `.top`, `.itp`, force-field include directories or a verified effective topology, coordinates, restraints, parameter files, and a complete closure manifest. A child must not depend on an unrecorded global force-field directory.

### 6.2 Catalog record

Each profile record contains:

- profile ID, semantic version, display name, family;
- supported components/use cases and explicit exclusions;
- source URLs/citations/license/redistribution terms;
- exact downloaded asset hashes and install manifest;
- preparation tool versions/image digest/SIF hash;
- force field, water, ion, ligand, lipid, carbohydrate, and metal assets;
- default protocol and allowed override schema;
- supported engines and topology forms;
- validation fixtures, lane/version, evidence hashes;
- assurance and operator-enabled status.

### 6.3 Promotion test

A profile is promotable only when:

1. all assets are present and checksummed;
2. fixture preparation is deterministic at the manifest/topology level;
3. component/charge/mass/bond/constraint inventory matches expectations;
4. the recursively closed bundle preprocesses with zero unexplained warnings;
5. single-point energy and short dynamics are finite;
6. checkpoint/artifact contracts pass;
7. named scientific validation passes where selection claims scientific support;
8. the deployed catalog probe reports the same profile hash.

---

## 7. Durable lifecycle specification

### 7.1 Authoritative state records

With explicit migration approval, add or finalize:

- `md_runs` — one parent aggregate and immutable normalized request;
- `md_replica_runs` — one row per replica attempt;
- `md_attempt_segments` — append-only execution/resume segments;
- `md_checkpoints` — checkpoint role, step/time, hash, compatibility key, source segment;
- `job_artifacts` — logical artifact identity and checksums;
- `md_events` — idempotent transition/observation journal.

Do not implement attempt/checkpoint history only inside mutable job `params` or directory scanning.

Preparation and finalization are durable CPU-only `Job` children with stage/idempotency identity, launch/process identity, manifests, and terminal observations. They are not merely in-process Nextflow phases. The existing `orchestrator.nf` + `spawn_replicas.py` + `wait_for_children.py` + `aggregate_children.py` path must either create and observe these children through the canonical state service or be retired; it may not remain a parallel state writer.

The persistence contract must specify concrete foreign keys and uniqueness/CAS rules:

- `md_runs.job_id` is the parent `jobs.id` PK/FK and carries `state_version`;
- `md_replica_runs` is unique on `(md_job_id, replica_index, attempt)` with one active attempt per replica;
- `md_attempt_segments` is unique on `(replica_run_id, segment_index)` and stores source checkpoint/segment, execution-plan hash, compatibility key, launch identity, and reservation token;
- `md_checkpoints` is unique by owner segment plus logical role/path; checksum is content metadata;
- `md_events` has a globally unique idempotency key and expected state/version;
- `job_artifacts` is unique by owner job/attempt plus relative logical path;
- parent/child relations use real foreign keys or an explicitly enforced equivalent, never best-effort string linkage.

### 7.2 Parent reducer

One MD state service derives parent state from durable children and the ingestion barrier. Neither Nextflow, the GPU orchestrator, nor a monitor independently writes terminal parent truth.

State axes remain separate:

- generic execution `Job.status`: bounded generic lifecycle/terminal truth;
- scheduler `queue_status`: eligibility/reservation state;
- MD parent `phase`: domain-specific progress listed below;
- `verification_status` and `chemistry_assurance`: scientific truth, never inferred from execution.

Required MD parent phases:

- `validating`
- `preparing`
- `replicas_queued`
- `replicas_running`
- `checkpointing`
- `paused`
- `finalizing`
- `ingesting`
- `completed`
- `partial`
- `failed`
- `cancelling`
- `cancelled`
- `reconciling`

The phase does not expand `Job.status`. API/frontend schemas define which generic and scheduler values are terminal, which axis drives polling, and which action guards apply.

### 7.3 Pause protocol

A running MD pause is never a database boolean flip:

1. CAS parent/replica to `checkpointing` and block new segments/retries;
2. send engine-appropriate checkpoint/termination request to every targeted running attempt;
3. observe process termination through tracked PID/process-group/Nextflow identity;
4. validate checkpoint readability, step/time, system/protocol hashes, and artifact checksums;
5. persist checkpoint and compatibility key;
6. release exactly one GPU reservation per stopped attempt;
7. publish `paused` only after all targeted descendants are terminal or explicitly failed to pause.

If checkpoint creation or process death is unverified, return `MD_PAUSE_INCOMPLETE`; never claim paused.

### 7.4 Resume protocol

1. select the latest accepted checkpoint in the lineage;
2. compare engine, container, backend/precision, system hash, protocol hash, run-input hash, and GPU compatibility class;
3. create a new immutable continuation segment;
4. schedule only on compatible hardware;
5. use GROMACS `-cpi` with validated no-append semantics or the exact supported OpenMM checkpoint route;
6. verify monotonic step/time and no duplicated/overlapping trajectory interval;
7. retain the source segment unchanged.

An incompatible checkpoint returns `MD_CHECKPOINT_INCOMPATIBLE`; it does not silently restart from the beginning.

### 7.5 Retry policy

Automatic retry is allowed only for allowlisted infrastructure failures, such as rejected spawn before process acceptance, transient worker loss, or retryable scheduler/runtime faults. It creates a new attempt/segment and preserves all old evidence.

No automatic scientific retry may alter force field, topology, protonation, timestep, constraints, cutoffs, PME, thermostat/barostat, or stage duration.

- NaN/unstable dynamics: terminal scientific/runtime failure requiring review.
- OOM: retry only if the exact contract can run on a larger compatible GPU; never reduce physics silently.
- Failed analysis: retry analysis without rerunning checksum-valid dynamics.
- One failed replica: retry only that replica; completed replicas remain immutable.

### 7.6 Cancellation

Parent cancel is idempotent and cascades:

- block new children/retries;
- cancel queued descendants without launch;
- terminate running descendants through tracked process identity;
- preserve complete checkpoints/artifacts;
- release reservations exactly once;
- publish parent `cancelled` only when descendants are terminal or durably orphan-classified.

### 7.7 Reconciliation

At API/coordinator startup and periodically:

- compare DB active attempts, launch identities, process/Nextflow truth, GPU reservations, child manifests, and artifact transactions;
- recover accepted launches not yet marked running;
- classify DB-running/process-dead attempts;
- finish ingestion where manifests are complete;
- repair derived parent state idempotently;
- never relaunch solely because an event was replayed;
- emit dry-run audit output before applying any broad repair.

Only a lease-owning reconciler may apply repairs when multiple API processes are possible. The implementation must inventory and retire/delegate every existing MD terminal writer in Nextflow monitoring, GPU orchestration, generic result finalization, and coordinator scripts.

---

## 8. MD-specific operational UI

### 8.1 Queue card/table

Every MD parent row displays:

- MD badge, chemistry profile, assurance, engine(s), replica count;
- parent phase and aggregate progress;
- replica state summary (`completed/running/queued/failed/paused`);
- simulated time versus requested time;
- checkpoint availability;
- assigned physical GPU models for active replicas;
- partial/failure warning and concise typed reason;
- actions allowed by current state.

Add filters for `model=molecular_dynamics`, chemistry profile/family, engine, assurance, parent phase, partial/failed, and checkpoint available.

Child replica jobs are collapsible under the parent rather than flooding the default queue.

### 8.2 MD job detail page

Panels:

1. **Run summary:** workflow status, verification status, chemistry assurance, profile/version/hash, engine/version, requested/achieved simulation.
2. **Lifecycle timeline:** parent and child transitions with timestamps and typed reasons.
3. **Preparation:** component inventory, protonation/termini, force field/water/ions, warnings, topology hash and chemistry manifest.
4. **Replica matrix:** replica, attempt, seed, stage, simulated time, status, scheduler GPU UUID/model/index, container ordinal, checkpoint, retry reason.
5. **Attempts and lineage:** immutable attempt/segment tree showing retry/resume ancestry and compatibility keys.
6. **Checkpoint inventory:** stage, step/time, size/hash, readability, compatibility, source segment.
7. **Provenance:** request/profile/protocol/image hashes and exact commands without secrets.
8. **Artifacts:** semantic role, owner attempt, size/hash, availability/download.
9. **Diagnostics:** typed preparation, launch, CUDA, engine, ingestion, and scientific-QC failures with operator next step.
10. **Controls:** pause, resume, retry replica, reconcile, cancel replica, cancel parent; disabled with a reason when invalid.

Trajectory visualization remains a separate later design decision. This page specifies lifecycle/data inventory, not the viewer implementation.

### 8.3 API surface

Add owner-scoped bounded detail/actions, or equivalent routes consistent with the existing jobs router:

- `GET /api/jobs/{job_id}/md`
- `POST /api/jobs/{job_id}/md/pause`
- `POST /api/jobs/{job_id}/md/resume`
- `POST /api/jobs/{job_id}/md/retry`
- `POST /api/jobs/{job_id}/md/reconcile`
- existing cancel route extended with MD cascade semantics

Action requests include expected status/version or idempotency key. Race conflicts return 409 with current state, never double-action.

---

## 9. Phase-level implementation roadmap

Dependency order is binding:

1. freeze a reconstructable target-scoped candidate and settle state/schema identity;
2. C0 capability truth;
3. L1 persistence **and transactional MD artifact ingestion**;
4. C1/C2 immutable preparation bundles and v2 chemistry contracts;
5. L2 controls/reconciliation across every old and new action/writer path;
6. MD-aware queue/detail backend projections;
7. U1 frontend;
8. V1 canonical runtime reconciliation;
9. V2 scientific promotion.

Because the current plan and MD implementation are untracked in a heavily dirty checkout, implementation must first preserve a byte-identical external snapshot/patch and target-scoped hashes. No commit is authorized by this plan.

### Phase C0 — Truthful capability catalog and current-profile correction

**Objective:** Remove hard-coded or absent force-field choices and establish deployed chemistry truth.

**Files:**

- Create: `platform/api/services/md/chemistry_catalog.py`
- Create: `platform/api/config/md_chemistry_profiles/*.yaml`
- Create: `platform/api/routers/molecular_dynamics.py`
- Modify: `platform/api/main.py`
- Modify: `platform/frontend/src/components/MolecularDynamicsTemplate.tsx`
- Modify: `platform/frontend/src/components/molecularDynamicsUiState.ts`
- Test: `platform/api/tests/test_md_chemistry_catalog.py`
- Test: `platform/frontend/tests/molecularDynamicsChemistryCatalog.test.ts`

**Work:**

1. Write failing tests proving a hard-coded unavailable `charmm36-jul2022` choice cannot appear as selectable.
2. Inventory deployed parameter directories and profile assets at runtime.
3. Implement installed/validated/selectable state resolution.
4. Drive launcher choices from the capability API.
5. Label `amber99sb-ildn` as smoke-only.

**Gate:** UI and API advertise exactly the deployed selectable profiles; an installed-but-unvalidated profile is visible only in an operator inventory.

### Phase C1 — Preparation image and versioned chemistry profiles

**Objective:** Install multiple modern families in one pinned preparation lane and emit self-contained bundles.

**Files:**

- Create: `containers/md-preparation/Dockerfile`
- Create: `containers/md-preparation/environment.yml` or a lock file
- Create: `scripts/bms_md/chemistry/catalog.py`
- Create: `scripts/bms_md/chemistry/prepare.py`
- Create: `scripts/bms_md/chemistry/reconcile.py`
- Create: `schemas/md_chemistry_profile_v1.schema.json`
- Modify: `scripts/bms_md/gromacs_pipeline.py`
- Modify: `scripts/bms_md/openmm_pipeline.py`
- Modify: `workflows/experimental/molecular_dynamics/orchestrator.nf`
- Modify: `workflows/experimental/molecular_dynamics/replica.nf`
- Modify: `modules/experimental/molecular_dynamics/{prepare,gromacs_replica,openmm_replica,finalize}.nf`
- Modify: `nextflow.config`
- Test: `tests/md/chemistry/`

**Work:**

1. Pin and license-audit AmberTools/ParmEd, CHARMM36m assets, GAFF2/CGenFF tooling, profile files, and transitive environment.
2. Record source and hashes for every redistributed or externally mounted asset.
3. Implement deterministic catalog probing and one shared preparation child that emits an immutable recursively closed bundle; replicas consume its hash rather than independently repeating preparation.
4. Add modern protein profiles first, then protein–DNA profiles.
5. Build fixture matrix for protein, DNA, protein–DNA, RNA, ligand, membrane, and unsupported metal cases.

**Gate:** Every installed profile has reproducible inventory evidence; every selectable profile emits a self-contained bundle and zero unexplained unmatched components.

### Phase C2 — `bms.md.job.v2` and launcher profile UX

**Objective:** Replace free-form chemistry fields with a versioned profile contract and useful bounded controls.

**Files:**

- Create: `schemas/md_job_v2.schema.json`
- Modify: `platform/api/services/md/launch_contract.py`
- Modify: `scripts/bms_md/contract.py`
- Modify: `platform/api/config/models/molecular_dynamics.yaml`
- Modify: `platform/frontend/src/components/MolecularDynamicsTemplate.tsx`
- Modify: `platform/frontend/src/components/molecularDynamicsUiState.ts`
- Test: backend schema/launch negative matrix and frontend serialization tests

**Gate:** `schema` is the single contract discriminator; the launch path performs real JSON-schema/Pydantic validation plus server-side replica/step/output/resource limits rather than frontend-only checks; v2 launcher/API cannot create an unsupported combination; v1 retained jobs remain read-only/relaunch-upgrade compatible; server-resolved profile hash and assets are immutable provenance.

### Phase L1 — Durable state and read model

**Objective:** Persist attempts, segments, checkpoints, artifacts, and transition events; transactionally ingest MD manifests/artifacts before completion; then expose one bounded MD detail response.

**Files:**

- Modify: `platform/api/database.py`
- Modify: approved additive migration path **only after explicit authorization**
- Create: `platform/api/services/md/state.py`
- Create: `platform/api/services/md/read_model.py`
- Create: `platform/api/services/md/artifacts.py`
- Modify: `platform/api/schemas.py`
- Modify: `platform/api/routers/molecular_dynamics.py`
- Modify: `platform/api/services/result_ingester.py`
- Modify: `platform/api/services/result_state_integrity.py`
- Modify: `platform/api/services/result_contracts.py`
- Modify: `scripts/bms_md/aggregate.py`
- Modify: `scripts/bms_md/aggregate_children.py`
- Create/modify: MD aggregate/replica manifest JSON schemas
- Test: `platform/api/tests/test_md_state_machine.py`
- Test: `platform/api/tests/test_md_read_model.py`
- Test: transactional MD ingestion, hash conflict, replay, and completion-barrier suites

**Gate:** process/API restart preserves exact lineage; duplicate observations are idempotent; no terminal parent can contain a required active child; parent `completed` is impossible until schema-valid, checksum-valid required MD artifacts commit transactionally.

### Phase L2 — Pause, resume, retry, cancel, and reconciliation

**Objective:** Implement the full lifecycle protocols in §7.

**Files:**

- Create: `platform/api/services/md/control.py`
- Create: `platform/api/services/md/reconciler.py`
- Modify: `platform/api/main.py`
- Modify: `platform/api/routers/queue.py`
- Modify: `platform/api/routers/jobs.py`
- Modify: `platform/api/services/job_control.py`
- Modify: `platform/api/services/gpu_orchestrator.py`
- Modify: `platform/api/services/nextflow.py`
- Modify: `workflows/experimental/molecular_dynamics/orchestrator.nf`
- Modify: `scripts/bms_md/spawn_replicas.py`
- Modify: `scripts/wait_for_children.py`
- Modify: `scripts/bms_md/aggregate_children.py`
- Modify: `scripts/bms_md/runner.py`
- Modify: `scripts/bms_md/gromacs.py`
- Modify: `scripts/bms_md/gromacs_pipeline.py`
- Modify: `scripts/bms_md/openmm_pipeline.py`
- Modify: engine adapters/checkpoint inspectors
- Test: signal interruption, checkpoint validation, no-append continuity, race, orphan, replay, and API-restart suites

**Gate:** generic queue/jobs actions cannot bypass MD controls; pause is not published before verified process death and checkpoint validation; resume cannot cross incompatible execution classes; unique segment directories preserve prior outputs and pass time-overlap checks; reservations release exactly once; replay does not duplicate launches; startup runs one lease-owning reconciler.

### Phase U1 — Queue and MD detail UI

**Objective:** Make lifecycle truth operable without reading raw logs or directories.

**Files:**

- Create: `platform/frontend/src/components/md/MolecularDynamicsJobDetail.tsx`
- Create: `platform/frontend/src/components/md/MdReplicaMatrix.tsx`
- Create: `platform/frontend/src/components/md/MdAttemptLineage.tsx`
- Create: `platform/frontend/src/components/md/MdCheckpointInventory.tsx`
- Create: `platform/frontend/src/components/md/MdChemistrySummary.tsx`
- Modify: `platform/frontend/src/components/JobDetailPage.tsx`
- Modify: `platform/frontend/src/components/JobDetailsPanel.tsx`
- Modify: `platform/frontend/src/components/JobQueuePanel.tsx`
- Modify: `platform/frontend/src/components/dashboard/JobQueueTable.tsx`
- Modify: `platform/frontend/src/components/dashboard/JobFilters.tsx`
- Modify: `platform/frontend/src/components/Dashboard.tsx`
- Modify: frontend API/types
- Modify: `platform/frontend/src/lib/api.ts`
- Modify: `platform/api/routers/queue.py`
- Modify: queue response schemas/read-model projection
- Test: component/contract/browser state matrix

**Gate:** the backend queue projection includes parent/child identity, chemistry, replica aggregate, phase/progress, checkpoint availability, and typed failure; default queue collapses MD children; every MD state/action and disabled reason is represented; DB/API/UI agree during queued, running, checkpointing, paused, partial, failed, cancelling, and completed states.

### Phase V1 — Canonical GROMACS artifact reconciliation

**Objective:** Resolve the historical SIF drift before broader acceptance.

**Work:**

1. Preserve both known SIF hashes and metadata.
2. Identify whether retained benchmark bytes can be recovered.
3. Choose/rebuild one canonical GROMACS 2025.3 SIF with pinned ancestry.
4. Verify `apptainer exec --nv ... gmx mdrun -version` and real trajectory.
5. Rerun all-GPU smoke and representative benchmark if execution equivalence cannot be proven.
6. Update capability catalog and benchmark provenance atomically.

**Gate:** deployed SIF hash, capability response, smoke evidence, and benchmark evidence identify the same canonical execution artifact or clearly separate non-equivalent evidence.

### Phase V2 — Scientific validation ladder

**Objective:** Promote chemistry profiles by named evidence, not installation.

Order:

1. 1AKI smoke across RTX 5060 Ti, both RTX 3090s, and RTX 5090.
2. Modern soluble-protein profile fixture.
3. Public protein–DNA reference comparing named OL15, parmbsc1, OL21, and any CHARMM candidate under preregistered scope.
4. DRT4 9VDV lower-complexity mutant/tetramer runtime shakeout, explicitly not a WT or chemistry substitute.
5. Implement and engine-validate free-dNTP plus joint dCTP/protonation/Mn/water parameter packs and 9VDP Tyr125 product-linkage pack.
6. DRT4 9VDP product-state preparation/short pilot only after covalent-pack acceptance.
7. DRT4 9VDO dCTP/two-Mn primary and joint sensitivity models only after dNTP/metal force-energy parity and chemistry review.
8. Matched controls/mutants under their own immutable state contracts.
9. Optional RNA, ligand, membrane, glycoprotein lanes.

Each lane freezes source hashes, exact chemical state, observables, exclusions, replica count, stationarity/extension rules, uncertainty, and pass/fail thresholds before production.

### Phase Q1 — Broad verification and documentation

**Objective:** Close repository-wide quality gaps without hiding unrelated baseline failures.

- focused backend/frontend/Nextflow/engine tests;
- full backend suite with exact failing-test inventory;
- production frontend TypeScript build and Node tests;
- browser QA for catalog, valid/invalid launch, all lifecycle states, action races;
- secrets/generated-path scan;
- `git diff --check` and target-scoped dirty-work review;
- operator docs for profile installation/promotion/disable/rollback;
- user docs distinguishing installed, selectable, scientifically validated, and uploaded-unreviewed;
- exact artifact hashes and validation reports.

**Gate:** no MD-specific regression; unrelated baseline failures are named separately and do not get mislabeled as MD failures or silently ignored.

---

## 10. Required test matrices

### Chemistry negative matrix

- profile missing/uninstalled/disabled/hash mismatch;
- incompatible force field + water/ions;
- unsupported residue, ligand, nucleotide, lipid, carbohydrate, metal;
- unexpected atom deletion/rename/charge;
- missing include or conditional topology dependency;
- high CGenFF penalty/review-required;
- unsupported override or profile range;
- generic Mn²⁺ request for catalytic DRT4 site;
- UI stale-catalog submission after server catalog changes.

### Lifecycle fault matrix

- cancellation before child creation, while queued, during launch, during dynamics, during finalization;
- pause during queued and running stages;
- signal interruption with valid/invalid/missing checkpoint;
- resume on same, compatible, and incompatible GPU classes;
- duplicate pause/resume/retry requests;
- API death before/after spawn identity persistence;
- process death before terminal event;
- event replay and duplicate manifest ingestion;
- one replica failed with others completed;
- OOM on smaller GPU and compatible larger-GPU retry;
- low-disk checkpoint termination;
- artifact hash conflict.

### UI state matrix

- parent phases and replica aggregate counts;
- multiple attempts and lineage ancestry;
- scheduler versus container GPU identity;
- checkpoint usable/unusable/incompatible;
- partial failure with retained artifacts;
- action enabled/disabled reasons;
- profile assurance and validation lane;
- no trajectory data loaded into queue/detail JSON.

---

## 11. Product completion gate

This completion tranche is done only when:

- force-field choices come from a runtime-probed profile catalog;
- modern protein and protein–DNA profiles are installed and at least one of each is scientifically validated/selectable;
- legacy AMBER99SB-ILDN is visibly smoke-only;
- no unsupported force-field/water/ion/ligand/metal combination can schedule;
- the MD detail page renders parent, replicas, attempts, GPUs, checkpoints, lineage, artifacts, provenance, and typed failures;
- pause means checkpoint → terminate → verify → paused;
- resume validates lineage/compatibility and produces continuous nonduplicated time;
- retry preserves prior attempts and never changes physics silently;
- cancellation drains descendants and releases reservations exactly once;
- startup reconciliation is idempotent and fault-tested;
- queue filters/cards expose meaningful MD state without flooding users with child jobs;
- canonical GROMACS SIF provenance is reconciled;
- 1AKI all-GPU, protein–DNA, and DRT4 staged gates are either passed or visibly remain blocked with exact reasons;
- broad tests and docs match only deployed facts;
- no unrelated dirty work is overwritten or included;
- no commit, migration, deployment, or restart occurs without separate explicit authorization.

Only after this gate should BMS choose and implement the interactive trajectory/data viewing architecture.

## 12. External scientific anchors

Use primary/original sources during implementation and pin exact citations in profile records. Current planning anchors include:

- GROMACS force-field guidance: `https://manual.gromacs.org/current/user-guide/force-fields.html`
- AMBER force-field inventory: `https://ambermd.org/AmberModels.php`
- ff19SB publication (OPC recommendation): PubMed `31714766`
- protein–DNA/divalent-metal comparison context: PubMed `41432306`
- DNA force-field comparison context: PubMed `31805230`
- CHARMM36 parameter distribution: `https://mackerell.umaryland.edu/charmm_ff.shtml`
- OpenMMForceFields inventory/tooling: `https://github.com/openmm/openmmforcefields`
- Lipid21 publication: DOI `10.1021/acs.jctc.1c01217`

These anchors inform candidate selection; they do not substitute for BMS profile-specific runtime and scientific validation.
