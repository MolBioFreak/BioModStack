# Reviewed redirect transport and immutable weight layouts

This implements byte acquisition/materialization, **not** release approval,
scientific qualification, license acceptance, SIF construction, or activation.
No production model YAML approvals were added. Upstream inventory remains
candidate metadata until reviewed by the release/license owners.

## Registry integration contract

The existing `ModelDefinition.acquisition` authority and
`model_runtime_dependencies(model_id)` remain authoritative. Each entry retains
its original `dependency`, `artifact_id`, immutable source URL, SHA-256, exact
positive size, source authority, approval reference and weight license identity.
Two optional fields are now supported:

* `redirect_policy`: null means **deny every redirect**, including same-origin.
  A reviewed object has exactly `source_url`, `approval_ref`,
  `allowed_authorities` (list of exact lowercase host authorities; no wildcards),
  and `max_hops` (integer 1–5). `source_url` must equal this artifact's original
  source URL. Every redirect destination, including same-origin intermediate
  hops, must be explicitly listed. Policy changes change the manifest/plan
  identity and block reuse of the old acquisition checkpoint under the same ID.
* `member_path`: null retains opaque artifact behavior. A relative POSIX member
  name opts the weights dependency into directory materialization. **All** entries
  of that dependency must then specify distinct safe paths and distinct artifact
  IDs; file/directory collisions, traversal, absolute paths, backslashes, dot
  components and duplicate members fail preflight. Names/components use ASCII
  letters, digits, `_`, `-`, `.` and cannot begin with `.`. Every member has its own
  exact size, hash, source and license. There is no archive extraction.

`preview_model_acquisition()` validates layouts and binds all metadata into the
existing plan digest. `acquire_model(..., expected_plan_digest=...,
accepted_licenses=...)` checks every license before downloads, returns the existing
`artifacts` receipts plus a `layouts` list. A layout receipt contains `dependency`,
`path`, `layout_digest`, a member verification map (hash/size/device/inode),
`qualification: not_checked`, and `test_only: false`.

The transaction/activation caller should map each `layouts[].dependency` to its
`layouts[].path`, **not** an opaque member's content-addressed `runtime.sif` path.
That basename is inherited byte-store machinery and does not classify weights
as SIFs. No active pointer or configuration is changed here. This worker did not
edit CLI or transaction code.

Lower-level library:

```
materialize_weights(dependency, entries, store_root,
                    accepted_licenses=(), test_only=False,
                    attempts=3, timeout=30, total_timeout=300)
```

`entries` is a sequence of `{member_path, manifest}` objects, with `manifest`
being `Artifact` constructor kwargs (including `kind: weights`, excluding
`dependency`/`member_path`). Only a trusted registry adapter may supply it.
Timeout/attempt budgets are per member acquisition, not a whole-model deadline.
Rerun this function at reuse/activation boundaries to revalidate the entire
member set. A receipt alone is not a future filesystem integrity guarantee.

## Transport boundaries

Production transport is anonymous HTTPS on port 443, with verified system TLS
trust and original DNS hostname/SNI. Every resolved address of every destination
must be globally routable; a numeric resolved address is pinned to the socket to
avoid a second DNS resolution/rebinding. Proxies, netrc, cookies, Authorization,
Referer and server-supplied headers are not used or forwarded. Fresh requests
carry only identity encoding and, when resuming, the pinned byte range. HTTP
redirects, private/link-local/loopback destinations, credentials, fragments and
unapproved authorities fail closed. Socket timeouts and the acquisition deadline
bound I/O; OS DNS resolution remains subject to the host resolver's timeout.

Only original source URLs (queries forbidden) and their reviewed policies are
manifest authority. Signed redirect URLs exist only in memory, are not saved in
state/receipts, and network exceptions are sanitized. Every final body is still
checked against the original exact size and SHA-256. Retry/resume starts from the
original source, allowing fresh CDN signatures. This enables reviewed immutable
HF revision URLs without requiring a mirror solely because HF redirects. It does
**not** approve any HF/CDN hostname or source by default, support authenticated
sources, or infer licenses from upstream availability.

## Publication and interruption

Under the existing service-owned store, layouts publish to
`weights/layouts/<dependency.relative_path>/<layout_digest>/`. A private staged
generation and per-layout lock serialize cooperating writers. All path traversal
uses no-follow directory descriptors; file copies are exclusive, not hardlinks.
Exact member sets and individual bytes are revalidated, files/directories become
0400/0500, data/directories are fsynced, and one rename publishes the generation.
The caller receives no layout until the full generation is verified.

Completed member downloads and copies are reused across interruptions. Incomplete
copy temps are discarded (never promoted); interruption after freezing but before
publication also resumes. Corrupt completed members, unexpected files, missing
members, symlinks and hardlinks block rather than silently repair. Existing
published generations are fully rehashed before reuse. These are local POSIX,
service-owned-store guarantees, not protection against root or the store owner
concurrently replacing/chmod-ing arbitrary files. There is no archive parser,
mutable symlink pointer or automatic old-generation deletion.

## Evidence and unresolved release gates

The root tests use real temporary loopback HTTP servers and real filesystem
writes. The explicit `test_only=True` exception permits only HTTP 127.0.0.1 and
segregates all output under `test-fixtures-not-scientific-assets`; production API
entrypoints do not expose it. Fixtures are non-model bytes. Registry API tests
exercise schema, preflight and dispatch; dispatch spies are not transport or
scientific evidence.

Focused commands from `platform/api`:

```
uv run --frozen --group dev python -m pytest ../../tests/test_pinned_acquisition.py ../../tests/test_member_redirect_transport.py --confcutdir=../../tests -q
uv run --frozen --group dev python -m pytest tests/test_runtime_acquisition.py tests/test_runtime_member_layout.py -q -s
```

Pinned SIF build/release approval, the complete reviewed per-member upstream
manifest/redirect policy, license authorization/acceptance and scientific/live
qualification remain unresolved release gates. No large downloads, container
builds, production publication, service changes or deployment were performed.
