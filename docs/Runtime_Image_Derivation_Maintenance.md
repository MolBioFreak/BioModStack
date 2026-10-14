# Explicit one-image derived-rootfs recovery

This is an **operator maintenance action**, not preparation, garbage collection,
image retirement, or scientific execution. It lives in the existing artifact
helper and shared image lifecycle owner. Ordinary launch/preparation is unchanged.

## Request contract

Invoke the **newly delivered** `bms_artifact_cache.py --root
/opt/biomodstack/cache/artifacts/v1` with this JSON on stdin:

```python
request = {
    'action': 'rebuild_runtime_image',
    'artifact': {'kind': 'runtime_image', 'sha256': digest, 'size_bytes': size},
    'operation_id': operation_uuid,  # one UUID, persisted and reused on retry
    'source_identity': old_manifest_source,
    'retire_leases': {token: exact_existing_lease_row, ...},
    'maintenance_authorization': 'the approved scope and external quiescence assertion',
}
```

`source_identity` is the exact old `manifest.json.source` (sha256, size, device,
inode, mtime_ns, ctime_ns). Each lease row is the full native row: owner,
identities, created_ns. Only exact single-image cache-artifact/attempt owners are
accepted. Lease identities need not equal the old rootfs source: successive failed
attempts can have pinned different source generations. No identity field is ignored
or rewritten. Persist the request **before** invocation; retries use identical JSON
values and operation ID, not a freshly collected post-maintenance lease allowlist.

Under lifecycle then selected-digest flock, the action fully hashes the canonical
SIF, audits the old derivative against its old source identity, preserves it by
same-parent rename to `.rebuild-<digest>-<operation_uuid>`, atomically retires only
the exact selected lease rows and reacquires the current cache-artifact lease,
then runs the existing `_derive`/`extract_sif` preparation and audits its output.
Canonical bytes, mode, inode and timestamps are not modified. Unlisted leases of
the selected image are retained/refused rather than inferred dead; unrelated
leases and images are untouched. No Jobs, receipts, aliases, inputs or outputs are
rewritten. Retained lane releases are not removed or changed.

The operation receipt is `quarantine/rebuild-<operation_uuid>.json`. Interruptions
before/after receipt publication, derivative rename, reference publication, or
extraction can be retried with that same request. Existing `_derive` recovery
removes only selected-digest interrupted extraction stages. Old rootfs bytes remain
quarantined (not physically deleted); reserve space for the new rootfs. A corrupt
canonical SIF or derivative still rejects. Do not delete the receipt/quarantine to
make a failed retry pass. This is not a general corruption-repair override.

## Approved parent sequence: target `vast:55308509` only

1. Integrate this commit on top of `f189b0e70517cb6af4f34f123859869763d401be`
   and perform the already-approved **single** controller deployment/restart.
   Do not touch targets `vast:55071963`/`vast:55301457` or the three binder Jobs.
2. Re-read the exact target and the six failed Jobs/attempts with their existing
   controller/native worker ownership APIs. Use each retained worker runner's
   `status --attempt-dir /opt/biomodstack/attempts/<attempt-id>` for its native
   writer observation. Retained image references are not live writer evidence.
   No new proof/admission requirement is introduced by this action.
3. `POST /api/execution-targets/vast:55308509/deactivate` with no body. The existing
   `deactivate_target` owner requires no nonterminal target Jobs, claims, setup or
   preload; it fences ordinary admission by setting this target inactive. Read
   back this exact target. Maintain the externally approved maintenance fence
   throughout helper delivery and recovery (no concurrent operator activation).
4. From the deployed API source/environment, use existing helper delivery, not a
   whole runtime/weight pack or a hand-copied lifecycle module:

   ```python
   import json
   from dataclasses import replace
   from services.remote_execution.cache import _install_helper
   from services.remote_execution.transport import (
       RemoteConnection, run_remote, quiesce_provision,
   )

   # target is the exact freshly read native ExecutionTarget row;
   # check_fence is the parent's existing maintenance ownership recheck.
   connection = RemoteConnection.from_target(target)
   # Persist a separate transport UUID in the parent's continuation. The existing
   # provision owner tracks/reaps helper processes AND extractor descendants.
   connection = replace(connection, provision_operation_id=transport_uuid)
   helper_path = await _install_helper(connection, check_fence)
   ```

   This installs only missing/mismatched bytes of the content-addressed helper
   generation: `bms_artifact_cache.py`, `shared_runtime_images.py`,
   `runtime_image_lifecycle.py`, `runtime_image_views.py`, `bms_hf_transfer.py`.
   It does not activate/replace the worker's managed scientific runtime.
5. Read exact selected metadata through that delivered lifecycle owner (read-only):

   ```python
   read_scope = '''import importlib.util,json,sys
   from pathlib import Path
   spec=importlib.util.spec_from_file_location('maintenance_cache',sys.argv[1])
   helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
   life=helper.runtime_lifecycle()
   digest=sys.argv[3]
   with life.transaction(Path(sys.argv[2])) as root:
       state=life.load_state(root)
       manifest=json.loads(life._read(root/'objects'/'sha256'/('.rootfs-'+digest)/'manifest.json'))
       print(json.dumps(dict(source_identity=manifest['source'],leases={
           t:r for t,r in state['leases'].items() if digest in r['identities']})))
   '''
   observed = json.loads((await run_remote(connection,
       ['python3', '-c', read_scope, helper_path,
        '/opt/biomodstack/cache/runtime-images', digest])).stdout)
   ```

   Approved digest:
   `625d81608118af006295601b8275389e7681a1d98a6fb0622e6241c5d101df4b`,
   size `996466688`. The approved old derivative source has ctime
   `1791679184205405441`; last observed canonical ctime was
   `1791681917867309677`. Do not manufacture lease rows from these observations.
   Select exactly these seven token/owner pairs from `observed['leases']`:

   | Token | Owner (attempts use `attempt:<id>:image:<digest>`) |
   |---|---|
   | `03950a8509774db787c3bba523576596` | `cache-artifact:<digest>` |
   | `772b52e32a7446ecb5fb257d88daa966` | `5b7bcff0-4674-4a70-8533-9b0c4be2c1bf` |
   | `83bb7e81c02e462cb363294ed84f816f` | `8512a51a-fe82-4659-a4f3-2731b9290955` |
   | `96cd8664d2e44afcba29fe05d7185d00` | `c5d571ba-475f-4a16-88cc-f845db8a3e6b` |
   | `9fa5f3acad244a2e84a734722bd74671` | `75c87e49-c449-4076-a043-f762bbcbd633` |
   | `d1695148307f4b8e93a7de631ccb037b` | `288b499b-d12f-4d02-85c7-997efdd09d81` |
   | `ee20e4aef9b6487ab277e7e6112d6ea2` | `7bf0f76c-3ff6-4214-b6e0-d14c68242e8f` |

   The unselected `fc2792093d26fb3af7fb88a2c01258f5cdd7814d743d8d108283dd88e02d9e12`
   image and its references stay unchanged. The action does not scan scientific
   data or infer any lease expiry.
6. Persist the request locally, then execute it once through the same native
   transport. Extraction is data-only (`apptainer sif list` + `unsquashfs`), not
   PRoot or GPU science; it can precede the managed runtime activation:

   ```python
   await check_fence()
   result = json.loads((await run_remote(connection,
       ['python3', helper_path, '--root', '/opt/biomodstack/cache/artifacts/v1'],
       input_bytes=json.dumps(request).encode(), timeout=3600)).stdout)
   await check_fence()
   ```

   Keep the same fence and request on interruption; never repeat activation to
   repair references. Read back through `load_state` and `verify_derivation`:
   selected old tokens absent, current cache token/identity as returned, unrelated
   rows unchanged, canonical full hash/identity unchanged. Exercise warm preparation
   through the same helper with
   `{'action':'prepare_runtime_image','artifact':request['artifact'],
   'backend':'udocker','operation_id':request['operation_id']}`.
   On success **or interruption**, close/reap that transport operation through
   `await quiesce_provision(connection, transport_uuid)` before retry/activation.
   This is the existing process-group owner, not a new maintenance gate. A retry
   after it is closed uses a new persisted **transport** UUID but the identical
   **rebuild request/operation UUID**; never reuse a closed transport admission.
7. Activate **once** through `POST /api/execution-targets/activate`:

   ```json
   {"provider":"vast","provider_instance_id":"55308509","username":"root","remote_root":"/opt/biomodstack"}
   ```

   Poll `GET /api/execution-targets/vast:55308509` until the existing attachment
   owner reports ready. This supported activation installs the revisioned
   `tools/udocker-1.3.17-bms1` and updated managed runtime from the combined deployed
   source. Verify the consumed PRoot engine SHA
   `5ff4fd4367f2cdf3fcbb8088f5e36eed2070a97fc0e401f97ca30b2f90facd5a`
   and new bound release using existing runtime metadata. Parent alone then owns
   the approved scientific retry; this maintenance operation never launches it.

No raw state/receipt edits, broad recovery/reset, generic cleanup, image retirement,
or canonical SIF replacement belongs in this sequence.
