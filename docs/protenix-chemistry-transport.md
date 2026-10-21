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
Native template search inputs use its expected `pairing.a3m`/`non_pairing.a3m`
filenames in task-owned directories. Template-only requests get a query-only
search input without enabling MSA inference. Explicit per-chain hit files remain
unchanged; an mmCIF database directory is never injected as a `templatesPath`.

Exact target anchoring uses the selected source PDB/model, not a downloaded or
name-matched replacement. Its manifest binds native template IDs, source chain
sequences and source bytes. Only selected target copies receive exact hits;
shared entities are split in preserved copy order and native bond/restraint
endpoints are rebased. In anchor-only mode, untemplated binder chains receive
an empty native hit file so anchoring does not silently trigger binder searches.
When generic templates are also explicitly requested, binder search remains
enabled and reads the shared corpus independently of the private source root.
Explicit source templates are not filtered by a searched PDB corpus's
publication-date/duplicate criteria, while native alignment and length checks
remain in force. An unresolved source association is reported, not silently
inferred from equal sequences or converted into a new launch gate.

`complex_json_path` denotes a compiled BMS object containing `components`, not a
raw native task-list API. Internal generated callers use the separate native
payload adapter. PLR validator input preserves candidate chain IDs, recognized
polymer modifications, explicit CCD/ion context and inter-residue bonds. Antibody
batch compact epitope selectors (`X42A`) use author residue/insertion identity;
the legacy colon form (`X:2`) retains its native sequence-position meaning.
Both forms rebase source chain IDs through the external-target alias map.

## Qualification and limits

Focused tests cover HTTP/SQLite create/readback/clone/resubmit, compiler
materialization, portable references, mounted UI controls and source-only
runtime delivery. Installed CPU parser checks must additionally inspect actual
modified atoms, interentity bonds, leaving groups and copy identities. JSON-key
presence alone is not chemical validation.

This transport does not implement unsupported native glycans or structural
constraint fields, certify arbitrary CCD stereochemistry, or automatically
expand binder-role correspondence for modified/atom-token entities. Native
result confidence axes must retain their actual token identities. The installed
native parser normalizes MSE to MET; retaining `CCD_MSE` in input does not prove
selenium retention. Explicit cyclic bonds survive the atom graph, but native
adjacent-polymer token-bond features do not establish their inference effect.
Generic template search requires both the mmCIF corpus and the pinned seqres
FASTA; exact-only source anchoring does not require that corpus.

Antibody spawned children retain the parent Protenix settings and epitope
selectors through fresh/retry creation. Both existing `protenix` and
`protenix_v2` selector spellings choose Protenix in native workflow and selected
dependency metadata; child validation uses the existing structure-prediction
result contract rather than the parent's aggregate closeout contract.

Previously completed direct runs are recovered through the separately supported
`protenix_native` external-result importer. Importing them does not manufacture
scheduler execution history, rewrite historical settings, or rerun inference.
