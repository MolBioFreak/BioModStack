# Pinned setup acquisition API

## Integration contract

The existing setup CLI now integrates this API through `provision-plan`,
`provision`, and identity-bound `resume`; see [Setup provision CLI](Setup_Provision_CLI.md).
Library consumers import `services.runtime_acquisition` with the API module
directory on their normal import path:

```python
plan = preview_model_acquisition(model_id)
# Show all blockers and obtain explicit license acceptance separately.
receipt = acquire_model(
    model_id, managed_runtime_store,
    expected_plan_digest=plan['plan_digest'],
    accepted_licenses=accepted_license_ids,
)
```

- Preview and execution re-resolve `model_runtime_dependencies` and the existing
  model YAML registry. Do not accept artifact URLs/manifests from UI or agent
  requests. The `Artifact` low-level executor is a trusted-code library, not an
  approval endpoint. `approval_ref` names an externally reviewed release; a
  nonempty string is **not** cryptographic evidence of that review.
- Missing closure, missing metadata, changed plans, or missing license acceptance
  block before downloads. The CLI must preserve and display these failures.
- `ModelDefinition.acquisition` is an optional list of release-reviewed entries:
  `artifact_id`, `dependency: {kind, relative_path}`, exact HTTPS `url`, lowercase
  SHA-256 `sha256`, integer `size_bytes`, exact URL `source_authority` (host:port),
  `approval_ref`, and weight `license_id`. Each dependency must bind to the
  existing runtime closure. No model registry or scientific parameters are forked.
- `acquire_model` returns per-artifact content-store paths, hashes, byte counts,
  filesystem identities, manifest digest, and `qualification: not_checked`.
  Configuration transaction code must perform its existing runtime binding,
  license persistence, and scientific qualification. Acquisition does not mean
  readiness and does not activate anything. The CLI journals license acceptance
  and materialization; the release/configuration transaction remains separate.
- Errors may include `AcquisitionError`, the shared store's validation error, or
  filesystem errors. A failed multi-artifact operation is not model success;
  rerun with the same preview to rehash/reuse completed objects and resume partials.

## Byte and filesystem guarantees

`lib.pinned_acquisition.acquire` streams real HTTP(S), with one to five attempts,
bounded socket timeout and an elapsed transfer deadline. HTTP redirects require
an explicit reviewed redirect policy; content encodings remain rejected. See
`Reviewed_Redirect_Weight_Layout_Contract.md`.
Resume requires an exact HTTP Content-Range; a server returning 200 restarts from
zero. Exact digest and byte size are checked before publication. Transfer size is
capped by the manifest, not server claims. System resolver delays, filesystem IO
and cooperating publisher lock waits are not hard realtime bounded.

Staging lives under the selected cache's `.acquisition/<artifact_id>` on the same
filesystem. A manifest-bound, fsynced JSON checkpoint records the partial byte
count and SHA-256 after a transfer ends or raises. Each invocation hashes partial
bytes again. Abrupt death before checkpoint commit causes an explicit
uncheckpointed-byte blocker (no silent repair), rather than trusting that tail.
An interrupted HTTP body with a committed checkpoint resumes on the next call.
Changed manifests require explicit operator reconciliation; this API never deletes
or silently replaces their state. Do not reuse an artifact ID for a new release
without that reconciliation.

Publication and reuse call the **existing** `shared_runtime_images.publish_image`
and `verify_image`: stable no-follow file identities, flock, verified copy,
fsync, read-only object directory, atomic rename, full hash revalidation on reuse.
Corrupt published cache objects are rejected, never healed from staging. The
service-owned filesystem assumption of that authority still applies.

Runtime images use the existing store root. Weight byte objects use its separate
`weights` namespace by default, or the explicit configured `weights_root` supplied
by the CLI. They require explicit license identity and acceptance and are never
automatically unpacked or activated. The inherited opaque object filename
is `runtime.sif` even in the weights namespace; this is a cache naming convention,
**not** an assertion that weights are SIF images. The member materializer preserves
pinned identities in an immutable approved directory layout; CLI/release consumers
use `layouts[].dependency` → `layouts[].path`, never opaque member paths.
No such layout approvals exist in the current production registry.
Staging is retained for explicit reconciliation; budget roughly two artifact
copies for staging plus publication, separately from any future expansion.

`scripts/download_models.sh` has mutable URLs, existence-only reuse and no pinned
SHA-256/size approvals, so its historical URLs are not promoted into authority.
The shared image publisher accepts local `{source, sha256}` inputs for existing
release transport, not approved remote acquisition URLs or exact byte sizes.
Local release/qualification attestations likewise do not authorize downloads.
No local SIF or fabricated digest was used to fill these gaps.

## Current production availability

At base `964f275b2333e185efab0bb12155723de9790dbe`, all **30** registry entries
have empty acquisition metadata. **Zero real models have approved complete
acquisition metadata.** Explicit resolvable artifact blockers:

| Model | Missing approved acquisition metadata |
|---|---|
| protenix | image `protenix.sif`; weights `protenix` |
| esmfold2 | image `esmfold2.sif`; weights `esmfold2` |
| esmfold2_experimental | image `esmfold2.sif`; weights `esmfold2` |
| fampnn | image `fampnn.sif` |

`frustrampnn` is in the incremental independent-closure allowlist, but public
registry lookup currently excludes it; its YAML names `frustrampnn.sif` without
acquisition metadata. The API correctly reports closure unavailable rather than
bypassing that availability gate.

Other closure-unavailable entries (also no approved acquisition metadata):
`af2`, `antibody_child`, `antibody_denovo`, `boltz2`, `boltz_cp_experimental`,
`boltzgen`, `boltzgen_child`, `caliby_experimental`, `conformational_mapping`,
`confornets_experimental`, `diffdock`, `docking`, `fampnn_child`, `ligandmpnn`,
`molecular_dynamics`, `nanopore`, `oligo_design`, `protein_cad_experimental`,
`protein_hunter_experimental`, `protein_local_redesign`,
`protein_modification_experimental`, `proteinmpnn`, `rf3`, `rfdiffusion`, `unidock`.
Their artifact closure must be reviewed before individual download promises.

## Validation

Run separately from `platform/api` (API conftest deliberately forbids default
network/subprocess IO; the root transport suite owns loopback/file IO):

```sh
uv run --frozen --group dev python -m pytest ../../tests/test_pinned_acquisition.py ../../tests/test_shared_runtime_images.py -q
uv run --frozen --group dev python -m pytest tests/test_runtime_acquisition.py -q -s
```

The transport tests serve explicit non-model bytes from isolated `127.0.0.1`
HTTP servers, shut down/join those servers, and exercise real downloads, interrupted
Range resume, corrupt cache/checkpoint/body rejection, changed manifests,
oversized bodies, redirects, invalid ranges, bounded retry/timeouts, license
separation, and symlink rejection. `test_only=True` forces a separate
`test-fixtures-not-scientific-assets` store; the setup-facing API does not expose
this option. Fixtures are never entered into the production scientific registry
or registered as runtimes. The explicit CLI test harness uses an isolated
in-process registry authority only.
