# Lab Automation, Mol Bio, and Sequencing

BioModStack is not limited to protein-design workflows. The live UI and API
also expose sequence operations, nanopore analysis, and BioXP hardware control.

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
- [platform/api/routers/bioxp.py](../platform/api/routers/bioxp.py)

This is a BMS-linked cockpit for a robot-local BioXP runtime, not a generic
cloud API and not a shell-based daemon supervisor.

Current capabilities include:

- runtime linkage and disconnect
- linked-runtime reachability/status via the BioXP proxy
- proxied hardware status for the currently exposed BMS BioXP route family
- manual axis status and motion actions for `x`, `y`, `z`, `g`, and `door`
- motion power and interlock preparation
- camera device/control/stream handling
- thermal and chiller control flows
- latch and LED actions
- protocol/operator surface integration through the BioXP cockpit

Current operational caveats:

- BMS currently proxies the route families used by the cockpit and validation
  for status, motion reference, liquid handling, camera stream state, vision,
  and protocol execution; it is still a curated subset rather than a full mirror
  of the robot-local BioXP API.
- Historical notes that `/motion/reference/status` and `/liquid/*` were absent
  from `/api/bioxp/*` are stale for current builds. Future capability notes
  should be checked against live `/api/bioxp/capabilities` route parity before
  being treated as current.
- `/api/bioxp/status` and `/api/bioxp/daemon/status` can diverge transiently
  during reconnect/recovery windows; treat that as control-plane status drift,
  not as a standalone hardware verdict.
- Repeated camera/UVC control-query failures and the historical Novo USB/CAN
  reset pattern should be documented as unresolved transport/recovery
  instability. Software reconnect/reset behavior remains a major confounder, so
  the current docs do not treat this as a closed "bad componentry" or blanket
  hardware-failure verdict.

Runtime dependencies:

- network reachability from BMS to the robot-local BioXP runtime URL
- a robot-local BioXP runtime supervised outside BMS (for example
  `bioxp-api.service`)
- `BIOXP_*` environment variables only when overriding default linkage or
  persistence behavior

Important env vars:

- `BIOXP_SERVER_URL`
  optional seed/default linkage URL loaded by BMS on startup
- `BIOXP_LINKAGE_STATE_PATH`
  file path where BMS persists the operator-selected linkage URL
- `BIOXP_SSH_HOST`
  legacy variable name used only to derive the recommended default host in the
  cockpit linkage UI
- `BIOXP_DAEMON_PORT`
  port used when constructing the recommended runtime URL (default `8123`)

Normal operation does not use BMS to start or stop the robot daemon. The
compatibility maintenance endpoints remain disabled and return a conflict if
called; use the robot-local service/runbook instead.

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
