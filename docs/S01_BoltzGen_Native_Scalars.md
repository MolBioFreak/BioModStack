# BoltzGen native scalar publication (bounded, activation OFF)

## Source authority

Static source was read from `/mnt/BioModStack/apptainer/boltzgen.sif`
(SquashFS offset `53248`). The image was not executed, mounted, changed, built,
or tested. No model, weights, hardware, service, or deployment was used.

- Image Git reference: `617e549edf70787d899f47bc39e3746d8f10ffff`.
  This is a **reference**, not proof of Git working-tree cleanliness.
- Actual CLI: `/opt/venv/bin/boltzgen`, shebang `/opt/venv/bin/python3`, imports
  `boltzgen.cli.boltzgen.main`.
- Actual ordinary install: `/opt/venv/lib/python3.11/site-packages/boltzgen`.
  Installed METADATA reports `0.2.0`; direct_url.json reports `file:///app/boltzgen`.
- All 114 installed Python files were read and SHA-256 hashed. Every one matches
  the corresponding actual `/app/boltzgen/src/boltzgen` working file byte-for-byte.
  This does **not** claim equality with pristine upstream Git objects.
- The supported source contract additionally hashes CLI, METADATA and direct_url:
  `scripts/lib/boltzgen_native_source.json` (117 individual file hashes).
- Combined canonical file-map SHA-256:
  `bbaf9e6eb607e1da30e6d5efe37b0977ced13cff40223f92b6e3cc0333144b6e`.
- `tests/fixtures/boltzgen_617e549/installed_source.zip` contains those exact source
  bytes for offline static-observation tests, not a model or an executable test
  runtime. The adjacent upstream LICENSE applies to these source files.

The wrapper reads the actual supported CLI/install before and after its existing
invocation. It obtains the version from observed METADATA and calculates the
source identity from bytes, rather than copying an expected version into output.
Different/missing source, alternate CLI location, unknown PYTHONPATH, reuse,
preexisting outputs, failed invocation, or unknown passthrough args cannot produce
an `ok` canonical producer identity. The contract attests installed **source**,
not checkpoint identity, successful scientific inference, or deployed state.

## Bounded scalar semantics

Source references below are relative to the installed `boltzgen/` directory;
exact hashes are in the supported contract.

| Canonical key | Native source and meaning | Canonical handling |
|---|---|---|
| `design_ptm` | `model/layers/confidence_utils.py:208–265`: chain-design token mask, predicted TM expected value and maximum; `task/analyze/analyze_utils.py:97–106,164–180`: best refold sample chosen by native confidence | fraction, higher is better, native design-chain tokens; CSV native selected sample, NPZ only scalar/single-sample value |
| `affinity_probability` | `model/models/boltz.py:783–821`: sigmoid of first affinity head, emitted as `affinity_probability_binary1`; `task/analyze/analyze.py:1129` copies native affinity scalars | fraction, higher is better, first native affinity head for complex; no ensemble substitution or pLDDT alias |
| `filter_rmsd` | `task/filter/filter.py:364–369`: `bb_rmsd` after inverse folding, otherwise `rmsd`; `task/analyze/analyze_utils.py:119–141` calls coordinate alignment; native coordinates originate from PDB/mmCIF Cartesian coordinates (angstrom) | angstrom, lower is better; backbone/all-atom scope separated using wrapper CLI's observed invocation setting; no unknown-scope numeric publication |

`cli/boltzgen.py:1164` establishes `from_inverse_folded = not skip_inverse_folding`.
Unknown passthrough configuration makes producer evidence unavailable, rather
than guessing this setting. NPZ does not supply native Filter's `filter_rmsd`;
missing values stay unavailable. Multi-sample NPZ values are invalid for this
bounded scalar reader, never averaged or silently selected. NaN/Inf, booleans,
non-scalars and values outside the native domain are invalid. Zero remains zero.
No pLDDT/pTM interchange, new score, ranking policy, or inference setting is added.

## Publication and reader chain

1. Existing marked wrapper captures observed source around the native invocation.
2. Existing NPZ/CSV metadata extraction retains the exact original native bytes
   and a source/candidate binding. Metadata scalars do not become canonical truth.
3. RunBoltzGen's existing metadata channel carries retained NPZ/CSV as well as
   JSON. Marked FilterBoltzGen uses task-local copy staging, checks the retained
   native digest/candidate binding, and publishes native + structure + metrics.
4. The existing publication adapter checks complete filter dispositions, manifest,
   selected identity set, and declared artifact bytes before Design creation.
5. `Design.confidence_metrics.core_protein_scientific.metrics` contains compact
   records with state/reason, unit, scope, direction, observed producer identity,
   derivation version, native artifact SHA-256, Design UUID and document `primary`.
6. `await verified_boltzgen_design(session, design)` in the same owner returns
   `{'block': ..., 'artifacts': ...}`. It reloads the marked owning Job, requires
   its persisted publication receipt, rebuilds publication from current bytes,
   validates the entire persisted selected set, reconstructs every row's scalar
   block, checks supplied candidate identity, and rehashes before returning.
   Blocking filesystem work is offloaded from the async event loop.

Missing native/producer evidence gives unavailable records, never legacy numeric
fallback. When the native source is absent, the unavailable record is bound to
its actual published metadata artifact. Unmarked legacy ingestion remains owned
by existing dispatch. No admission flags, parent callers, deployment
configuration, or scoring policy changed in this scalar integration.
The integrated candidate dispatches marked `boltzgen`/`boltzgen_child` reads through
this verifier in `scientific_analytics.persisted_projection`. Deferred ORM fields
are loaded explicitly before source/identity verification. Invalid publication
returns null-valued invalid states and no source claim; it never falls back to
legacy floats. The existing typed parser and mounted `AnalyticsDashboard` consume
all three native keys, server-owned cohorts and complete-case pairs without unit
conversion, new scientific roles, or a separate viewer. API-generated software
fixtures cover native NPZ/CSV zero, missing, invalid, unknown producer and changed
source bytes; mounted dashboard tests consume those exact serialized responses.

## Evidence boundary

Tests exercise real source observation over an offline audited-source fixture,
real wrapper postprocessing/filter publication, SQLite reload and canonical
reader. The native model call is replaced only by source-shaped software fixture
outputs. A separate literal Nextflow FilterBoltzGen test exercises copy staging
and native-byte publication without running an image. These tests prove software
publication/reader integrity, not model execution or scientific acceptance.
