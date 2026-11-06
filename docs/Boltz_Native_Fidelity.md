# Boltz native molecular fidelity

Runtime authority: `boltz-community 2.9.5`, source revision
`7ebf1be087d4d61a02234c878402838bf3712d8b`, `boltz/data/parse/schema.py`.

## Existing BMS component vocabulary

`protein`, `peptide`, `dna`, `rna`, `ligand`, and `ion` remain BMS types.
Only native emission translates peptide to protein and ion to ligand. CCD
strings and CCD lists are passed through; HEM is not replaced with SMILES.
Protein/DNA/RNA `modifications: [{position: <1-based>, ccd: <code>}]` and
`cyclic` retain their native meaning. Parent sequence is never rewritten to
separate chemically distinct molecules. Top-level `constraints`, `templates`,
and `properties` retain native nesting. Affinity selection is explicit:
`properties: [{affinity: {binder: L}}]`; there is no first-ligand inference.
The installed native restrictions on selected affinity ligands remain intact.

An explicit ordered ID list identifies copies. `count` means total copies;
an undersized list is extended with unused spreadsheet-style alphabetic IDs,
reserving every explicit ID across the complete component roster first.
A smaller count never deletes authored IDs. The materialized YAML records the
resulting IDs, and constraints continue to address IDs, not component ordinals.
Native parsing may group identical chemistry into shared entities; downstream
axes must follow the native structured chain ledger, not infer an axis from the
editor's row positions. Compiler/UI integration must expose the actual roster.

`msa: empty` means single-sequence; explicit blank/None means native automatic
intent. Explicit `msa` takes precedence over legacy `msa_path` and short-peptide
policy. Supplied MSA applies to short peptides too. The provider/prepared owner
remains responsible for automatic alignment preparation. Immutable prepared
components are hydrated/validated once, before molecular serialization; an
empty prepared alignment is retained explicitly. No provider is invoked by the
CPU qualification.

## Native source adaptation

`scripts/run_boltz_wrapper.py` installs the small in-memory native adapter before
importing `boltz.main`. It changes the defective grouping key to include effective
last-write-wins modifications and cyclic topology, as the native grouping comment
intends. Identical chemistry still groups and retains native MSA consistency
checks. The same pinned parser calls nonexistent `AllChem.Descriptors.MolWt` for
CCD affinity; the adapter resolves that call to RDKit `Chem.Descriptors.MolWt`,
without changing the molecular-weight algorithm. No installed file is modified.
A differing grouping source emits a warning and retains installed behavior;
this is diagnostic evidence, not a new input admission gate.

## Samples and alignment

`publish_boltz_samples.py` enumerates the exact input YAML roster and each native
record directory, retaining every `model_N` PDB, confidence and PAE artifact.
`N` is the native output/confidence rank, **not** the latent diffusion draw index.
Each score sidecar carries `bms_native_source` with exact input ID, input YAML,
native filename and native model index. The existing alignment consumer resolves
the original design by this explicit source ID and preserves the association in
its result. It does not use a substring or a guessed model suffix. A sample
manifest records availability without introducing a new success condition.

## CPU qualification

Run `tests/test_boltz_fidelity.py` in the existing pinned image with read-only
source and molecule assets, private TMPDIR, `--cleanenv --no-home --contain
--net --network none`, no `--nv`. Tests execute the actual embedded complex
converter, native parser, real PDB/template preparation, sealed MSA hydration,
all-sample publisher and alignment consumer. `tests/boltz_cli_cpu_probe.py`
uses an existing Nextflow JAR's Groovy parser to render literal process CLI
expressions; native Click parsing verifies seed zero and requested numerical
settings without invoking the model callback. Synthetic coordinate/sample
fixtures are transport controls, not predictions. No checkpoint, model accuracy,
provider search, deployment, or historical affected-job claim is made.

Shared compiler, remote dependency inventory, UI and antibody/Shape invocation
changes are integration-owner responsibilities. Source helpers must travel with
all selected/remote Boltz processes; tests against a checkout alone do not prove
remote packaging or full saved-request lifecycle parity.
