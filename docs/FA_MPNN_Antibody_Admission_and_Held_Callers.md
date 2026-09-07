# FA-MPNN antibody declaration and held generated callers

Authority: BMS-CP-SCI-01 FA-06–FA-10/C-01 and the settled S-00 scope.
This package changes software contracts only. `ACTIVATED_CALLERS` remains empty.
It adds no public mode, model integration, historical-result operation, or mutation-guidance ingestion.

## Caller declarations

| Existing caller | Summary default | Mutation default / overrides | Admission disposition |
|---|---|---|---|
| Direct FA-MPNN general sequence design | Declared protein input domain | Authorized nonfixed sequence-design residues; typed narrowing only | Existing inventory; activation OFF; inherited implementation retained |
| Direct FA-MPNN binder design | Exact declared binder-role residues | Authorized nonfixed binder sequence-design residues; typed narrowing only | Existing inventory; activation OFF; inherited implementation retained |
| Local redesign FA-MPNN | `sequence_redesign_positions_spec` | Its authorized nonfixed region; typed narrowing only | Existing inventory; activation OFF; inherited implementation retained |
| Antibody de novo FA-MPNN | Exact authorized sequence-design antibody domain; includes framework when the existing request authorizes framework design | Resolved native CDR membership minus actual fixed/protected residues; typed narrowing only. Summary is fixed, with no summary override | Existing `antibody_denovo/antibody_denovo_pipeline` and `fampnn_child/sequence_design` inventory only; activation OFF |
| Legacy RFdiffusion general-to-FA-MPNN call in `protein_design.nf` | Declared generated protein domain (held declaration, not a fabricated residue map) | Authorized nonfixed sequence-design region; narrowing only | **HELD / UNADMITTED.** No authoritative public caller inventory entry. The admission compiler rejects legacy `rfdiffusion` callers |
| Legacy RFdiffusion binder-to-FA-MPNN call in `protein_design.nf` | Exact generated binder-role domain (held declaration, not a guessed chain) | Authorized nonfixed binder sequence-design region; narrowing only | **HELD / UNADMITTED.** No new caller entry or activation |

General RFD3 requires `run_rfd_only=true` (`workflows/protein_design.nf`), so it is not an admitted generated FA-MPNN route. BoltzGen's sequence-design stage is skipped. Neither route is enabled by publishing this table. There is no analyzer-wide biological default. Missing child declarations, unsupported parent owners, unsupported materialization sources, missing native role proof, and incompatible prepared bytes fail closed.

## Real software joins

1. Normal `JobCreate` / `_create_job` persists the initial antibody declaration on `Job.provenance` and transports it in params. It retains framework selection and existing settings, but does not invent future generated residue IDs or candidate files.
2. The existing RFantibody entrypoint is run through the existing wrapper's role-export-only branch. This branch does not apply the separate rotation patch. The adapter requires exact source-file hashes and verifies loaded module origins. A mismatching debug overlay cannot supply role evidence; no letters-only fallback is available.
3. Native `AbPose.H/L/T` -> `get_chain_idx` -> `ab_write_pdblines` is the inspected role mapping. Native `get_loop_map` supplies global one-based CDR positions. The exporter records framework/target input paths and hashes, relevant executed-source hashes, role/CDR identities, and a PDB-body binding. It does not infer a framework-to-generated residue alignment.
4. `prep_fampnn_designs.py` carries that record through its existing residue-object pairs at each dump. The existing antibody constraint owner records its actual fixed result and authorized region in the preparation receipt. Native loop labels remain authoritative over stale original chain-position fallback requests.
5. `SpawnFAMPNNJobs` transports `analysisContract.declaration`, not `.policy`. `spawn_fampnn_children.py` strips reserved internal authority before public submission. The real child schema/admission reloads the parent's persisted declaration, enforces forbidden summary/expanding mutation overrides before scheduling, checks the now-existing prepared inputs, and inherits the parent's sequence-design settings.
6. The existing compiler writes hash-bound declaration transport. Resolution verifies actual preparation bytes and source/constraint evidence; only then does native candidate binding enumerate actual output files. The existing analyzer consumes the resolved policy. Original parent declaration is not replaced by resolved artifact evidence.

Other transformations or overlays without this source binding are not treated as role proof. In this checkout, `scripts/normalize_antibody_inputs.py` does not exist. The active target normalization entrypoint is `NormalizeTargetPDB` / `scripts/normalize_target_pdb.py`; no missing script's behavior was assumed.

## Static native source evidence

Read-only `unsquashfs -cat -o OFFSET SIF exact/path` extraction; no mount, image execution, model run, checkpoint read, installation, or clean-tree qualification.

RFantibody image `/mnt/BioModStack/apptainer/rfantibody.sif`, offset 61440, checkout `/opt/RFantibody`; parent-reported HEAD ref `2b864664e48c87edac7f119c157f77e5bbad16e9` is not clean-tree proof. The relevant actual file hashes are:

| Relative native file | SHA-256 |
|---|---|
| scripts/rfdiffusion_inference.py | fe66312248ba280e6bb05fa66677a6eb96dd8206679ce7d0d9ad5760a9495aca |
| src/rfantibody/util/io.py | 8aa179cfdf4cb84092308e57cd2b43c8e8cde9074c072bb2f089ab0a5c9aab19 |
| src/rfantibody/rfdiffusion/inference/model_runners.py | 1cbfddc72b257ba1b7bec3b28c8712f37e7eca5ff0d93da92da60033ed8d8c65 |
| src/rfantibody/rfdiffusion/inference/ab_pose.py | 215aea008dba95547a294988965b91416e19cf60739a45647c93691e3f77c917 |
| src/rfantibody/rfdiffusion/parsers.py | 607fbf934c643a8a5da975eb1e7a2f98fd15a2a3ee796f4b057dfae00d1400e1 |

FA-MPNN image `/mnt/BioModStack/apptainer/fampnn.sif`, offset 53248, checkout `/app/fampnn`; parent-reported HEAD `18363df253dbeb7b2cb963daf7a732fbaa25157d` matches the SOW producer dialect. Actual source hashes:

| Relative native file | SHA-256 |
|---|---|
| fampnn/inference/seq_design.py | 9d790bf2009ed9ec7b863d36e19e035cb06e6287ce86e0e4474a2009d3e2e9b5 |
| fampnn/data/residue_constants.py | 0e2363f9242dbdffa6f5f6071d5ba2f04e9b31938ed029ea32ba25555e5f296f |
| fampnn/model/sd_model.py | c11837a76e25b956e1f57f28b7d95950177e6d762ce82c915ac4685b836b5b72 |
| fampnn/sampling_utils.py | 21c8e6212ee62e9bdf7bd3d03fb7ebfcbbd52341dd64fa6483579e367b980bf0 |

## Verification boundary

`platform/api/tests/test_antibody_fampnn_roundtrip.py` exercises actual API admission and disposable SQLite persistence, source-shaped generated physical PDB records, the actual constraint CLI, real child-spawn payload/schema/admission, compiler, resolver, native-file binding and the existing analyzer. Model inference and the HTTP transport are test doubles, not live scientific execution. Root tests also exercise real data-only Nextflow `RunFAMPNN` transport against producer-shaped PKLs, native-role remapping, byte substitution rejection, and source-origin rejection. No frontend file was edited by this writer. Whole-candidate independent review remains the parent's gate.
