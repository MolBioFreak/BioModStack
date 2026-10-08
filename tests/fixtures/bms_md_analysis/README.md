# MDAnalysis runtime-format fixtures

These are bounded infrastructure fixtures, not scientific-validation datasets.
Both lanes derive from the `md_1u19` data distributed in nglview 3.1.4.
The retained upstream-source identities used to generate them were:

- `md_1u19.gro`: `8eec93cb0b45c43abc50ec40e49f983e2c2484671077777db19bf7340c068c76`
- `md_1u19.xtc`: `8279bc6723a4b2e70d6ef3e83fbbcaeb3f981d80546ef4195b7b1d7ba5815af1`
- `md_1u19.pdb`: `e449c380b9eb7921910922eaa97495a1a300a4e1d4a5cdaecedba7a1843740d0`
- nglview package release/build: `nglview-3.1.4-pyh620948e_0`
- canonical conda-forge archive: `https://conda.anaconda.org/conda-forge/noarch/nglview-3.1.4-pyh620948e_0.conda`
- archive SHA-256: `09c19df1aba52965ca7fced368b0da4b57a234fb3be338cc5669cf28a0b32ea0`

Derived-file mapping:

- `gromacs_1u19_format_smoke/system.gro` is a byte-preserving copy of `md_1u19.gro`.
- `gromacs_1u19_format_smoke/trajectory.xtc` contains source XTC frames 0, 10, 20, 30, 40, and 50 written by MDAnalysis 2.9.0.
- `pdb_dcd_1u19_format_smoke/system.pdb` is `md_1u19.pdb` with non-semantic line-end padding removed so repository whitespace checks remain strict.
- `pdb_dcd_1u19_format_smoke/trajectory.dcd` contains the same six source frames written by MDAnalysis 2.9.0.
- Each `atom_order_manifest.json` is produced by `scripts.bms_md.contract.write_atom_order_manifest` from its lane topology.
- Each lane `manifest.json` is then checksum-bound to those derived files; `fixtures.json` binds both lane manifests and every retained artifact.

Reproduction command shape inside the pinned analysis runtime:

```text
MDAnalysis.Universe(md_1u19.gro, md_1u19.xtc)
Writer(output.xtc|output.dcd, universe.atoms.n_atoms)
for source_frame in [0, 10, 20, 30, 40, 50]:
    universe.trajectory[source_frame]
    writer.write(universe.atoms)
```

Frames 0, 10, 20, 30, 40, and 50 were rewritten with the candidate
MDAnalysis 2.9.0 runtime. The GRO+XTC lane qualifies GROMACS-format opening;
the PDB+DCD lane qualifies the format pair emitted by BioModStack OpenMM runs.
The DCD is deterministic format-conversion evidence, not evidence that an
OpenMM production simulation ran. Every source and derived runtime file is
checksum-bound by `fixtures.json`.

The package license shipped with the nglview distribution is retained as
`SOURCE_LICENSE.txt`. These fixtures do not establish scientific correctness,
equilibration, sampling adequacy, convergence, or engine parity.
