# Pinned FrustraMPNN capability inventory

**Inventory schema:** `frustrampnn_capability_inventory` version `1`

**Inventory content SHA-256:** `6fec27041d721478bf0c2c4822dbd7be6995c19c70f2e8e9a6bf6d4176179414`

## Pinned runtime identity

| Identity | Pinned value |
|---|---|
| Image | `$BMS_CONTAINER_DIR/frustrampnn.sif` |
| Verified image SHA-256 | `4dc310797f9dc2b2a0d48219e0c3f692188cbbf17e59b78bf4e0ec19dcbd52ea` |
| Executable | `/opt/venv/bin/frustrampnn` |
| Executable SHA-256 | `f56c38dac14b89a00f623297ab438cc4c4696994d143556c00a0959199da798c` |
| Installed CLI version | `frustrampnn, version 1.0.0` |
| Source commit | `1f74d44748c15cc751572b0b980edcc8b0bb26d2` |
| Checkpoint identity | `megascale.ckpt` |
| Checkpoint path | `/opt/frustrampnn_weights/megascale.ckpt` |
| Checkpoint SHA-256 | `eaee71adb7eec366fc672d2aadef87f2c51243042a4518cd897634784dc2da3b` |

The checkpoint digest is reused from the authoritative `FRUSTRAMPNN_RUNTIME_IDENTITY` in `platform/api/services/frustrampnn/runtime.py`; it is not inferred or newly invented here.

## Source evidence

The option surface was read directly from the pinned image with:

```text
apptainer exec $BMS_CONTAINER_DIR/frustrampnn.sif /opt/venv/bin/frustrampnn predict --help
```

The pinned help output reports:

```text
-p, --pdb FILE         Input PDB file  [required]
-c, --checkpoint FILE  Model checkpoint file (.ckpt)  [required]
-o, --output FILE      Output CSV file (default: frustration_predictions.csv)
--chains TEXT          Comma-separated chain IDs to analyze (default: all chains)
--positions TEXT       Comma-separated positions to analyze (0-indexed, default: all)
--device [cuda|cpu]    Device to use (default: auto-detect)
--config FILE          Config file for old-format checkpoints
-q, --quiet            Suppress progress bar
--help                 Show this message and exit.
```

The installed version was read with:

```text
apptainer exec $BMS_CONTAINER_DIR/frustrampnn.sif /opt/venv/bin/frustrampnn --version
```

No model inference was run; both evidence commands only requested CLI metadata.

## Exact option dispositions

These nine keys are every option exposed by the installed `predict` help and no other settings are claimed.

| Installed forms | Ownership class | Product control | API type / control kind | Default source | Validation evidence | Disposition |
|---|---|---|---|---|---|---|
| `-p`, `--pdb` | Workflow/source | Yes | `governed_artifact_reference` / governed source selector or upload | Owning workflow or governed upload; BMS snapshots and hashes the source instead of accepting a host path | Pinned SIF help plus runtime boundary | **Governed input.** Required structure input; BMS owns path resolution and snapshot identity. |
| `-c`, `--checkpoint` | System/runtime | No product control | Not applicable | Authoritative runtime registry | Pinned SIF help plus `runtime.py` identity | **System pinned.** Always use authenticated `megascale.ckpt`; operator checkpoint paths are excluded. |
| `-o`, `--output` | System/storage | No product control | Not applicable | Scheduler-owned job output root | Pinned SIF help | **Scheduler allocated.** Output placement is storage/security policy, not scientific control. |
| `--chains` | Scientific/operator | Yes | `array_of_stable_entity_or_chain_references` / entity or chain multi-selector | All protein entities unless a complete typed source-backed selection is supplied | Pinned SIF help: comma-separated IDs, all chains by default | **Operator exposed.** Selection changes which chains are scored. |
| `--positions` | Scientific/operator | Yes | `array_of_stable_residue_references` / residue multi-selector | All scoreable positions unless stable residue references are selected and resolved | Pinned SIF help: comma-separated, zero-based, all positions by default | **Operator exposed.** Selection changes which residues are scored; free-form position text is not a product control. |
| `--device` | Scheduler/runtime | No product control | Not applicable | Scheduler physical assignment compiled to task-visible CUDA device 0 | Pinned SIF help plus runtime command authority | **Scheduler assigned.** Host GPU IDs and CPU fallback are not operator controls. |
| `--config` | System/compatibility | No product control | Not applicable | Absent for the pinned MegaScale checkpoint | Pinned SIF help identifies this only for old-format checkpoints; runtime registry identifies MegaScale | **Not applicable to pinned checkpoint.** No config file is supplied. |
| `-q`, `--quiet` | System/diagnostics | No product control | Not applicable | Fixed runtime policy: omitted (`false`) | Pinned SIF help identifies progress-bar suppression only | **System fixed.** Diagnostic presentation does not alter scientific output. |
| `--help` | CLI/diagnostics | No product control | Not applicable | CLI documentation behavior only | Pinned SIF help | **Documentation only.** It prints help and exits; it is never part of inference execution. |

## Deterministic content binding

`content_sha256` uses the exact schema-declared semantics:

```text
sha256(rfc8785(document_without_top_level_content_sha256))
```

The preimage is the RFC 8785 canonical JSON byte sequence of the complete inventory after removing only the top-level `content_sha256` member. The stored digest field therefore has no self-reference ambiguity and can later be returned unchanged beside the inventory by an API.
