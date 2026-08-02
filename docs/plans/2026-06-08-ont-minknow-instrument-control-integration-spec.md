# ONT MinKNOW Instrument-Control Integration Specification and Gap Assessment

> **For Hermes:** Use `subagent-driven-development` only after Christian approves a phase. This document is a source-grounded implementation spec and gap assessment for adding Mk1D/Mk1B instrument control to the BioModStack NGS core. Do not treat it as proof that BMS can already start an ONT run.

**Goal:** Make BioModStack recognize and control ONT Mk1D/Mk1B runs through MinKNOW, then hand real run outputs into BMS plasmid/construct verification workflows.

**Architecture:** MinKNOW remains the hardware driver and sequencing-protocol control plane. BMS adds a host-side MinKNOW adapter, a BMS API/device-run model, an NGS UI instrument mode, and a handoff from MinKNOW output files into typed Nextflow sequence-QC workflows. Nextflow continues to own reproducible file-based analysis only; it must not own live device handles.

**Tech Stack:** ONT `minknow_api` Python package and gRPC APIs, BMS host agent, FastAPI, React/TypeScript, existing BMS job launcher, Nextflow DSL2, Dorado/FASTQ/plasmid-QC workflow artifacts, `sequence_qc.manifest.v1`.

---

## 1. Executive Answer

BMS currently handles ONT/NGS **data analysis**, not ONT **instrument control**.

The target state is:

```text
BMS NGS UI
→ BMS API
→ BMS host-agent ONT MinKNOW adapter
→ MinKNOW Manager/API on the host, usually port 9502
→ Mk1D/Mk1B controlled by MinKNOW
→ POD5/FASTQ/BAM output directories
→ BMS analysis job: ont_plasmid_qc / ont_fastq_qc
→ qc_manifest.json / IGV / plasmid verification report
```

The correct integration is not to bypass MinKNOW. ONT's own API is designed for LIMS/automation clients that need to discover positions, inspect flowcells, start/stop protocols, monitor runs, and find output files.

---

## 2. Source-Grounded ONT API Background

Primary ONT surfaces reviewed:

- `https://github.com/nanoporetech/minknow_api`
- `README.md`
- `AUTH.md`
- `proto/minknow_api/manager.proto`
- `proto/minknow_api/instance.proto`
- `proto/minknow_api/device.proto`
- `proto/minknow_api/protocol.proto`
- `proto/minknow_api/acquisition.proto`
- `proto/minknow_api/data.proto`
- `proto/minknow_api/statistics.proto`
- `python/minknow_api/examples/list_sequencing_positions.py`
- `python/minknow_api/examples/start_protocol.py`

Key ONT facts:

1. MinKNOW is the ONT software that controls sequencing devices including MinION Mk1B/Mk1D when installed on a user PC.
2. The MinKNOW API is gRPC-based.
3. The Python package `minknow_api` is the practical integration surface.
4. The Manager service is the API entrypoint and is normally available on port `9502`.
5. `manager.flow_cell_positions()` discovers flow-cell positions and tells clients how to connect to each position.
6. `position.connect()` returns a per-position connection exposing services such as `device`, `instance`, `protocol`, `acquisition`, `statistics`, and `data`.
7. `device.get_flow_cell_info()` reports whether a flowcell is present and exposes flowcell/product metadata.
8. `instance.get_output_directories()` reports absolute paths local to the machine where MinKNOW is installed.
9. `protocols.find_protocol(...)` resolves a runnable protocol for product code, kit, config, and barcoding choices.
10. `protocols.start_protocol(...)` starts a MinKNOW sequencing protocol and returns a run ID.
11. `protocol.get_run_info(...)`, `protocol.get_current_protocol_run(...)`, `protocol.watch_current_protocol_run(...)`, `acquisition.current_status()`, and `acquisition.get_progress()` are run-monitoring surfaces.
12. `data.get_live_reads()` and Read Until / adaptive-sampling style control are out of scope for the first BMS instrument-control implementation.

Authentication facts:

- Local MinKNOW access may use local-token behavior handled by `minknow_api`.
- Remote or stricter access may require a developer API token or client TLS certificate/key.
- BMS should support config variables for host, port, token, certificate chain, and private key, but the first implementation can focus on local host-agent access.

---

## 3. MinKNOW API Calls BMS Should Use

### 3.1 Manager / installation / position discovery

Use:

```python
from minknow_api.manager import Manager

manager = Manager(host="localhost", port=9502)
positions = manager.flow_cell_positions()
```

Relevant ManagerService RPCs:

- `describe_host()`
- `get_version_info()`
- `flow_cell_positions()`
- `watch_flow_cell_positions()`
- `local_authentication_token_path()`
- `find_protocols()`
- `list_settings_for_protocol()`
- `get_sequencing_kits()`
- `get_flow_cell_types()`
- `find_basecall_configurations()`
- `get_default_output_directories()`
- `get_disk_space_info()`

BMS usage:

- discover Mk1D/Mk1B positions
- report MinKNOW availability/version
- show plug/unplug state
- look up supported kits/protocol options
- validate basecalling model availability before run start
- verify disk/output health before run start

### 3.2 Per-position connection

Use:

```python
position_connection = position.connect()
```

Then use the per-position services below.

### 3.3 Device / flowcell inspection

Use:

```python
flow_cell_info = position_connection.device.get_flow_cell_info()
```

Relevant DeviceService RPCs:

- `get_device_info()`
- `get_device_state()`
- `stream_device_state()`
- `get_flow_cell_info()`
- `stream_flow_cell_info()`
- `get_sample_rate()`
- `get_temperature()`
- `stream_temperature()`

BMS usage:

- require a real flowcell before enabling instrument-run start
- capture `flow_cell_id`, `product_code`, optional user-specified IDs, and sample rate
- show device/flowcell state in `/bms/ngs`
- avoid fake device states

### 3.4 Output directory inspection

Use:

```python
output_directories = position_connection.instance.get_output_directories()
```

Relevant InstanceService RPCs:

- `get_output_directories()`
- `get_default_output_directories()`
- `set_output_directory()`
- `set_reads_directory()`
- `get_disk_space_info()`
- `stream_disk_space_info()`
- `stream_instance_activity()`

BMS usage:

- determine where POD5/FASTQ/BAM will land
- map host-local MinKNOW paths to BMS-visible paths
- decide whether analysis handoff is possible
- optionally show current protocol/acquisition state through `stream_instance_activity()` later

Do not immediately call `set_output_directory()` by default. Changing MinKNOW output locations must be an explicit, tested operator-controlled feature.

### 3.5 Protocol resolution and run start

Use ONT helper functions:

```python
from minknow_api.tools import protocols

protocol_info = protocols.find_protocol(
    position_connection,
    product_code=product_code,
    kit=kit,
    config_name=config_name,
    barcoding=barcoding,
    barcoding_kits=barcode_kits,
)
```

Then:

```python
run_id = protocols.start_protocol(
    position_connection,
    identifier=protocol_info.identifier,
    sample_id=sample_id,
    experiment_group=experiment_group,
    basecalling=basecalling_args,
    fastq_arguments=fastq_arguments,
    pod5_arguments=pod5_arguments,
    bam_arguments=bam_arguments,
    stop_criteria=stop_criteria,
)
```

Relevant ProtocolService RPCs:

- `start_protocol()`
- `stop_protocol()`
- `pause_protocol()`
- `resume_protocol()`
- `trigger_mux_scan()`
- `wait_for_finished()`
- `get_run_info()`
- `list_protocol_runs()`
- `get_current_protocol_run()`
- `watch_current_protocol_run()`
- `list_protocols()`
- `generate_run_report()`

BMS usage:

- resolve protocol before enabling the Start button
- start a run with BMS sample metadata and output choices
- stop a run only through an explicit operator-confirmed action
- track MinKNOW run ID and tie it to a BMS instrument-run record
- monitor status until output files are available

### 3.6 Acquisition and statistics monitoring

Relevant AcquisitionService RPCs:

- `current_status()`
- `get_progress()`
- `get_acquisition_info()`
- `get_current_acquisition_run()`
- `watch_current_acquisition_run()`
- `watch_for_status_change()`

Relevant StatisticsService RPCs:

- `stream_acquisition_output()`
- `stream_read_length_histogram()`
- `read_length_n50()`
- `stream_q_score_histogram()`
- `stream_basecall_boxplots()`
- `stream_temperature()`
- `stream_bias_voltages()`

BMS usage:

- MVP: poll acquisition/protocol status and output file existence.
- Later: live dashboard for yield, read length, q-score, temperature, bias voltage.

### 3.7 DataService and adaptive sampling

Relevant DataService RPCs:

- `get_live_reads()`
- `get_read_statistics()`
- `get_experiment_yield_info()`
- `get_signal_bytes()`
- `get_channel_states()`

BMS first phase should not use `get_live_reads()` or adaptive sampling. That is a later advanced project and not necessary for Mk1D plasmid verification.

---

## 4. BMS Product Contract

BMS must expose two separate NGS paths:

1. **Analyze existing data**
   - current file-based behavior
   - FASTQ + reference → `ont_fastq_qc` / `ont_plasmid_qc`
   - POD5/BAM paths → basecalling/modkit/analysis as supported

2. **Start instrument run**
   - new MinKNOW-backed behavior
   - requires real detected Mk1D/Mk1B position
   - requires flowcell present
   - resolves kit/protocol/basecalling config
   - starts MinKNOW protocol
   - monitors output directory
   - hands resulting files to BMS analysis workflows

The UI must never imply that a file-only analysis job is a live instrument run.

---

## 5. Current BMS State and Gap Assessment

### 5.1 Already present

Source files already present or added in the current NGS/instrument-control tranche:

- `platform/api/services/ont_ngs_contract.py`
  - canonical ONT/NGS workflow family registry
  - workflow IDs:
    - `ont_basecall_dna`
    - `ont_basecall_rna`
    - `ont_plasmid_qc`
    - `ont_construct_screening`
    - `ont_methylation_analysis`
    - `ont_fastq_qc`
  - defaults and manifest contract mapping

- `platform/api/services/ont_device_control.py`
  - truthful device-control boundary
  - reports `not_configured` by default
  - now has opt-in delegation to a MinKNOW discovery adapter when `BMS_ONT_MINKNOW_ENABLED=1`

- `platform/api/routers/ont_devices.py`
  - exposes `GET /api/ont/devices/status`

- `platform/api/services/ont_minknow_client.py`
  - newly added first adapter layer
  - normalizes MinKNOW `flow_cell_positions()` style output into BMS-safe `live_devices`
  - handles `configured`, `client_missing`, `unreachable`, and `auth_error`
  - does not invent devices

- `platform/api/tests/test_ont_device_control.py`
  - covers not-configured state and delegation to MinKNOW adapter

- `platform/api/tests/test_ont_minknow_client.py`
  - covers normalized fake MinKNOW positions without using fake production devices

- `platform/api/services/nextflow.py`
  - routes canonical ONT workflow IDs to direct `workflows/ngs/ont_*.nf` entrypoints

- `workflows/ngs/ont_*.nf`
  - direct wrappers for canonical ONT workflow IDs
  - now bind product-specific params for direct CLI launches

- `workflows/ngs/nanopore_methylation.nf`
  - current seed workflow for POD5/BAM/FASTQ analysis
  - still carries methylation-era naming but includes FASTQ QC/plasmid-QC behavior

- `platform/frontend/src/components/NanoporeTemplate.tsx`
  - file-based Nanopore/FASTQ launch surface
  - currently submits `model_id: nanopore`, `mode: methylation_analysis`
  - requires FASTQ + reference for FASTQ plasmid QC

- `platform/frontend/src/components/NGSToolkit.tsx`
  - NGS runs and artifact viewing surface
  - still large and path-scraping-heavy in places

- `platform/api/services/sequence_qc_manifest.py`
  - parser and safety layer for sequence-QC manifests

- `platform/api/routers/sequence_qc.py`
  - manifest endpoint surface

### 5.2 What BMS can do now

Current practical capability:

- analyze existing FASTQ + reference for plasmid/FASTQ QC if runtime prerequisites are available
- analyze existing POD5/BAM/FASTQ through the current Nanopore seed workflow path
- show a truthful ONT device-control status endpoint
- represent ONT/NGS workflow IDs and defaults in API source/tests
- normalize MinKNOW discovery results in a unit-testable adapter, but not yet from the host-agent runtime

### 5.3 What BMS cannot do yet

BMS cannot yet:

1. Detect a real Mk1D plugged into the host through the live BMS runtime.
2. Query a host MinKNOW service through the BMS host agent.
3. Show MinKNOW version/auth/output-dir state in the NGS UI.
4. Inspect a specific flowcell position from the UI.
5. Resolve valid protocols/kits/basecalling models for the attached flowcell.
6. Start a MinKNOW sequencing protocol.
7. Record a BMS instrument-run record tied to a MinKNOW run ID.
8. Monitor live protocol/acquisition progress as a BMS run.
9. Locate FASTQ/POD5/BAM output files from a MinKNOW run and prove they are BMS-accessible.
10. Trigger `ont_plasmid_qc` handoff directly from a completed instrument run.
11. Display a single joined instrument-run → analysis-job → manifest/report chain.

### 5.4 Gap severity by area

- MinKNOW API understanding: **low gap**
  - docs/API path is clear enough to implement discovery/start/status wrappers

- BMS source contract for ONT/NGS workflow family: **low to medium gap**
  - registry exists; needs UI/API mode cleanup

- Device discovery adapter: **medium gap**
  - first API-side adapter exists; host-agent wiring and live MinKNOW environment validation missing

- Run start/control: **high gap**
  - no BMS endpoint/model yet for start/stop/monitor

- UI instrument mode: **high gap**
  - current UI is file-analysis-centric, not instrument-centric

- Analysis handoff: **medium/high gap**
  - file-analysis launch exists; instrument-run-to-analysis linkage missing

- Runtime path mapping: **high-risk medium gap**
  - MinKNOW output paths are host-local absolute paths; BMS API/container needs safe mappings

- Live hardware validation: **unknown/high gap**
  - cannot claim until tested against actual MinKNOW + Mk1D

---

## 6. Target Runtime Data Model

Create an instrument-run model separate from the existing Nextflow job model.

Suggested table/model: `OntInstrumentRun`

Fields:

- `id`: BMS UUID
- `minknow_run_id`: MinKNOW protocol run ID
- `position`: e.g. `X1`
- `device_type`: `mk1d`, `mk1b`, or `unknown`
- `flow_cell_id`
- `user_specified_flow_cell_id`
- `product_code`
- `sample_id`
- `experiment_group`
- `kit`
- `protocol_id`
- `basecalling_enabled`
- `basecalling_simplex_model`
- `basecalling_modified_models`
- `basecalling_stereo_model`
- `requested_pod5`: boolean
- `requested_fastq`: boolean
- `requested_bam`: boolean
- `output_directories`: JSON
- `status`: `created`, `starting`, `running`, `finishing`, `completed`, `failed`, `stopped`, `unknown`
- `last_minknow_payload`: JSON summary only, no raw client objects
- `handoff_analysis_job_id`: nullable existing BMS job ID
- `created_at`, `started_at`, `updated_at`, `ended_at`

Do not overload existing Nextflow jobs for live acquisition. Acquisition and analysis have different owners and failure modes.

---

## 7. API Design

### 7.1 Existing endpoint to extend

```text
GET /api/ont/devices/status
```

Current default truth:

```json
{
  "implementation_status": "not_configured",
  "live_devices": [],
  "fake_or_demo_devices": false
}
```

Target configured truth:

```json
{
  "implementation_status": "configured",
  "minknow": {
    "host": "localhost",
    "manager_port": 9502,
    "version": "...",
    "auth_mode": "local_token|api_token|client_cert|unknown"
  },
  "live_devices": [
    {
      "position": "X1",
      "device_type": "mk1d",
      "state": "...",
      "running": false,
      "available_for_run": true,
      "flow_cell": {
        "present": true,
        "flow_cell_id": "...",
        "product_code": "...",
        "sample_rate": 5000
      }
    }
  ],
  "fake_or_demo_devices": false
}
```

### 7.2 New endpoints

Create router:

```text
platform/api/routers/ont_runs.py
```

Add endpoints:

```text
GET  /api/ont/positions
GET  /api/ont/positions/{position}
GET  /api/ont/positions/{position}/protocol-options
POST /api/ont/positions/{position}/start
GET  /api/ont/runs
GET  /api/ont/runs/{run_id}
POST /api/ont/runs/{run_id}/stop
POST /api/ont/runs/{run_id}/handoff/plasmid-qc
```

### 7.3 Host-agent endpoints

Add host-agent routes under `scripts/bms_host_agent.py` or split into an imported module:

```text
GET  /ont/status
GET  /ont/positions
GET  /ont/positions/{position}
GET  /ont/positions/{position}/protocol-options
POST /ont/positions/{position}/start
GET  /ont/runs/{minknow_run_id}
POST /ont/runs/{minknow_run_id}/stop
```

The BMS API container should call the host agent rather than importing `minknow_api` inside the container for production live hardware access.

---

## 8. Implementation Plan

### Phase 0 — Preserve current file-analysis behavior and test baseline

Objective: ensure the new instrument-control work cannot break existing NGS file analysis.

Files:

- Test: `platform/api/tests/test_ont_ngs_contract.py`
- Test: `platform/api/tests/test_ont_ngs_workflow_products.py`
- Test: `platform/api/tests/test_nanopore_nextflow.py`
- Test: `platform/api/tests/test_sequence_qc_manifest.py`
- Test: frontend NGS/Nanopore tests if available

Steps:

1. Run current API NGS tests:

```bash
uv run --directory platform/api python -m pytest \
  tests/test_ont_ngs_contract.py \
  tests/test_ont_ngs_workflow_products.py \
  tests/test_nanopore_nextflow.py \
  tests/test_sequence_qc_manifest.py \
  -q
```

2. Record failures before touching instrument-control code.
3. Do not edit unrelated dirty files.

Acceptance:

- Current file-analysis tests pass or pre-existing failures are documented.
- `/api/ont/devices/status` still reports `not_configured` unless explicitly enabled.

### Phase 1 — Host-agent MinKNOW discovery wrapper

Objective: move real MinKNOW access to host-agent side.

Files:

- Modify: `scripts/bms_host_agent.py`
- Create: `scripts/lib/ont_minknow_host.py` or equivalent host-agent helper
- Test: `platform/api/tests/test_ont_minknow_client.py`
- Test: add host-agent unit tests if a harness exists; otherwise add a pure helper test under API tests only if imported safely

Implementation:

- Use lazy import of `minknow_api.manager.Manager`.
- Add config:
  - `BMS_ONT_MINKNOW_ENABLED`
  - `BMS_ONT_MINKNOW_HOST`
  - `BMS_ONT_MINKNOW_PORT`
  - `BMS_ONT_MINKNOW_API_TOKEN`
  - future cert/key paths
- Implement:
  - `discover_status()`
  - `list_positions()`
  - `inspect_position(position)`
- Normalize errors:
  - `client_missing`
  - `unreachable`
  - `auth_error`
  - `configured`

Acceptance:

- If `minknow_api` is missing, host-agent returns `client_missing` and no devices.
- If MinKNOW is unreachable, returns `unreachable` and no devices.
- If positions exist, returns real normalized positions.
- No simulated devices are returned unless an explicit test-only mode is set and excluded from production status.

### Phase 2 — API proxy to host-agent ONT status

Objective: make BMS API consume host-agent MinKNOW status.

Files:

- Modify: `platform/api/services/host_agent_client.py`
- Modify: `platform/api/services/ont_device_control.py`
- Modify: `platform/api/routers/ont_devices.py`
- Test: `platform/api/tests/test_ont_device_control.py`
- Test: add `platform/api/tests/test_ont_host_agent_proxy.py`

Implementation:

- Add host-agent client method:

```python
get_ont_status()
get_ont_positions()
get_ont_position(position)
```

- Preserve default not-configured behavior when feature disabled.
- If host-agent is unavailable, return truthful degraded status, not fake devices.

Acceptance:

- API endpoint works when feature disabled.
- API endpoint delegates to host-agent when enabled.
- Host-agent timeout is represented as unavailable/degraded.
- Unit tests do not require actual MinKNOW.

### Phase 3 — Protocol options and start-run preflight

Objective: prove BMS can decide whether a specific Mk1D position can start a plasmid verification run.

Files:

- Modify: host-agent ONT helper
- Modify: `scripts/bms_host_agent.py`
- Create/modify: `platform/api/routers/ont_runs.py`
- Create: `platform/api/services/ont_run_control.py`
- Test: `platform/api/tests/test_ont_run_control.py`

Implementation:

- Add protocol option call using:

```python
protocols.find_protocol(...)
manager.find_basecall_configurations(...)
```

- Add preflight response:

```json
{
  "can_start": true,
  "blockers": [],
  "protocol_id": "...",
  "basecalling_options": {...},
  "output_directories": {...}
}
```

Acceptance:

- Missing flowcell blocks run start.
- Already running position blocks run start.
- Unknown kit blocks run start.
- Missing basecalling model blocks run start if basecalling requested.
- Output directory absence/unmapped path is reported before start.

### Phase 4 — Start/stop MinKNOW protocol run

Objective: BMS starts a real MinKNOW protocol through the host agent.

Files:

- Modify: host-agent ONT helper
- Modify: `scripts/bms_host_agent.py`
- Create/modify: `platform/api/services/ont_run_store.py`
- Create/modify: `platform/api/routers/ont_runs.py`
- Test: `platform/api/tests/test_ont_run_start_contract.py`

Implementation:

- Add request schema:

```json
{
  "sample_id": "plasmid_A12",
  "experiment_group": "bms_plasmid_verification",
  "kit": "SQK-...",
  "duration_hours": 1,
  "outputs": {"pod5": true, "fastq": true, "bam": false},
  "basecalling": {"enabled": true, "quality_mode": "sup", "modified_bases": "none"},
  "confirm_start": true
}
```

- Host-agent calls `protocols.start_protocol(...)`.
- API records `OntInstrumentRun`.
- Stop endpoint calls `protocol.stop_protocol(...)` or ONT helper equivalent.

Acceptance:

- Start request requires explicit confirmation.
- Start returns both BMS run ID and MinKNOW run ID.
- Stop requires explicit confirmation.
- Errors include whether failure is protocol resolution, flowcell state, auth, output dir, or MinKNOW runtime.

### Phase 5 — Run monitoring and output discovery

Objective: track a MinKNOW run until files are ready for BMS analysis.

Files:

- Modify: host-agent ONT helper
- Modify: `platform/api/services/ont_run_control.py`
- Modify: `platform/api/routers/ont_runs.py`
- Test: `platform/api/tests/test_ont_run_status_contract.py`

Implementation:

- Poll:
  - `protocol.get_run_info(...)`
  - `acquisition.current_status()`
  - `acquisition.get_progress()`
  - `instance.get_output_directories()`
- Scan output dirs for FASTQ/POD5/BAM using strict allowed path mappings.
- Do not report handoff-ready until real files exist and are readable in BMS path space.

Acceptance:

- Running/finished/stopped/failed states are represented distinctly.
- Missing output files do not become fake paths.
- Host absolute paths are not exposed as primary frontend contracts unless explicitly allowed.

### Phase 6 — Handoff to plasmid verification

Objective: launch BMS plasmid verification from a real instrument run.

Files:

- Modify: `platform/api/routers/ont_runs.py`
- Modify: `platform/api/services/ont_run_control.py`
- Modify: existing job launch service if needed
- Test: `platform/api/tests/test_ont_run_handoff.py`

Implementation:

- Add endpoint:

```text
POST /api/ont/runs/{run_id}/handoff/plasmid-qc
```

- Require:
  - instrument run exists
  - FASTQ exists and is readable
  - reference FASTA provided or selected
  - no unresolved host/container path mapping

- Launch:

```json
{
  "model_id": "nanopore",
  "mode": "plasmid_qc",
  "params": {
    "ont_workflow_id": "ont_plasmid_qc",
    "fastq_path": "...",
    "reference_fasta": "...",
    "run_fastq_qc": true,
    "run_modkit": false,
    "modified_bases": "none",
    "fastq_minimap2_preset": "map-ont",
    "source_instrument_run_id": "..."
  }
}
```

Acceptance:

- Analysis job is linked back to instrument run.
- Manifest endpoint returns `qc_manifest.json` only when real workflow artifacts exist.
- UI can show instrument run → analysis job → manifest/report chain.

### Phase 7 — Frontend NGS instrument mode

Objective: add user-facing Mk1D instrument control without breaking file analysis.

Files:

- Modify: `platform/frontend/src/components/NGSToolkit.tsx` or split shell first
- Create: `platform/frontend/src/components/ngs/OntInstrumentPanel.tsx`
- Create: `platform/frontend/src/components/ngs/useOntDevices.ts`
- Create: `platform/frontend/src/components/ngs/useOntRuns.ts`
- Modify: `platform/frontend/src/lib/api.ts`
- Test: frontend tests under `platform/frontend/tests/`

UI requirements:

- separate tabs/buttons:
  - `Analyze existing data`
  - `Start instrument run`
- show device state:
  - MinKNOW not configured
  - MinKNOW unreachable
  - client missing
  - no devices
  - flowcell absent
  - position running
  - available for run
- require explicit confirmation before starting/stopping run
- show handoff readiness only when real output files exist

Acceptance:

- Existing FASTQ file-analysis launch still works.
- No instrument run button is enabled without a real available position.
- No fake/demo device states are displayed.
- UI can start a test-mocked instrument run in contract tests.

### Phase 8 — Live Mk1D validation

Objective: prove against real hardware and real MinKNOW.

Environment prerequisites:

- Mk1D physically connected
- MinKNOW installed and running on the host
- `minknow_api` installed in the host-agent Python environment
- BMS host-agent has permission to use local MinKNOW auth token or cert/token config
- BMS can read MinKNOW output directories

Acceptance gate:

1. `GET /api/ont/devices/status` shows configured status and a real position.
2. Position detail shows flowcell present.
3. Protocol options resolve for selected kit/config.
4. BMS starts a short run and records MinKNOW run ID.
5. BMS monitors run status.
6. Output FASTQ/POD5 appears in a mapped readable path.
7. BMS launches plasmid QC handoff from real output.
8. Analysis produces:
   - `align/aligned.bam`
   - `align/aligned.bam.bai`
   - `fastq_qc/qc_manifest.json`
   - `fastq_qc/per_base_support.tsv`
9. `/api/sequence-qc/jobs/{job_id}/manifest` returns 200.
10. UI displays manifest/report without fake artifacts.

---

## 9. Validation Commands

API tests:

```bash
uv run --directory platform/api python -m pytest \
  tests/test_ont_minknow_client.py \
  tests/test_ont_device_control.py \
  tests/test_ont_ngs_contract.py \
  tests/test_ont_ngs_workflow_products.py \
  tests/test_nanopore_nextflow.py \
  tests/test_sequence_qc_manifest.py \
  -q
```

Frontend tests after UI work:

```bash
npm --prefix platform/frontend test -- --run
npm --prefix platform/frontend run build
```

Diff hygiene:

```bash
git diff --check -- \
  docs/plans/2026-06-08-ont-minknow-instrument-control-integration-spec.md \
  docs/plans/README.md
```

Live status checks once deployed:

```bash
curl -sS http://127.0.0.1:8000/api/ont/devices/status | python -m json.tool
curl -sS http://127.0.0.1:8798/ont/status | python -m json.tool
```

---

## 10. Definition of Done

BMS reaches the goal when all are true:

1. BMS detects a real Mk1D/Mk1B through MinKNOW without fake devices.
2. BMS shows MinKNOW configured/unreachable/auth/client-missing states truthfully.
3. BMS can inspect flowcell/product/sample-rate/output-directory state.
4. BMS resolves valid protocol and basecalling options for the selected kit/flowcell.
5. BMS can start a MinKNOW protocol with explicit operator confirmation.
6. BMS records the MinKNOW run as a BMS instrument run.
7. BMS can stop a run with explicit operator confirmation.
8. BMS monitors the run and discovers real output files.
9. BMS can hand FASTQ/POD5/BAM outputs into the correct ONT/NGS analysis workflow.
10. BMS links instrument run → analysis job → `qc_manifest.json` → report/IGV evidence.
11. File-only NGS analysis remains available and clearly separate from instrument control.
12. No UI/API/docs claim fake device availability, fake output files, or unverified plasmid QC success.

---

## 11. Recommended Next Action

Implement **Phase 1 + Phase 2** together as the next PR-sized tranche:

```text
host-agent MinKNOW discovery endpoint
→ BMS API proxy
→ /api/ont/devices/status returns real host-agent MinKNOW state when enabled
```

This is the smallest useful step toward Christian's goal because it answers the first operator question:

```text
If I plug in a Mk1D, can BMS see it truthfully?
```

Do not start with run-start UI. First prove detection, auth, and path visibility through the live host-agent boundary.
