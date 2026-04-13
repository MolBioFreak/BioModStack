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
- [main.nf](../main.nf)
- the Dorado/modkit modules under `modules/`

## BioXP Robotics Surface

Frontend route:
- `/bioxp`

Primary frontend client:
- [platform/frontend/src/lib/bioxpClient.ts](../platform/frontend/src/lib/bioxpClient.ts)

Primary backend router:
- [platform/api/routers/bioxp.py](../platform/api/routers/bioxp.py)

This is a workstation-linked remote hardware cockpit, not a generic cloud API.

Current capabilities include:

- remote daemon linkage and disconnect
- daemon start/stop/status over SSH
- proxied hardware status
- axis status and motion actions
- motion power and interlock preparation
- camera device/control/stream handling
- thermal and chiller control flows
- latch and LED actions

Runtime dependencies:

- a reachable remote BioXP daemon
- working SSH from the BMS workstation to the robot host
- `BIOXP_*` environment variables where custom host/user/port/repo paths are
  needed

Important env vars:

- `BIOXP_SSH_USER`
- `BIOXP_SSH_HOST`
- `BIOXP_DAEMON_PORT`
- `BIOXP_REPO_DIR`
- `BIOXP_SERVER_URL`
- `BIOXP_LINKAGE_STATE_PATH`

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
