# bms-alignment-core — partial implementation

Standalone Python primitives and a MAFFT runner. This is **not a completed first-pass integration** and must not be advertised as full engine or parameter coverage. No BMS database, API, frontend, or runtime image is included.

## Local checks

```sh
cd packages/bms-alignment-core
PYTHONPATH=. python -m pytest -q -rs
# For actual native evidence set BMS_TEST_MAFFT to the executable and, if
# required by that installation, MAFFT_BINARIES to its helper directory.
```

`bms-alignment schema`, `validate`, `run`, `inspect`, `compare`, `export`, `bundle create`, and `bundle verify` accept JSON matching `AlignmentRequest` or `SequenceSet` as appropriate. `run` executes a real process with separate input, output, log and receipt. Native failures preserve files and return the native status. The receipt records the executable basename and relative input name, not local argv paths; it is not yet a complete portable scientific manifest.

FASTA import retains duplicate headers with generated occurrence-local row IDs. Stockholm and Clustal are basic block parsers; original Stockholm text is retained, but structured annotations are not implemented. A3M retains original text; `formats.project_a3m` separates lowercase/dot insertion slots from match columns, without constructing a rectangular alignment. Conversion is unavailable. Exports disclose Stockholm metadata loss and replacement of headers by row IDs for block formats. Source identity defaults to unknown; comparison uses only explicitly declared member/occurrence IDs and contiguous forward offsets, reports ambiguous identities and unknown mappings without refusing execution, and does not calculate cross-row alignment agreement or accuracy. A3M comparison remains unmapped; its reported unmapped indices are raw row offsets, not match columns. Summary counts ambiguity literally and each occurrence equally.

`bundle create SOURCE DEST FILE... --exclude DESCRIPTION` copies caller-selected files with relative paths, hashes, and caller-declared exclusions. It does not discover dependencies or enforce their completeness; `bundle verify DEST` verifies listed bytes only. Bundles are not yet full BMS scientific contracts.

`tree.map_mafft_tree` maps exact numbered MAFFT leaf labels with explicit missing/unmapped/duplicate observations. It is not a complete validating Newick parser, is not wired into TWILIGHT, and is not evidence of MAFFT 7.525 interoperability.

## Required remaining work

Complete machine-readable inventories and all native settings; FAMSA, MUSCLE, TWILIGHT and their native qualification; real reversible supplied/generated guide tree workflows; robust formats/A3M projection and raw bytes; source transformation maps and full correspondence metrics; typed full contracts and HTTP SDK; cancellable process ownership; full portable bundle manifests and relocation; package wheel clean-install acceptance; API/PM/Job/UI integration. More exhaustive verification and security review are needed before use as an integration authority.
