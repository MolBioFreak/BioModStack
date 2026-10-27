# bms-alignment-core — partial implementation

Standalone Python primitives and native adapter work for MAFFT, FAMSA, MUSCLE and TWILIGHT. This is **not a completed first-pass integration** and must not be advertised as full engine or parameter coverage. No BMS database, API, frontend, or runtime image is included.

## Local checks

```sh
cd packages/bms-alignment-core
PYTHONPATH=. python -m pytest -q -rs
# For actual native evidence set BMS_TEST_MAFFT to the executable and, if
# required by that installation, MAFFT_BINARIES to its helper directory.
```

`bms-alignment schema`, `validate`, `run`, `inspect`, `compare`, `export`, `bundle create`, and `bundle verify` accept JSON matching `AlignmentRequest` or `SequenceSet` as appropriate. `run` executes a real process with separate input, output, log and receipt. Native failures preserve files and return the native status. The receipt records the executable basename and relative input name, not local argv paths; it is not yet a complete portable scientific manifest.

FASTA import retains duplicate headers with generated occurrence-local row IDs. Stockholm and Clustal are basic block parsers; original Stockholm text is retained, but structured annotations are not implemented. A3M retains original text; `formats.project_a3m` separates lowercase/dot insertion slots from match columns, without constructing a rectangular alignment. Conversion is unavailable. Exports disclose Stockholm metadata loss and replacement of headers by row IDs for block formats. Source identity defaults to unknown. Comparison requires explicitly declared member/occurrence IDs plus `source_positions` (one zero-based source position or null per raw row column) and nonempty `transformation_provenance` for each row; `source_offset` alone never establishes correspondence. Maps and provenance are producer declarations, not independently verified. Duplicate source positions, invalid map length and missing entries remain unmapped; partial matches are reported, but only-position claims are suppressed when the opposite row is incomplete. Ambiguous identities and unknown mappings are observations, not execution gates. Comparison does not calculate cross-row alignment agreement or accuracy. A3M comparison remains unmapped; its reported unmapped indices are raw row offsets, not match columns. Summary counts ambiguity literally and each occurrence equally.

`bundle create SOURCE DEST FILE... --exclude DESCRIPTION` copies caller-selected files with relative paths, hashes, and caller-declared exclusions. It does not discover dependencies or enforce their completeness; `bundle verify DEST` verifies listed bytes only. Bundles are not yet full BMS scientific contracts.

TWILIGHT accepts supplied trees with explicit leaf mapping or MAFFT tree preparation; raw and submitted trees and mapping observations are retained. The mapping tokenizer is not a complete validating Newick parser. Neither TWILIGHT mode nor MAFFT 7.525 interoperability has been qualified. Use a fresh work directory for every run; reusing a directory may mix prior output files. MUSCLE 5.3 source has a special case in which stratified `replicates=1` produces one member rather than four; do not infer member counts from the help text. Ensemble files retain the native permutation/seed in their filenames.

## HTTP client (partial)

`bms_alignment.http.AlignmentClient` exposes typed model discovery, preview, Job submission and Job readback using the existing BMS routes. `JobSubmission.params` uses the same `AlignmentRequest` as the standalone package; explicitly supplied false, zero and null values are retained. Supply authentication headers to the Python client where required, and use HTTPS outside a trusted local connection. Redirects are not followed. Project launch context, when supplied in the request, is also sent in the existing launch-context header. Server authorization and admission remain authoritative.

The CLI has `bms-alignment api --url URL model MODEL`, `preview REQUEST.json`, `submit REQUEST.json`, `job JOB_ID`, and `export-project PROJECT RECEIPT DIRECTORY [--shared-state ID ...]`. The SDK method `export_project_bundle` also accepts authentication headers. Export copies all artifacts in the authorized Project receipt, plus only explicitly selected shared-state revisions, with a verified inventory; `bundle inspect-project DIRECTORY` verifies and displays it offline. The CLI has no credential configuration. Project bundle import, automatic annotation discovery and restoration/remapping are not implemented. This export is not a complete AC14 round trip.

The optional `tests/test_http_sdk_integration.py` runs with API dependencies and paths available; it uses real BMS ASGI model/Job routers and a scratch SQLite store, with an in-process HTTP adapter, not a listening server or native alignment run.

## Required remaining work

Complete machine-readable inventories and all native settings; native TWILIGHT and exact pinned MAFFT guide qualification; independent tree parsing, full transformation and ensemble identities; robust formats/A3M projection and raw bytes; source transformation maps and full correspondence metrics; typed full contracts and HTTP SDK; cancellable process ownership; full portable bundle manifests and relocation; package wheel clean-install acceptance; API/PM/Job/UI integration. More exhaustive verification and security review are needed before use as an integration authority. The adapter currently does not enforce molecule applicability or unsupported MUSCLE option combinations; native errors are retained.

Native runs create a fresh `attempt-*` directory under the supplied work directory on every invocation. The receipt's `attempt_dir` identifies the directory containing its relative artifact names; MAFFT guide preparation has its own nested attempt. Callers must use that location rather than expecting outputs directly in the parent work directory. These local paths are execution locations, not portable artifact identities.
