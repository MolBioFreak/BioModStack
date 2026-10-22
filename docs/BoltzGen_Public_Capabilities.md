# BoltzGen public capability mapping (pinned runtime)

## Authorities

Installed `/mnt/BioModStack/apptainer/boltzgen.sif`, `/app/boltzgen` Git revision:
`617e549edf70787d899f47bc39e3746d8f10ffff` (clean checkout).
Actual parser/CLI inspected under `/opt/venv/lib/python3.11/site-packages/boltzgen`.

- [Pinned official README / protocols](https://github.com/HannesStark/boltzgen/blob/617e549edf70787d899f47bc39e3746d8f10ffff/README.md#protocols)
- [Pinned official specification documentation](https://github.com/HannesStark/boltzgen/blob/617e549edf70787d899f47bc39e3746d8f10ffff/example/README.md)
- [Pinned native parser](https://github.com/HannesStark/boltzgen/blob/617e549edf70787d899f47bc39e3746d8f10ffff/src/boltzgen/data/parse/schema.py)
- [Pinned CLI/protocol implementation](https://github.com/HannesStark/boltzgen/blob/617e549edf70787d899f47bc39e3746d8f10ffff/src/boltzgen/cli/boltzgen.py)
- [Official small-molecule/file example](https://github.com/HannesStark/boltzgen/blob/617e549edf70787d899f47bc39e3746d8f10ffff/example/small_molecule_from_file_and_smiles/4g37.yaml)
- [Current official documentation](https://github.com/HannesStark/boltzgen#protocols), independently retrieved and retained as `official-main-README.md` in the evidence directory below.

Current main documentation additionally advertises `protein-redesign`, SolubleMPNN default inverse folding, and ESMFold2 scoring. These are **not the installed pin's protocol/defaults**: its `protocol_configs` contains protein-anything, peptide-anything, protein-small_molecule, nanobody-anything, antibody-anything. No unsupported protein-redesign protocol or newer scoring default has been invented or installed here. File-backed explicit design masks are supported by this pin and remain available independently of that newer protocol.

## Public modes

All modes use the existing typed `native_generation` schema, model discovery endpoint, shared human settings editor, normalizer, compiler and `workflows/boltzgen_generation.nf`. Explicit protocol overrides, including `auto`, remain operator-owned.

| Public ID | Default protocol | Native input meaning |
|---|---|---|
| `protein_binder` | `protein-anything` | Designed `protein` sequence/length or explicit file scaffold design ranges; independent target file context. |
| `peptide_binder` | `peptide-anything` | Protein entity, not a fictitious peptide entity. Cyclic flag maps to `protein.cyclic`, or `file.add_cyclization` for the selected scaffold chain. Existing covalent bonds remain available for disulfides/staples. |
| `nanobody_binder` | `nanobody-anything` | Existing VHH sequence/framework/scaffold input. Protocol alone does not establish a nanobody framework: absent a framework, the existing de novo length behavior remains. |
| `antibody_binder` | `antibody-anything` | Explicit native scaffold chain/design ranges or sequence template. No inferred CDR numbering, framework or mapping to VHH. Multichain specifications can use the retained native input tree. |
| `ligand_binder` | `protein-small_molecule` | Designed protein plus SMILES ligand; optional file-conditioned ligand/context. |
| `ntp_binder` | `protein-small_molecule` | Designed protein plus the requested existing NTP SMILES template. Optional `catalytic_site` adds only `[Mg+2]`, not coordination distances, catalytic geometry or polymerase activity. |
| `scaffold_around_ligand` | `protein-small_molecule` | Designed protein plus native **file** entity (not unsupported `ligand.path`). `ligand_chains` selects exact native chains; omitted means retain all source context. Coordinates condition the model, not a rigid-pose guarantee. |
| `backbone_docking` | `protein-small_molecule` | Retained legacy ID, visibly labeled **Backbone-conditioned Ligand Sampling**. Native file backbone plus requested ligand/NTP, which was previously omitted. Binding positions address the backbone when no separate target is supplied. **Not an official docking protocol or a qualified docking optimizer.** No design mask is fabricated. |

Targets may retain DNA/RNA/ligands via native file entities and exact chain selection. Native input trees are preserved rather than rewritten into sequence-only or antibody approximations. No new readiness/admission gate was added. Native pLDDT is unavailable in the known CSV/NPZ result path; neither the typed contract nor historical catalog now injects `min_plddt=70`.

## Concrete limits and unresolved native behavior

1. **Ligand-only PDB input at this pin:** native `mmcif_from_block` unconditionally indexes the first `_entity_poly_seq` row and raises `IndexError` for the tested ligand-only PDB. A real source containing polymer context with `ligand_chains` selecting the ligand succeeds. No dummy scientific context, coordinate loss, new refusal gate, or image patch was substituted. Supporting arbitrary ligand-only files requires a native parser repair/version decision; the failed attempt is retained.
2. **Legacy docking:** the schema parses backbone plus ATP with zero designed residues. This proves entity/coordinate/argv transport, not that the full design/inverse-fold/folding/selection pipeline is scientifically meaningful with a zero-design mask. The official pin has no docking protocol. Dedicated docking capability remains unproven and is not advertised as such.
3. This is scoped public-mode reconciliation, not a claim of exhaustive editor parity for every nested native YAML field or every upstream CLI option. No inference, affinity/quality validation, experimental activity validation, live launch, image installation or publication occurred.

## Qualification and evidence

Evidence root:
`/home/dalab/.local/state/biomodstack/task-evidence/structure-workflow-fidelity-repair/integration/closeout/boltzgen-capabilities/`

- `native-parser-fifth.log`: all eight mode schemas parsed by the actual installed native parser and all eight corresponding native CLI argv parsed. Synthetic RDKit ALA/GLY and ethanol molecular fixtures, real ATP SMILES and Mg2+ parsing; no checkpoints or GPU. Includes cyclic peptide, explicit antibody design mask, ligand file chain inclusion and backbone zero-design mask.
- `final-api.log`: **36 passed**, typed defaults/discovery/alias/idempotence and real compiler native settings payloads for all eight modes. Compiler source identity is an explicit unit-test fixture, not a runtime provenance claim.
- `pytest-api-sixth.log`: **45 passed**, including request compatibility/admission rank preservation (overlaps the final API suite).
- `pytest-launch-seventh.log`: **8 passed**, saved template create/reopen/update and normalization/discovery across all modes using isolated fixture storage; the separate replay test was deselected.
- `final-prep.log`: **28 passed, 1 deselected**, entity materialization and real offline Nextflow preparation plus explicit non-science zero-yield workflow fixture. No inference substituted.
- `ui-fifth.log`: **4 passed, 6 skipped**, mounted actual JobSubmission for the four formerly unrouted modes, every discovered typed control present, native default protocol and null pLDDT retained. No protein-only blind-prediction round silently added to these modes.

All failed logs remain. Initial attempts exposed and fixed the staged Nextflow sentinel basename issue, missing UI inventory eligibility and the incorrect inherited blind-round control. Other retained failures: combined API/root tests hit the API subprocess restriction; the initial broad focused runtime check had an existing symlink-preview expectation mismatch (66 passed, 1 failed); the source-containment test has an existing symlink-versus-outside-root error-message mismatch and was explicitly deselected in the final prep run. Neither security policy was changed to satisfy tests. One combined API/template attempt timed out; the exact template subset subsequently passed. Native fixture attempts also preserve the ligand-only parser failure and UNL filtering behavior before the supported EOH/context case passed.
