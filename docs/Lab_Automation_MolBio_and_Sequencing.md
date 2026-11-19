# Lab Automation, Mol Bio, and Sequencing

BioModStack is not limited to protein-design workflows. The Development UI and API sources
also expose sequence operations, nanopore analysis, and the BioXP compact control plane.

## Molecular Biology Toolkit

Start at `/designer` for work on a molecule. Use `/ngs` for a sequencing run, even when the reference is the same construct. The [Molecular Biology Toolkit](../platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx) groups work around the selected molecule:

| Task | Where to work and what to keep |
|---|---|
| Create or edit a construct | Import, paste or reopen a DNA/RNA record from the sequence shelf. Use the sequence and feature views to edit or annotate it. Use History to distinguish the current molecule from an earlier revision; share the revision when the exact sequence matters. |
| Review primers and PCR products | Use the Primer and PCR panels on the chosen template. A primer annotation and a simulated product are different records. Keep the template revision with the product. |
| Inspect restriction sites or assemble a construct | Use Digest for cut maps and predicted fragments; use Assembly for restriction–ligation, Gibson overlap assembly or Golden Gate. A preview is not a saved molecule. Reopen the saved product or exact revision before using it as input to another operation. |
| Compare or inspect sequences | Use search, pairwise alignment or RNA structure inspection in the editor. For a family alignment, use the [Alignments workspace](Workspaces.md), not the editor’s pairwise panel. |

For maintainers, [restriction operations](../platform/api/routers/molbio_restriction.py), [Gibson suggestions](../platform/api/routers/molbio_gibson_suggest.py) and [Golden Gate design](../platform/api/routers/molbio_golden_gate_design.py) are distinct from the sequence shelf. A Project reference identifies a record; it is not itself an exported copy of the molecule.

## Nanopore / NGS Surface

Open `/ngs` for ONT run review. Pick the workflow by the input already in hand and the question to answer. The [ONT configuration](../platform/api/config/models/nanopore.yaml) marks the suite experimental; its [workflow implementations](../workflows/ngs/) distinguish these tasks:

- **Basecalling:** DNA and RNA basecalling are separate modes for raw reads. Existing FASTQ reads do not require another basecall just to review read quality.
- **Read and construct QC:** FASTQ QC summarizes reads; plasmid QC compares reads with a reference. Construct screening, pooled reference assignment and clone validation are distinct modes, not names for the same report.
- **Methylation analysis:** review modified-base evidence from compatible inputs, including an already tagged BAM. A plain alignment or FASTQ is not equivalent to modified-base calls.

Open the selected run generation for its reports, alignments and retained files. Use the read and signal workbench below for a particular read or locus. The [BNS deployment guide](BNS_NGS_Runtime_Deployment.md) covers a separate edition; it does not establish which dependencies are installed in full BMS.

### ONT Read and Signal Workbench

The `/ngs` result surface includes the **Read and Signal Workbench** for one
persisted run generation. It uses IGV to view alignments and can show:

- one acquired-signal waveform from the selected run’s retained, indexed BLOW5 file;
- reusable signal-to-read and signal-to-reference mappings;
- read, reference, and pileup views within configured limits, rendered by a pinned Squigualiser version without network access; and
- saved viewer state that reopens the same read, locus, mapping, and render job.

Acquired signal remains authoritative only when it resolves through the exact
run, generation, raw representation, retained partition, and index. Derived
plots and simulated artifacts never satisfy an instrument raw-signal lookup.
Historical metadata with missing device/profile evidence remains
`legacy_unknown`; it is not silently relabeled compatible.

The source tree contains the gated Squigulator ideal-reference comparison lane
and manual trace-review ledger. Squigulator output is explicitly
model-derived—not acquired signal—and uses a runtime identity separate from the
Squigualiser comparison renderer. This lane must not be described as a live
operator capability until both OCI identities, bounded execution, visible
synchronized tracks, and artifact/review behavior pass live acceptance in the
Development service.

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
- robot-owned operator catalog, admission, invocation, and durable receipts
- canonical activation and recovery through `meta.activate_motion` and
  `meta.recover_motion_non_homing`
- physical aggregate emergency stop through the robot operator plane
- deterministic offline protocol validation
- persistent local jobs with append-only transition events

Current operational caveats:

- BMS is a typed presentation and connection layer, not an arbitrary robot proxy
  or a second BioXP command authority.
- Production BioXP linkage is intentionally disabled; current work uses the
  Development `test` environment and selected robot runtime only.
- Stale or unknown connection/ownership generations never authorize a mutation.
  The robot decides action readiness and admission.
- Offline protocol validation does not claim robot compatibility or
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

There is no general robot-host shell or reboot interface. The separately configured [service-only recovery](bioxp-service-restart.md) concerns one named service; it is not a robot reset or proof of hardware readiness.

## Workstation / System Operations

BMS also exposes workstation-facing support surfaces outside the structure
workflows:

- `/`
  Dashboard telemetry; `/infra` redirects here
- `start_ui.sh`
  API/frontend launcher
- `biomodstack_panel.py`
  GTK control panel for local service operation

## What These Docs Deliberately Avoid

These docs describe source interfaces, not proof of an active installation. They do not treat every robotics or
sequencing idea note in `docs/` as current support. If a behavior matters for a
run or operator flow, confirm it in the active frontend route, API router, or
workflow entrypoint referenced above.
