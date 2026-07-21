# Lab Automation, Mol Bio, and Sequencing

BioModStack is not limited to protein-design workflows. The live UI and API
also expose sequence operations, nanopore analysis, and the BioXP compact control plane.

## Molecular Biology Toolkit

Frontend route:
- `/designer`

Primary component:
- [platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx](../platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx)

Current surface includes:

- DNA and RNA construct viewing/editing
- sequence shelf / construct library
- construct import and paste flows
- feature and primer editing
- digest planning
- PCR product generation
- search/find utilities
- direct sequence edit operations
- GC-content and annotation-oriented sequence visualization

The mol bio API surface is split across:

- `/api/sequences`
  saved nucleotide constructs
- `/api/molbio`
  digest, PCR, ligation, mutagenesis, Gibson, and Golden Gate operations
- `/api/user-sequences`
  user sequence library helpers
- `/api/user-templates`
  reusable sequence/template assets

## Nanopore / NGS Surface

Frontend route:
- `/ngs`

Primary components:

- [platform/frontend/src/components/NGSToolkit.tsx](../platform/frontend/src/components/NGSToolkit.tsx)
- [platform/frontend/src/components/NanoporeTemplate.tsx](../platform/frontend/src/components/NanoporeTemplate.tsx)

The current sequencing surface is oriented around Oxford Nanopore workflows.

Supported input patterns:

- POD5 input for Dorado basecalling
- existing BAM input for methylation/reporting workflows
- FASTQ input for alignment and QC against a reference

Current sequencing features:

- Dorado basecalling
- optional Dorado alignment
- BAM normalization/preparation for downstream analysis
- modkit methylation summary outputs
- FASTQ plasmid QC
- IGV-ready track and reference artifacts
- optional clone-validation style workflow handoff parameters

The canonical sequencing model config is:
- [platform/api/config/models/nanopore.yaml](../platform/api/config/models/nanopore.yaml)

The workflow logic lives in:
- [ngs.nf](../ngs.nf), which dispatches the nanopore workflow entrypoint
- [workflows/nanopore_methylation.nf](../workflows/nanopore_methylation.nf)
- the Dorado/modkit modules under `modules/`

## BioXP Robotics Surface

Frontend route:
- `/bioxp`

Primary frontend client:
- [platform/frontend/src/lib/bioxpClient.ts](../platform/frontend/src/lib/bioxpClient.ts)

Primary backend router:
- [platform/api/routers/bioxp/](../platform/api/routers/bioxp/)

Canonical current contract:
- [BioXP Compact Control Plane](BioXP_Compact_Control_Plane.md)

This is a BMS-linked cockpit for a robot-local BioXP runtime, not a generic
cloud API and not a shell-based daemon supervisor.

Current capabilities include:

- explicit profile save/forget with masked readback
- process-local connect, disconnect, and bounded readiness probe
- orthogonal configured/active/reachable/runtime/hardware/freshness evidence
- deterministic offline protocol validation
- persistent local jobs with append-only transition events
- server-advertised typed command admission with default mappings disabled
- emergency-stop delivery evidence without a physical-effect claim

Current operational caveats:

- BMS exposes a bounded control plane, not an arbitrary robot proxy. The compact
  API owns one saved profile, one process-local connection, local protocol
  validation, persistent local job truth, bounded command admission, and
  emergency-stop delivery evidence.
- Startup always remains disconnected, even when a profile is saved. An operator
  must explicitly connect, and stale or unknown readiness never authorizes a
  normal command.
- Normal OEM command mappings remain disabled until their online robot contract
  is verified. Offline protocol validation does not claim compatibility or
  executability.
- Repeated camera/UVC control-query failures and the historical Novo USB/CAN
  reset pattern should be documented as unresolved transport/recovery
  instability. Software reconnect/reset behavior remains a major confounder, so
  the current docs do not treat this as a closed "bad componentry" or blanket
  hardware-failure verdict.

Runtime dependencies:

- network reachability from BMS to the explicitly saved robot-local BioXP profile
- a robot-local BioXP runtime supervised outside BMS (for example
  `bioxp-api.service`)
- an allowlisted host/CIDR before enabling robot-facing mutations

Important env vars:

- `BMS_BIOXP_MUTATIONS_ENABLED`
  defaults to `0`; robot-facing commands remain unavailable until explicitly enabled
- `BMS_BIOXP_ALLOWED_HOSTS` / `BMS_BIOXP_ALLOWED_CIDRS`
  explicit target allowlists used when validating the saved profile

Normal operation does not use BMS to start, stop, reset, reboot, shell into, or
collect remote logs from the robot host. Those routes are absent; use the
robot-local service/runbook instead.

## Workstation / System Operations

BMS also exposes workstation-facing support surfaces outside the structure
workflows:

- `/infra`
  system telemetry and power/fan controls
- `start_ui.sh`
  API/frontend launcher
- `biomodstack_panel.py`
  GTK control panel for local service operation

## What These Docs Deliberately Avoid

These docs describe the live surfaces. They do not treat every robotics or
sequencing idea note in `docs/` as current support. If a behavior matters for a
run or operator flow, confirm it in the active frontend route, API router, or
workflow entrypoint referenced above.
