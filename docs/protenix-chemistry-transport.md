# Protenix chemistry in ordinary prediction requests

The existing Protenix `predict`/`complex` modes expose the versioned
`protenix_chemistry` schema through their model discovery `parameter_schema`.
The same nested scientific contract is checked by Job creation, registry
admission (including new retry requests), and native conversion. This is not a
new execution path.

## Molecular fields

- `complex_components` remains the ordered BMS entity roster. Its types are
  `protein`, `peptide`, `dna`, `rna`, `ligand`, and `ion`.
- Protein/peptide modifications use native one-based `ptmPosition` and
  `ptmType` (for example `CCD_AIB`). DNA/RNA modifications instead use
  `basePosition` and `modificationType`.
- `count` is a positive integer. Optional `copy_ids` gives one unique native
  chain ID per copy. A scalar BMS `id` remains a display/reference identity;
  multicopy entities without `copy_ids` use the native automatic chain IDs.
- Ligands select exactly one of `ccd`, `smiles`, or `ligand_file`.
  `ligand_file` is a managed file selector, such as `inputs/ligand.sdf`, not an
  absolute execution path. The compiler stages its original bytes through the
  existing generated-input and portable-reference owners.
- Task-level `covalent_bonds` uses native entity/copy/residue/atom endpoints.
  Entity ordinals are one-based positions in the ordered component roster, not
  BMS display IDs. Native `left_*`/`right_*` and existing `entity1`/`entity2`
  aliases are supported without rewriting the saved request.
- Singular `constraint` preserves supported native `contact` and `pocket`
  inputs. Unsupported native `constraint.structure` is not advertised.
- Protein inline/path paired/unpaired MSA fields, legacy precomputed-MSA
  directories, RNA unpaired MSA fields, and protein `templatesPath` survive
  compilation. `templatesPath` denotes native template-hit input, not an
  arbitrary coordinate directory. `protenix_use_template` remains explicit and
  separate from target-geometry conditioning.

Unknown component/chemistry fields are errors, not silently discarded extras.
Only known molecular fields and explicit `name`/`id` metadata belong in the
component contract. This does not change unrelated models' parameter handling.

## Editor and lifecycle

The structure-prediction editor retains full scientific state while labels are
changed and requests are saved, reopened or cloned. Counted Protenix entities
are not expanded or capped at twelve. Annotated single chains remain molecular
component requests. Sequence edits invalidate alignment fields without deleting
chemical annotations; invalid positions are reported rather than repaired.

Stable editor identities keep bonds and constraints attached to their original
entities. Removing an unreferenced preceding entity remaps ordinals. Removing a
referenced entity leaves a visible unresolved endpoint until the operator fixes
or removes that record; adding a new entity cannot silently redirect the bond.

Both single and batch compiler writers retain task chemistry. The shared
converter maps native fields explicitly; the already-native payload branch
remains a deep copy. MSA/template preparation does not reconstruct chemistry.

The pinned native search detector only recognizes file paths, while its reader
supports inline MSAs. The existing inference wrapper materializes task-owned
A3M files alongside retained inline values before native preprocessing. Empty
inline values remain explicit query-only input; no provider search is invented.

## Qualification and limits

Focused tests cover HTTP/SQLite create/readback/clone/resubmit, compiler
materialization, portable references, mounted UI controls and source-only
runtime delivery. Installed CPU parser checks must additionally inspect actual
modified atoms, interentity bonds, leaving groups and copy identities. JSON-key
presence alone is not chemical validation.

This transport does not implement unsupported native glycans or structural
constraint fields, certify arbitrary CCD stereochemistry, or automatically
expand binder-role correspondence for modified/atom-token entities. Native
result confidence axes must retain their actual token identities.

Previously completed direct runs are recovered through the separately supported
`protenix_native` external-result importer. Importing them does not manufacture
scheduler execution history, rewrite historical settings, or rerun inference.
