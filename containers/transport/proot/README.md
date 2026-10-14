# PRoot read-only canonical-image transport correction

This is a small patch to the existing udocker PRoot owner, **not** a udocker
upgrade, a guest-UID change, a new admission gate, or an image-guard exception.
The normal udocker P1 `-0` execution mode remains in use.

## Owner and defect

The pinned udocker wheel is 1.3.17 and englib is 1.2.11. On modern x86_64 kernels,
udocker selects `proot-x86_64-4_8_0`. The original installed engine's SHA-256 is
recorded in `pins.json`; its ELF Build ID is
`2a3edbd1a9c36c9a297c2b17b58c671ec0110c76`. It is static and includes DWARF for
`/proot/src/extension/fake_id0/fake_id0.c`, built with Red Hat GCC 9.3.1.

The owner is `jorge-lip/proot-udocker`, not a replacement PRoot implementation.
The englib 1.2.11 build-history entry identifies branch `udocker-4`; this patch
pins that branch's commit `fe64598e08ca3b4e9884c4cf8ecb83f6dc9720b8` and its
uthash submodule. The original engine does not expose a Git commit in its
version banner; source lineage and matching DWARF are evidence, not a claim
that the original Fedora build was reproduced byte-for-byte.

`fake_id0.override_permissions` adds owner read **and write** permission before
translated path operations, then restores the mode. Tracing the exact original
engine copied from the worker showed a read-only open of a 0400 file performing
`chmod(..., 0600)` followed by `chmod(..., 0100400)`. Both succeed; ctime changes.
No supported option in that engine/udocker owner was found that fixes this
while preserving fake-root emulation. `UDOCKER_USE_PROOT_EXECUTABLE` is an
existing supported selection mechanism, used only for initial isolated probes.
The final qualified installation uses normal pinned-engine selection.

The patch narrows permissions for read-only `open`/`openat` and stat-family path
lookups. Read-only operations need search permission on traversed directories,
and read permission on the final node, not write permission. Inaccessible reads
still receive fake-root permission emulation. Mutating operations, including
`O_TRUNC` and `O_CREAT`, retain their existing behavior and mode restoration.
The final patched engine's traced inert read performs **no chmod**.

## Canonical attestation

A second transport issue was exposed by running actual attestation: binding
only the file creates a synthetic guest parent rather than the canonical 0500
object directory. `bms_container.py` now binds the **single canonical object
directory** at its unchanged canonical path. It does not bind the whole store
or sibling derived rootfs directories. This preserves both file identity and
its real parent, as required by `prepare_runtime_image_attestation.py`.
The attestation implementation, ctime checks, source leases, derived receipts,
quiescence and scientific code are unchanged.

## Rebuild and pins

```sh
python containers/transport/proot/build.py --output /absolute/scratch/proot-rebuilt
cmp containers/transport/proot/proot-x86_64-4_8_0 /absolute/scratch/proot-rebuilt
```

Requires Linux x86_64, Git, Docker and network access for pinned source/toolchain
acquisition. `Dockerfile` uses a digest-pinned Debian base and frozen Debian
snapshot. Compilation runs without network at fixed `/proot`, with a fixed
version string and source epoch. It disables the optional Python extension
(which is not used by the original engine) and strips debug information only.
`pins.json` verifies source patch and final artifact; the installer independently
pins the artifact and complete installed manifest. Three clean source builds
produced identical artifact bytes.

Qualified toolchain: GCC 12.2.0-14, binutils 2.40-2, libc6-dev
2.36-9+deb12u9, libtalloc-dev 2.4.0-f2. This is a reproducible rebuild of the
pinned owner plus the one patch, not the original Fedora toolchain. The older
kernel `proot-x86_64` binary remains untouched and is not covered by this fix.

PRoot is GPL-2.0-or-later; see `COPYING` and upstream source headers. The pinned
uthash source retains its BSD notice. Static libc/talloc and GCC runtime sources
and license notices are available from the frozen Debian snapshot used by the
build (glibc LGPL-2.1-or-later, talloc LGPL-3.0-or-later, GCC runtime exception).
The complete recipe supports rebuilding/relinking those components.

## Supported installation path (not performed on the live worker)

The critical-runtime producer bundles this artifact as
`lib/proot-x86_64-4_8_0`, alongside the changed bootstrap and container launcher.
Its normal managed installation invokes bootstrap's udocker mode, which verifies
upstream archives and the bundled engine and publishes a **new** tool directory:
`<worker-root>/tools/udocker-1.3.17-bms1`. Existing tool directories are not edited.
`bms_managed_runtime.container_environment` selects that directory.

A controller-only update is insufficient. Deployment must include these source
and artifact bytes, followed by the supported managed critical-runtime
installation/qualification and target binding/attachment flow. Do not manually
replace the engine in an existing release. Previously stale image-derived
receipts are a separate recovery concern; this patch neither edits nor blesses
them. No push, asset publication, deployment, target reattachment, cache repair,
scientific Job or rental action is part of this candidate.

## Verification

- `tests/test_proot_readonly.py` executes the shipped binary: both open ABIs,
  repeated read-only opens/stat, file+parent identity, fake UID/setuid emulation,
  reads of inaccessible files, mutating opens/mode restoration, real canonical
  attestation and same-byte metadata-mutation rejection.
- Installer tests use the independently downloaded pinned wheel/englib archives:
  exact installation, offline repeat, and rejection of corrupted engine bytes
  even with a rewritten manifest. Managed packaging/attachment tests also pass.
- On the exact BC target, using task-owned stores/workspaces and the original
  MD preparation SIF bytes: cold read, repeated warm reads, real in-container canonical
  attestation, and two concurrent shared-image attestations all exited zero with
  unchanged file **and parent** identities. Fake root remained UID 0.
- An intentional write-and-restore completed its body but was rejected with exit
  125 by the existing source guard; same-byte attestation and subsequent stale
  derivation selection also rejected it. Separate byte corruption was rejected
  by actual SHA-256 attestation. No guard was modified.
- Both task stores had zero leases and no private views before removal. The
  original shared SIF identity was unchanged throughout; isolated remote files
  were removed and absence verified.

Evidence from this qualification is retained by the operator, not required as a
runtime gate. The broader existing inspect-file suite has two failures also
reproduced at the unchanged base revision (expected hash-call count and its
hash-time mutation injection); they are outside this patch.
