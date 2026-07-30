# BioModStack Molecular Dynamics — Practical Product Specification

**Status:** implementation target for the first usable MD release
**Replaces:** user-facing interpretation of the longer lifecycle/chemistry planning documents
**Principle:** one launcher, one MD Operations page, one results/viewer owner, direct controls

## 1. What the product does

BioModStack runs GPU-accelerated explicit-solvent molecular dynamics for:

1. soluble proteins;
2. protein–DNA complexes;
3. reviewed system-specific chemistry packs, including future DRT4 states.

A user submits a structure, chooses a supported chemistry profile, sets the run length and replica count, and starts the job. BioModStack prepares the system, runs each replica, tracks progress, supports pause/resume/cancel/retry, and presents trajectories and reports in MD Operations.

This release does **not** add per-user ACLs, policy engines, generic workflow builders, or a second molecular viewer.

## 2. User experience

### 2.1 Launcher

The normal launcher exposes only inputs a scientist needs:

- input structure;
- chemistry profile;
- temperature and pressure;
- minimization, NVT, NPT, and production duration;
- replica count;
- output/checkpoint cadence;
- GPU selection when manual placement is enabled.

Profile-specific advanced inputs may expose box padding, salt concentration, protonation overrides, termini, and disulfides. Low-level force-field internals remain part of the selected profile rather than becoming dozens of unrelated dropdowns.

The launcher names the actual engine and chemistry, for example:

- **GROMACS 2025.3 — ff19SB/OPC protein**
- **GROMACS 2025.3 — ff19SB/OL15/OPC protein–DNA**

### 2.2 MD Operations

MD Operations is the sole operational page. It shows a bounded newest-first list with:

- job name and ID;
- chemistry and engine;
- current phase;
- replica progress;
- elapsed/remaining work when available;
- GPU assignment;
- available actions;
- failure reason;
- expandable run details.

Controls are direct:

- **Pause** — checkpoint and stop all running replicas;
- **Resume** — continue every paused replica from its recorded checkpoint;
- **Cancel** — stop the run;
- **Retry** — retry only a failed replica that encountered an operational/infrastructure failure;
- **Open Results** — open the existing MD results and trajectory viewer.

Generic queue controls and generic structure viewers do not own MD jobs.

## 3. Execution contract

### 3.1 Request

New launches use `bms.md.job.v2`. The server resolves the selected chemistry and protocol into an immutable normalized request before scheduling.

The request binds:

- source-structure hash;
- chemistry profile ID and hash;
- preparation runtime image hash;
- execution runtime image hash;
- preparation settings;
- protocol and stage lengths;
- replica count and seeds;
- GPU offload mode;
- output cadence.

Identical scientific requests may be launched as separate jobs.

### 3.2 Preparation

Preparation produces an immutable bundle containing:

- source structure;
- prepared coordinates;
- AMBER topology and coordinates;
- GROMACS topology and coordinates;
- preparation report;
- file hashes and bundle hash.

Preparation is atomic: either the complete verified bundle is published or no bundle is admitted.

### 3.3 Canonical runtime

Canonical execution uses the pinned CUDA-enabled GROMACS 2025.3 image.

For OPC virtual-site systems, supported GPU execution is:

- GPU nonbonded forces;
- GPU PME;
- GPU bonded forces;
- CPU coordinate update.

Strict GPU update is not advertised for these systems.

Each replica is independently scheduled and owns exactly one GPU. CPU minimization or setup work may precede GPU dynamics.

## 4. Durable lifecycle

### 4.1 Records

BioModStack durably records:

- run;
- replica attempt;
- execution segment;
- checkpoint;
- artifact;
- lifecycle event;
- scheduler child link.

The MD records are lifecycle truth. Generic scheduler rows are launch/status projections.

### 4.2 Pause

Pause performs this direct sequence for every running replica:

1. record the pause request;
2. signal the tracked Nextflow/GROMACS worker;
3. allow GROMACS to write its checkpoint;
4. wait for the worker to stop;
5. validate the checkpoint with GROMACS;
6. record its step, simulation time, size, and SHA-256;
7. attach it to the exact run/replica/attempt/segment/child lineage;
8. release the GPU assignment;
9. mark the replica paused;
10. mark the run paused after all active replicas are paused or completed.

If the API restarts after the worker stops, it can consume the existing checkpoint receipt and finish the same pause request. It must not invent checkpoint metadata.

### 4.3 Resume

Resume is one run-wide action:

1. require every active replica to be paused or completed;
2. resolve exactly one verified checkpoint artifact for each paused replica;
3. create the next segment linked to that checkpoint;
4. queue the existing replica child;
5. verify the checkpoint hash before launch;
6. continue the original replica output in place using GROMACS `-cpi`, `-append`, and explicit `-cpo`;
7. keep completed replicas completed.

Partial continuation of a mixed running/paused run is not offered.

### 4.4 Retry

Retry creates a new immutable attempt. It is available only for server-classified operational failures such as worker loss, scheduler transients, or runtime launch failure. Scientific failures—bad topology, unstable chemistry, invalid outputs, or failed scientific checks—remain failed and require a changed request.

### 4.5 Cancel and recovery

Cancel stops tracked replica workers and records a terminal cancelled result. On service restart, reconciliation compares durable MD state with worker/scheduler state and finishes interrupted lifecycle transitions without changing completed scientific artifacts.

## 5. Results and provenance

Every completed replica publishes:

- normalized request;
- preparation identity;
- topology and coordinates;
- run input;
- trajectory;
- energy file;
- engine log;
- final checkpoint;
- stage ledger;
- validation report;
- analysis report;
- atom-order manifest;
- representative structure.

Artifacts carry exact hashes, byte counts, run/replica/attempt/segment lineage, and source relationships.

The existing MD results pane remains the sole trajectory viewer. It provides:

- bounded frame loading;
- exact source frame, step, and time;
- play, pause, and loop;
- one Mol* owner and canvas;
- full-screen behavior that minimizes surrounding UI;
- trajectory/report downloads tied to the exact source artifacts.

## 6. Chemistry lanes

### 6.1 Soluble protein

Initial modern lane:

- ff19SB protein;
- OPC water;
- matched monovalent ions;
- 0.15 M target salt unless overridden within the supported lane;
- explicit disulfide and termini handling;
- canonical GROMACS runtime.

Acceptance system: 1AKI preparation plus a named dynamics acceptance run.

### 6.2 Protein–DNA

Initial lane:

- ff19SB protein;
- OL15 DNA;
- OPC water;
- matched monovalent ions;
- explicit DNA termini and residue mapping;
- canonical GROMACS runtime.

Acceptance system: 1LMB preparation plus a named dynamics acceptance run.

### 6.3 DRT4

DRT4 is a system-specific pack lane, not a generic metal dropdown.

Current source truth:

- 9VDO supports six dCTP sites and twelve Mn sites;
- 9VDV contains D240A/D241A;
- 9VDP does not support the previously proposed Tyr125–DNA-phosphate covalent bond.

Therefore BioModStack must not invent that bond or label an invented topology as deposited 9VDP chemistry.

DRT4 becomes selectable only when a reviewed pack exists for a precisely named state. The pack must include the selected structure/assembly, sequence, mutations, dCTP state, two-Mn model, protonation, missing atoms, waters/ions, topology, and hashes. Until then, DRT4 remains visibly unavailable while soluble-protein and protein–DNA MD can ship.

## 7. Release acceptance

### 7.1 Required for the first usable release

- `bms.md.job.v2` launches through the real scheduler;
- preparation bundles verify against pinned runtime and profile hashes;
- canonical GROMACS GPU-force/CPU-update execution works;
- pause produces a real validated checkpoint and stopped worker;
- resume continues from that exact checkpoint;
- cancel and allowed retry work;
- API/service restart preserves lifecycle state;
- MD Operations presents the correct actions and errors;
- completed jobs open only in the existing MD results/viewer owner;
- exact trajectory/checkpoint/report provenance is downloadable;
- soluble-protein and protein–DNA named acceptance runs pass;
- Development is verified from the exact candidate before Production promotion.

### 7.2 Explicitly deferred without blocking the first release

- selectable DRT4 chemistry until its reviewed pack exists;
- membrane, RNA, glycoprotein, and arbitrary ligand chemistry;
- strict GPU update for OPC virtual-site systems;
- OpenMM as a second production-default engine;
- enterprise authorization or policy systems.

## 8. Completion definition

The MD product is complete for the first release when a user can launch, observe, pause, resume, cancel, inspect, and download a modern soluble-protein or protein–DNA run from the normal BioModStack UI, and every displayed result can be traced to the exact runtime, request, replica, segment, and artifact bytes that produced it.

DRT4 completion is reported separately and does not reduce the completion percentage of the explicitly scoped first release; it remains a named deferred lane until real chemistry exists.
