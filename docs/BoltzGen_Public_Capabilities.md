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
| `backbone_docking` (**compatibility only**) | `protein-small_molecule` | Historical file backbone plus ligand/NTP, retained without rewriting its mask, protocol, or requested input. **Not offered as a fresh public operation:** zero-design analysis fails at this pin. Explicitly reopened requests remain editable and transportable; the label is not a claim of docking or complex-sampling support. |

Targets may retain DNA/RNA/ligands via native file entities and exact chain selection. Native input trees are preserved rather than rewritten into sequence-only or antibody approximations. No new readiness/admission gate was added. Native pLDDT is unavailable in the known CSV/NPZ result path; neither the typed contract nor historical catalog now injects `min_plddt=70`.

## Concrete limits and unresolved native behavior

1. **Ligand-only PDB/mmCIF:** the unmodified pin fails before non-polymer parsing (`IndexError` for an empty polymer loop and `RuntimeError` when the category is absent). The [native parser repair](BoltzGen_Ligand_Parser_Repair.md) handles these empty/absent polymer rows while preserving real ligand coordinates and component chemistry, without dummy protein context. A source-only localimage recipe and CPU regression cover ligand PDB/mmCIF, schema masks, features, writer/reparse and unchanged polymer/mixed controls. Original failed evidence remains retained. The repaired candidate is separate from the installed image; no publication or activation is implied.
2. **Legacy zero-design path is not a complete native operation:** `tests/qualify_boltzgen_zero_design.py` constructs the actual installed `protein-small_molecule` pipeline with device discovery and checkpoint path resolution stubbed (no loading). It still schedules design, inverse folding, folding, design folding, affinity, analysis and filtering. Executing the complete installed inverse-fold `sample` method on synthetic CPU zero-mask features skips every decoder iteration and retains the sequence. Executing the complete installed `compute_metrics` method on zero-mask features raises `IndexError: index 0 is out of bounds for dimension 0 with size 0` at the first designed-chain lookup (`analyze.py:541-545`). The generated-data design-folding branch also extracts only design-chain residues; a zero mask selects none. There is no native zero-design docking branch. This proves a deterministic downstream failure, not diffusion/folding success or molecular accuracy. Skipping inverse folding cannot repair the mandatory analysis. Fresh model lists/mode lists and the binder chooser omit the legacy ID; full model detail and direct settings retain it with `compatibility_only=true` for historical readback/editing. No launch refusal, forced design mask, altered protocol, or silent reduced-stage substitute was added. New protein/ligand design uses the documented explicit designed protein/file ranges under the existing modes.

   Reproducible CPU evidence: `integration/closeout/boltzgen-native-modes/zero-design-cpu-third.log`, alongside exact installed sources in `native-control-flow-sources.json`. The script hashes the installed methods it executes; synthetic feature fixtures are explicitly not inference output.
3. This is scoped public-mode reconciliation, not a claim of exhaustive editor parity for every nested native YAML field or every upstream CLI option. No inference, affinity/quality validation, experimental activity validation, live launch, image installation or publication occurred.

## Qualification and evidence

### Zero-design mode alignment

Evidence: `integration/closeout/boltzgen-native-modes/` under the same task-evidence root.

- Installed CPU method/pipeline probe: `zero-design-cpu-third.log` (expected zero-mask analysis failure reproduced, unchanged inverse-fold sequence).
- Fresh model/project discovery and retained normalization: `discovery-final.log`, **3 passed**.
- Legacy saved-template create/reopen/update: `legacy-template-final.log`, **1 passed**.
- Input materialization: `prep.log`, **9 passed**; zero-mask file/ligand semantics unchanged.
- Mounted JobSubmission and binder chooser: `ui-final.log`, **8 passed, 19 skipped**; reopened legacy requests remain submit-able and disappear from fresh selection after choosing another mode.
- TypeScript build passed; isolated Vite artifact build passed (`build.log`). No deployment.

The earlier combined API run passed 10 tests (`api-third.log`). Later combined/template runs intermittently timed out in the existing async SQLite fixture; those logs remain, and the final discovery and legacy-template subsets passed independently. No fixture or production lifecycle behavior was weakened. Historical evidence below predates compatibility-only classification and uses “eight modes” to count accepted request IDs, not fresh public capabilities.

### Earlier input transport qualification

Evidence root:
`/home/dalab/.local/state/biomodstack/task-evidence/structure-workflow-fidelity-repair/integration/closeout/boltzgen-capabilities/`

- `native-parser-fifth.log`: all eight mode schemas parsed by the actual installed native parser and all eight corresponding native CLI argv parsed. Synthetic RDKit ALA/GLY and ethanol molecular fixtures, real ATP SMILES and Mg2+ parsing; no checkpoints or GPU. Includes cyclic peptide, explicit antibody design mask, ligand file chain inclusion and backbone zero-design mask.
- `final-api.log`: **36 passed**, typed defaults/discovery/alias/idempotence and real compiler native settings payloads for all eight modes. Compiler source identity is an explicit unit-test fixture, not a runtime provenance claim.
- `pytest-api-sixth.log`: **45 passed**, including request compatibility/admission rank preservation (overlaps the final API suite).
- `pytest-launch-seventh.log`: **8 passed**, saved template create/reopen/update and normalization/discovery across all modes using isolated fixture storage; the separate replay test was deselected.
- `final-prep.log`: **28 passed, 1 deselected**, entity materialization and real offline Nextflow preparation plus explicit non-science zero-yield workflow fixture. No inference substituted.
- `ui-fifth.log`: **4 passed, 6 skipped**, mounted actual JobSubmission for the four formerly unrouted modes, every discovered typed control present, native default protocol and null pLDDT retained. No protein-only blind-prediction round silently added to these modes.

All failed logs remain. Initial attempts exposed and fixed the staged Nextflow sentinel basename issue, missing UI inventory eligibility and the incorrect inherited blind-round control. Other retained failures: combined API/root tests hit the API subprocess restriction; the initial broad focused runtime check had an existing symlink-preview expectation mismatch (66 passed, 1 failed); the source-containment test has an existing symlink-versus-outside-root error-message mismatch and was explicitly deselected in the final prep run. Neither security policy was changed to satisfy tests. One combined API/template attempt timed out; the exact template subset subsequently passed. Native fixture attempts also preserve the ligand-only parser failure and UNL filtering behavior before the supported EOH/context case passed.
