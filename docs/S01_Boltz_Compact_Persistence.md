# S-01 Boltz compact persistence boundary (not activation)

`ingest_job_results(job_id, output_dir, session, commit=...)` routes trusted
`Job.provenance.core_protein_scientific_contract == 1` (integer, not Boolean)
Boltz2 predict/complex results to the private
`services.boltz_scientific_persistence.ingest_verified_boltz` adapter. Existing
explicit FrustraMPNN terminal owners retain precedence. CM is not routed here.
`ACTIVATED_CALLERS` remains empty. This document does not declare S-01 complete.

## Producer transport and inventory

The three literal Boltz process owners in `modules/structure_prediction.nf`
retain their existing flattened legacy publications and additionally copy marked
outputs to `scientific/boltz/<producer-task>/` under the job result root:

- `producer_candidates.json`: the **unchanged real producer manifest**;
- `predictions/`: the declared native PDB, confidence JSON, processed-structure
  NPZ ledger, PAE NPZ and pLDDT NPZ, as transported by the existing publisher.

Sequence/WithMSA task namespaces use `producer_meta.producer_artifact_id`;
complex uses `complex_name`. Namespace directory names are transport selectors,
not scientific candidate identities. Ingestion discovers producer manifests, not
structures; only their closed candidate inventories establish documents. It
never reconstructs identity from filename scans, array lengths, chain counts or
ASCII arithmetic. All discovered inventories must validate before any Design
cleanup, autoflush or insertion. Empty/malformed declarations fail closed.

## Launch authority and complete task membership

The operator-settled authority is persisted in `Job.provenance.boltz_launch_authority`
by `services.nextflow._persist_boltz_launch_authority`, before adapter/remote
handoff and again at the native spawn command boundary. The source is the
persisted request plus the existing command/input compiler, after registry and
strict registered-field type validation. Caller-supplied transport fields are
removed; the producer revision and roster transport are reconstructed only from
the persisted scientific marker. The previously separate producer revision name
(`protein_science_contract_revision`) is now explicitly transported by that trusted
owner; passing a parameter marker alone cannot authorize it. The launch owner
atomically writes the exact persisted authority bytes to the job-owned
`.boltz-launch-authority.json` and passes only its compact path plus SHA-256 on
the command line. No base64 authority/MSA payload is passed as an argv element.
The workflow refuses foreign paths and symlinks, captures bounded file bytes
without following the final symlink, checks their digest, and parses those same
bytes. The transport file is not a second authority; SQLite provenance remains
trusted truth.

The closed version-1 authority contains:

```text
schema_name: boltz_launch_authority
schema_version: 1
job_id, model_id, mode, result_root
attempt: <persisted Job.retry_count>
request_sha256: <canonical persisted Job.params bytes>
tasks: [
  namespace: <resolved sequence producer_artifact_id or complex_name>
  owner: BoltzFromSequenceTask | BoltzFromSequenceWithMSATask | BoltzFromComplex
  metadata: <existing sequence producer metadata, or null for complex>
  input_sha256: <normalized sequence bytes or captured complex JSON bytes>
]
input_files:
  <resolved input path>: {sha256: <hex>, content_base64: <captured exact bytes>}
```

The sorted task list is nonempty, unique, and bounded; total authority is at most
2 MiB and 10,000 tasks. Sequence metadata uses the existing name-as-ID semantics
and producer metadata validator, not another identifier algorithm. Complex
single-input and batch-manifest owners, explicit MSA inputs, and component-owned
MSA paths are captured. Component MSA selectors must be resolved absolute paths.
Same-job/same-`retry_count` resume requires identical authority bytes; changed
inputs fail. A new attempt or new job recompiles and validates fresh authority.
No new caller is activated by this code.

`checkedBoltzInputs` collects the existing input-owner channel before any Boltz
dispatch (and before parent MSA/complex preparation), independently constructs
its roster, and compares it with the launch roster/hash. It hashes and parses the
same captured bytes. It dispatches frozen source/MSA snapshots rather than
mutable input symlinks; complex component MSA references are resolved to those
snapshots without changing the persisted original-input identity. Its closed
`scientific/boltz_workflow_inventory.json` binds job, attempt, root, launch hash,
and exact tasks. Each literal producer also publishes
`scientific/boltz/<task>/boltz_task_binding.json`, tying that task invocation to
this authority/attempt without modifying its native producer manifest.

Ingestion requires launch authority, the exact independently compiled workflow
inventory, every task invocation binding, and exact expected-versus-observed
task membership **before first mutation or autoflush**. Whole missing task
directories, foreign/extra/equal-count substituted namespaces, duplicate roster
identities, stale attempt bindings, and missing/changed inventories fail closed.
Individual native candidate verification remains unchanged. Requested task count
is not candidate count: the software transport fixture has two tasks and four
candidate documents. The existing generated/selected/published candidate receipt
remains separate and additionally hash-binds workflow inventory and task bindings.

The immutable source snapshot verifier is
`scripts/lib/boltz_native_identity.verify_boltz_native_identity`. Ingestion opens
relative paths with directory FDs and `O_NOFOLLOW` at each component, verifies
manifest and native hashes, independently reconstructs axes/native asym-ID chain
mapping, then compares the complete producer claim. It revalidates paths against
the prepared snapshot hashes before mutation. Parsing never reopens a mutable
path in place of the captured bytes.

## Stable compact shape

`Design.confidence_metrics` adds exactly these owner keys (other subsystem keys
are not repurposed):

```text
core_protein_scientific_contract: 1
core_protein_candidate_artifacts: <same artifacts object as below>
core_protein_scientific:
  schema_name: boltz_scientific_design
  schema_version: 1
  design_id: <actual Design.id>
  candidate_id: <producer candidate ID>
  document_id: <producer_output_key>
  producer:
    producer_method: boltz
    producer_sample: <producer-owned sample>
    producer_rank: <nonnegative integer or null>
    producer_output_key: <unchanged producer-owned key>
  artifacts:
    structure: {path: <job-contained absolute selector>, sha256: <hex>}
    manifest:  {path: <job-contained absolute selector>, sha256: <hex>}
    metrics:   {path: <job-contained absolute selector>, sha256: <hex>}
    ledger:    {path: <job-contained absolute selector>, sha256: <hex>}
    pae:       {path: <job-contained absolute selector>, sha256: <hex>}
    plddt:     {path: <job-contained absolute selector>, sha256: <hex>}
  metrics: [<two canonical scalar envelopes below>]
  identity:
    provider_revision: 7ebf1be087d4d61a02234c878402838bf3712d8b
    source_axis: boltz_native_tokens
    chain_key_namespace: native_asym_id
    matrix_key: pae
    vector_key: plddt
    vector_unit: fraction
```

All block fields are constructed by this verified adapter; replay compares the
exact block, including its database binding. No public generic setter is added.
Canonical metric validation is reused from
`core_protein_scientific_contract.validate_metric`, not duplicated.

A scalar envelope is exactly:

```json
{
  "metric_key": "ptm",
  "state": "ok",
  "value": 0.6,
  "reason_code": null,
  "unit": "dimensionless",
  "direction": "higher_is_better",
  "scope": "overall",
  "producer_version": "7ebf1be087d4d61a02234c878402838bf3712d8b",
  "derivation_version": "boltz-native-scalar-v1",
  "source": {
    "artifact_sha256": "<exact confidence JSON SHA-256>",
    "candidate_id": "<unchanged producer candidate ID>",
    "document_id": "<unchanged producer document ID>"
  }
}
```

The second record uses `metric_key=complex_plddt`, `unit=fraction`,
`scope=complex`, with the same envelope fields. Both require native finite
non-Boolean numerical values in [0,1]. Zero remains valid. Values are read from
the exact verified confidence JSON, not recomputed from the PAE matrix or vector.
No legacy Design scalar columns are populated or relabeled by this adapter.

Sequence candidates retain `producer_artifact_id` as candidate ID and
`producer_output_key` as document ID (several documents can share a sequence
candidate). Complex producer manifests use `producer_output_key` for both.
`Design.name` is the document key; `Design.id` is an independent generated DB ID.
Neither replaces IDs in hash-bound axes. Bulk residue axes, chain maps,
token-to-structure maps, matrices and vectors remain in the manifest/native files,
not SQLite JSON. Consumers must use the descriptor hashes and verifier rather
than attach the sidecar as trusted data.

## Scalar source authority and deliberate omissions

Pinned upstream source inspected at
`https://github.com/Novel-Therapeutics/boltz-community/tree/7ebf1be087d4d61a02234c878402838bf3712d8b/src/boltz`:

- `model/layers/confidence_utils.py`, SHA-256
  `a0013daa1f79280826ac886cd27e0382fb50e9b167218713335152ad6c149ec5`:
  lines 116–128 aggregate normalized bin probabilities over [0,1]; lines
  131–169 define dimensionless native whole-prediction pTM, distinct from iPTM.
- `model/modules/confidencev2.py`, SHA-256
  `d2de7be37f65a360889afdeb0e09992afdd47dca6074cfb93b87f20545b47b07`:
  lines 324–329 / 397–401 define complex pLDDT as the native masked aggregate;
  lines 479–487 publish pTM separately. This adapter does not claim a particular
  atom/token aggregation policy beyond the native whole-complex scalar. The
  provider can publish zero pTM on its own exception fallback; `ok` means a valid
  native scalar representation, not independent model-execution success.

No binder/target roles, chain names derived from numerical keys, pair-matrix
scalars, ipTM role interpretation, magnitude-inferred units, or pTM-to-pLDDT alias
are introduced. Ambiguous native fields are omitted, not presented as validated
canonical metrics. Existing `design_metrics` key/unit/direction/scope vocabulary
and the common scientific envelope remain the consumer conventions.

## Transaction and replay contract

All candidates, both scalar records, source identities and artifact snapshots
are prepared before mutation. A malformed final candidate leaves prior review
rows and caller pending edits intact even after catch-plus-commit. `commit=False`
flushes without committing; caller rollback removes the new publication.
Identical replay returns zero and retains IDs/bytes. Changed source evidence,
changed canonical blocks, missing rows and equal-count substitutions fail.
The entire canonical block and publication receipt compare canonical JSON bytes
using the existing `services.frustrampnn.contracts.canonical_json_bytes` owner
(the same owner used by `result_ingester._strict_canonical_json_equal`), not
Python container equality. SQLite-persisted Boolean substitutions for schema
version `1`, native scalar `0.0`, or receipt count `1` are rejected without
repair; string substitutions are rejected too. Regression fixtures explicitly
mark JSON columns modified because SQLAlchemy's ordinary dirty comparison can
otherwise suppress the intended Boolean-corruption UPDATE. Reopened-session
catch-plus-commit checks preserve both corrupted stored evidence and unrelated
pending caller edits, with no ingestion autoflush.

The existing `core_protein_candidate_publication` receipt owns exact persisted
document IDs and artifact hashes. The shared validator recognizes only the four
additional Boltz evidence roles without pretending they are `Design.json_path`;
structure and metrics still require their real column bindings. No new
`core_protein_native_prevalidated` bypass is set by the Boltz adapter and no
finalizer precedence or filter integration is changed.

## Data-only software transport acceptance

`tests/test_boltz_workflow_transport.py` executes the cached native Nextflow
25.10.1 jar with an isolated home/config/cache, offline mode, local CPU executor,
and container engines disabled. It extracts the literal three producer owners,
retaining their input/output declarations, all publishDir rules, and task-binding
command. Only model script bodies are replaced with copies of pinned data-writer
fixture payloads **inside task work directories**, never into the final result
root. The actual input normalization and workflow precheck functions execute.

Each owner runs two namespaces with two native candidates apiece, plus unmarked
controls. Tests compare every published manifest byte and native artifact byte,
then call real `ingest_job_results` against the actual published root and commit/
reload temporary SQLite. They verify every stored artifact location/hash and
identical replay. Separate actual-publication controls remove whole tasks or
inventories, substitute namespaces, change attempt/input/roster authority, and
prove catch-plus-commit retains review rows and unrelated pending edits without
autoflush. Source-file race controls change complex JSON, explicit MSA, or
component MSA files after workflow checking, proving tasks consume the captured
snapshots instead of reopened mutable paths. The real launcher handoff test reads
committed authority using an independent SQLite connection before the adapter
boundary is invoked. The large-input control uses a 150 KiB MSA, asserts compact
argv elements, and exercises real Nextflow authority-file loading; foreign-file,
symlink and hash-mutated-file controls fail before dispatch.

API unit fixtures remain direct persistence fixtures and are not claimed as
Nextflow proof. The replay Boolean regressions still force SQLAlchemy JSON
modifications to SQLite. API conftest subprocess restrictions are unchanged;
native transport runs outside that tree. No model, service, hardware, GPU,
network, or deployment acceptance is claimed. Analytics and activation remain
outside this boundary; the PAE-only consumer continuation is described below.

## PAE-only persisted consumer continuation

`boltz_scientific_consumer` re-verifies launch/task/publication authority and
native source/ledger/artifact bytes from the owning Job. It compares the exact
persisted candidate set and compact blocks without ingestion, UUID allocation,
or writes. `_verified_publication` is the shared read-only factor; only the
writer's `_prepare` allocates Design identities. Reads currently verify the
whole publication, not a cached selected-task claim.

Design list, by-job list, and detail attach a compact `ViewerDocument` with
`documentId=primary`, the actual selected DB Design ID, and verified structure
SHA-256. Producer candidate/document IDs remain unchanged in native axes and
are explicitly mapped by the PAE wire's `producer_binding`. No full axis or
matrix is included in a Design response. Invalid native evidence leaves the
structure usable and the PAE endpoint reason-coded unavailable.

New ingestions materialize the existing structure-prediction review profile
and verified aligned-error path/format/key columns so the existing analysis
admission and frontend analyzer discovery can actually reach PAE. These columns
are not scientific authority: the PAE signature and worker both use the trusted
consumer, not legacy path fingerprints. Historical rows are not rewritten.
The signature binds full native identity and artifact hashes; unavailable state
has a separate namespace and cannot reuse a healthy cached run. The strict
loader receives exactly `artifact_sha256`, `matrix_key`, `row_axis`, and
`column_axis`. Publication hashes are checked again after its read, preventing
changed source or native ledger generations from being committed as a positive
projection.

The real transport test now serializes Design/PAE through in-process ASGI after
SQLite reload for each marked literal owner. It writes that exact wire to a
temporary file (no DB UUID normalization or hand-built positive fixture), passes
its SHA-256 to Vitest, and mounts the existing StructureViewerPane parser and
PairMatrixExtension. The GPU owner alone is substituted. Directed pixels,
keyboard/click selections and complete ResidueRefs, malformed wire controls,
and foreign selected-document rejection are exercised. The integration Vitest
file is explicitly included when `BMS_TEST_BOLTZ_WIRE` is present; the Python
transport test owns generation and invocation. Separate API tests exercise the
actual analysis request/read routes and `_run_analysis` against temporary
SQLite/cache roots, with no worker process or live service launch.

Positive residue-pLDDT, chain metrics, analytics, ESMFold continuation, and caller
activation are intentionally not opened by this PAE slice.

Focused commands (from the stated owning directories):

```bash
# platform/api
uv run --frozen --group dev python -m pytest tests/test_boltz_scientific_persistence.py tests/test_core_protein_candidates.py tests/test_scientific_residue_identity.py tests/test_core_protein_scientific_contract.py tests/test_boltz_launch_authority.py tests/test_boltz_task_inventory.py -q -s --tb=short

# repository root; explicit local cached jar, never the Docker launcher
BMS_TEST_NEXTFLOW_JAR=/path/to/cached/nextflow-25.10.1-one.jar platform/api/.venv/bin/python -m pytest tests/test_boltz_workflow_transport.py tests/test_boltz_native_producer_identity.py -q -s --tb=short
```
