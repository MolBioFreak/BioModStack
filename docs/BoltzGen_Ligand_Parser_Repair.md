# BoltzGen ligand-only native parser repair

The native `617e549edf70787d899f47bc39e3746d8f10ffff` parser reads the
first `_entity_poly_seq` row unconditionally. PDB conversion creates an empty
polymer loop for a ligand-only structure; serialized mmCIF omits that category.
The original image raises `IndexError` and `RuntimeError` respectively before
reaching its otherwise usable non-polymer parser.

`apptainer/patches/boltzgen/ligand-only-mmcif.patch` leaves the native polymer
sequence loop intact when rows exist and uses an empty sequence mapping when
no polymer rows exist. It does not invent protein context, atom coordinates,
chemical component definitions, bonds, or inference behavior. A malformed
polymer still reaches its existing missing-sequence lookup. The observational
source-correspondence exporter does not apply this scientific repair.

## Reproducible candidate

From the repository root:

```sh
apptainer build --fakeroot --mksquashfs-args '-processors 4 -mem 2G' \
  --build-arg base_image=/absolute/path/to/base.sif \
  /absolute/path/to/boltzgen-ligand-parser.sif apptainer/boltzgen-ligand-parser.def
```

Use a task-owned `TMPDIR`, `APPTAINER_TMPDIR` and `APPTAINER_CACHEDIR`.
The qualified base image SHA-256 is
`a62b63635a53f18f3bbb66585817bdd0425f58799696b2a1c19124bd6023a672`.
The recipe changes the repository and installed copies of `mmcif.py`, with
exact build-time source checks. It does not resolve or reinstall dependencies.
These build checks are not application admission rules.

The repaired source SHA-256 is
`9ca07e896d533914e9b462b21a8caf705e4f25977eb99aa1ae7c129a38131633`.
Native result identity and correspondence observation recognize both the original
and repaired exact source inventories. Historical results remain readable and
unsupported images continue to execute as before, without invented provenance.

## CPU qualification

```sh
BMS_TEST_BOLTZGEN_BASE_IMAGE=/absolute/path/to/base.sif \
BMS_TEST_BOLTZGEN_IMAGE=/absolute/path/to/boltzgen-ligand-parser.sif \
  python -m pytest tests/test_boltzgen_ligand_parser.py \
  tests/test_boltzgen_source_correspondence.py -q -o addopts=
```

The regression preserves the original failures and checks ligand-only PDB and
mmCIF atoms, bond count and coordinates against the synthetic ethanol source.
It exercises ordinary scaffold preparation, native schema masks, tokenization,
featurization and native writer/reparse with inert input-coordinate predictions.
Polymer and mixed-input structure arrays, design masks and emitted CIF bytes
are compared with the original image. The existing observer regression checks
native writer byte parity and source mapping. No weights, GPU, inference,
affinity/accuracy claims, publication, installation or service restart are part
of this qualification.

The completed local candidate is retained at
`~/.local/state/biomodstack/task-evidence/structure-workflow-fidelity-repair/integration/closeout/boltzgen-ligand-parser/boltzgen-ligand-parser.sif`.
Its SHA-256 is
`e4c9cfe3ca127de46de50a1a5e1533ee341231229e0f956f4bd45c671402712f`
and its size is 10,443,853,824 bytes. Build and inspection completed successfully.
The candidate passed 12 combined parser/correspondence/authoring-position tests,
the real native fixture's API publication/readback check, eight request-schema
and CLI parses (seven fresh variants plus the retained historical request), and
the zero-design diagnostic branch probe. The latter confirms the unsupported
analysis branch; it does not establish docking support. All 117 installed package
versions are unchanged; only the intended native parser source digest changed.

Exact commands, results, retained failures, inventories and archived native fixtures
are beside the candidate in `candidate-verification.json`, command receipts, logs
and `native-fixtures.tar.gz`. The installed image remains the original base above.
