# Shared scientific runtime images

## Contract and scope

Protenix conformational-mapping preflight and NGS samtools use verified immutable SIF objects rather than task-local or source-parent-specific full snapshots. A machine shares image bytes across environments and attempts; references, receipts and aliases are small metadata, not another copy of the image.

The store is not yet a universal replacement for every legacy model registry. Do not relink an image merely because its hash matches: some retained/local readers require a regular file at a registered path or pin inode identity. Preserve those consumers until their exact transport is migrated. Distinct custom builds and checkpoints are not duplicate images merely because their sizes/names are similar.

## Objects and environment references

- Default store: `${BMS_CONTAINER_DIR}/.image-store`, resolved through the installation profile as well as explicit configuration. Override: `BMS_RUNTIME_IMAGE_STORE`.
- Object: `objects/sha256/<digest>/runtime.sif`, file mode 0400, object directory 0500, one inode/link.
- Publication independently copies a mutable source once, hashes/verifies it, fsyncs and atomically promotes it under a lifecycle/digest lock. Valid existing objects are reused without changing their identity. Corrupt objects fail closed; they are not silently overwritten.
- Abruptly interrupted `.publish-<digest>-<uuid>` staging is recovered under the same publisher fence. Unknown staging entries/content fail closed rather than being recursively deleted.
- Store ownership is an external trust boundary: chmod alone does not prevent the filesystem owner/root changing objects. Ordinary execution must not mutate published objects. Do not create writable hardlink aliases.

Publish a complete lane selection with:

```sh
python scripts/publish_runtime_images.py --store-root PATH --lane development --manifest FILE
```

The JSON maps supported keys (`BMS_NGS_RUNTIME_SIF`, `BMS_CM_CONFORNETS_CONTAINER_PATH`, `BMS_PROTENIX_CONTAINER_PATH`, `BMS_FRUSTRAMPNN_SIF`) to `{ "source": "...", "sha256": "..." }`. A transaction records versioned retained lane releases in `references/state.json` before updating `references/development.env` or `production.env`. Valid legacy projections are retained during migration; unknown legacy references fail closed. Old generations remain protected until explicitly unretained. This is deliberate retention, not automatic indefinite backup copying: each digest still has one object.

Managed Development API/adapter units consume the selected store's Development reference projection. Runtime-specific settings explicitly selected by a supported caller are resolved at execution rather than prematurely freezing Nextflow defaults. Publication alone never restarts services, migrates production, removes originals or proves live adoption.

## Execution

Protenix stages a verified receipt/reference, not a SIF-containing preflight directory. Execution resolves and verifies the shared image at its boundaries. NGS samtools retains no-follow descriptor/inode checks and execution through an inherited descriptor. Deferred Dorado Nextflow selectors and nested clone/construct commands preserve the configured image selection.

Worker SIF transport publishes into the same per-worker `cache/runtime-images` store and transports small runtime aliases/reference manifests instead of full attempt copies for compatible readers. Input files, support tools and model data keep their existing materialization semantics. Retained legacy cache/attempt images are not automatically deleted by this change. Exact-path/no-follow scientific registries must retain their prior compatible materialization until explicitly migrated; cleanup must not disable a supported workflow just to claim zero copies.

### FrustraMPNN canonical reader

`BMS_FRUSTRAMPNN_SIF` is an installation-owned path selector. Set it to the existing shared store's `objects/sha256/c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da/runtime.sif`, with `BMS_RUNTIME_IMAGE_STORE` identifying that store (default `${BMS_CONTAINER_DIR}/.image-store`). The selector is snapshotted into the existing runtime identity at process startup; restart through the normal managed release process after changing it. It does not select a new model digest, executable or checkpoint. No public request parameter or digest override is added.

Without this selector, the existing exact `${BMS_CONTAINER_DIR}/frustrampnn.sif` regular-file contract remains. With it, existing component/grouped/CM callers may supply that registered semantic name; the central validator returns the configured canonical path without reading or resolving the alias. Arbitrary alternate paths are rejected. The scientific reader verifies the shared store's immutable-object constraints, compares the verified generation with its no-follow descriptor, and retains that descriptor for executable/checkpoint authentication and inference. Opening a symlink directly remains forbidden.

The registered `run_frustrampnn` workflow stage uses the ordinary shared worker image transport for prewarm and launch. Bundles select the canonical worker object through the existing authenticated envelope's `BMS_FRUSTRAMPNN_SIF` environment entry, alongside `BMS_RUNTIME_IMAGE_STORE`; the existing runtime-image manifest authenticates bytes and compatibility aliases. No Frustra-specific store, cache kind or reference schema exists. Same-digest names, provisioning and repeated attempts reuse one worker object rather than retaining ordinary-cache and per-attempt SIF copies. Public standalone model launch/provisioning availability is unchanged.

The Protenix and canonical Confornets Nextflow labels defer their container selection until task evaluation, respecting `protenix_container_path` / `cm_confornets_container_path` (including their existing environment defaults) before semantic-name fallback.

Retained capability inventories and execution configurations remain historical evidence: their own original content hashes are verified without rewriting them, and compatibility compares every scientific runtime field while excluding only the host `configured_sif_path`. The v1 global configuration keeps its original semantic installation path/hash. V1 receipt validation accepts that original registered installation path as provenance after canonical selection. A new retry configuration uses current placement; retained placement never authorizes execution. Fresh-process tests cover reopening/recompiling retained configurations with the original path absent; filesystem tests separately pin a real canonical fixture object with both original and alias absent.

Publication/lane-key enablement and managed unit projection are coordinated with the shared lifecycle owner; reader migration alone neither removes old images nor authorizes live cleanup.

## Retention and explicit retirement

The lifecycle exposes durable leases (`acquire_lease`, `release_lease`) for callers that can bind queued/running/resumable job ownership. These are **not yet wired into every legacy admission/resume path** and do not expire merely because a process dies. No unattended collector is enabled.

```sh
python scripts/retire_runtime_images.py --store-root PATH list
python scripts/retire_runtime_images.py --store-root PATH why-kept --digest SHA256
python scripts/retire_runtime_images.py --store-root PATH plan --digest SHA256
python scripts/retire_runtime_images.py --store-root PATH select-release --lane development --release ID
python scripts/retire_runtime_images.py --store-root PATH forget-release --release ID
```

`plan` emits a JSON review receipt. Current lanes, retained releases, and durable leases protect their objects. Unknown reference files, stale projections, changed objects and stale plans fail closed. Forgetting a non-current release changes retention metadata only; it does not delete an image or establish that external jobs no longer use it.

`apply --plan FILE --maintenance-authorization CHANGE_ID` revalidates under the lifecycle fence and **quarantines by rename, not deletion**. The authorization identifies an externally established maintenance window: admissions fenced, all legacy/queued/resumable users accounted for, and no untracked aliases/users. A string alone cannot establish those conditions. Quarantine reclaims zero bytes. Physical purge/grace policy is a separate explicitly reviewed maintenance action. Current references alone and an unprivileged open-FD scan are insufficient grounds for deletion.

## Acceptance boundary

A deduplication fix is incomplete until every affected supported workflow actually
invokes its selected singular image. Reader support, environment inspection and
bytes reclaimed are not substitutes for this execution contract. Verification
must cover the actual submission command compiler, local container launch, remote
dependency inventory and translated worker command, saved-job prewarm, independent
provisioning where publicly supported, managed startup probes, and retained retry
configuration. Tests must remove conventional originals and record the actual
container path or pinned image inode at invocation; repeated attempts must not
create task-local SIF copies. Do not insert selector arguments in a test that the
production compiler should have supplied. Distinct experimental and canonical
builds retain distinct scientific identities even when both use the same store.

Automated tests cover real temporary filesystem publication, concurrent publishers, killed publication processes, reference migration/rollback, retained leases, quarantine fencing, remote transport and real Nextflow configuration resolution. Synthetic fixture bytes and fake model commands do not prove scientific inference. Live-image checks must separately exercise real Apptainer execution and verify unchanged image identity/allocation; model/GPU qualification remains distinct.

A host migration is complete only after consumer adoption, safe retirement of redundant originals and measured allocated-space reduction. Do not equate publication, a passing test count, or a symlink name with completed storage consolidation.
