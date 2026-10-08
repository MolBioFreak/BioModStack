# Verified native reads: build and deployment contract

**Status: integrated source / unqualified runtime.** No project import, build, test,
or native execution was performed for this change before the coordinated
full-first-pass gate. The source archive and build-tool lock hashes are authentic
upstream metadata/download hashes, not claims about a built wheel.

## Binding, provenance and installation

`platform/api/vendor/pysam` is a local PEP 517 package, pinned as
`pysam==0.23.3+bms1` in `pyproject.toml` and `uv.lock`. Its backend downloads the
exact upstream sdist identified in `upstream.json`, verifies SHA-256 before
extraction, applies `no-save-index.patch`, regenerates affected Cython bindings,
and delegates compilation to the upstream setuptools build. It does not vendor a
new scientific engine or maintain a second HTSlib implementation. The build
receipt `pysam.bms_native_build.IDENTITY` records the actual source and patch
hashes. No binary/wheel hash exists until that binary is built and qualified.

The binding changes expose `AlignmentFile(..., save_remote_index=False)` and carry
that flag through every iterator reopen using `sam_index_load3`. Upstream's
ordinary default remains unchanged; BMS readers always select no-save. Two small
FASTX binding guards reject a null BGZF open and a BGZF read error rather than
crashing or translating a failed HTTP read to clean EOF. Scientific parsing,
alignment/index interpretation, and FASTX record validation stay upstream.

The Docker API build installs curl, compression headers, and `patch`, then installs
the hashed, exactly pinned build tools before `uv sync --frozen --inexact
--no-build-isolation-package pysam`. `HTSLIB_CONFIGURE_OPTIONS=--enable-libcurl`
requests the transport explicitly. The source hash and patch hash are checked by
runtime feature admission, alongside the binding version, feature descriptor and
`pysam.config.HAVE_LIBCURL`. Stock wheels and a binding without the receipt are
rejected; no local-descriptor fallback exists. The existing Arrow callback path
is unchanged.

After the parent opens the full-first-pass gate, from `platform/api`:

```sh
uv venv .venv
uv pip install --python .venv/bin/python --require-hashes -r vendor/pysam/build-requirements.lock
HTSLIB_CONFIGURE_OPTIONS=--enable-libcurl uv sync --frozen --group dev --inexact --no-build-isolation-package pysam
uv run --frozen python -c 'from services.verified_native_reads import require_runtime; p=require_runtime(); from pysam.bms_native_build import IDENTITY; print(p.__version__, IDENTITY)'
uv run --frozen --group dev pytest tests/test_verified_native_reads.py -m 'not native_http'
uv run --frozen --group dev pytest tests/test_verified_native_reads.py -m native_http
uv run --frozen --group dev pytest tests/test_ngs_alignment_sessions.py tests/test_ont_ngs_workflow_products.py
```

Use the repository's normal test database/environment setup. Installing system
headers, compiling the Docker image, native imports and **all** commands above are
deferred execution, not completed verification. Test fixtures using a lightweight
HTTP server are test-only; production never creates a server per read or request.

## Managed HTTP authority and resources

The existing API listener owns a private ASGI route, intercepted ahead of ordinary
routing, compression, authentication and access logging. A native operation gets
a cryptographically random, ephemeral capability for an exact in-process set of
already-verified generation leases, plus explicitly allowlisted sidecars. Incoming
clients cannot ask it to open a pathname, select a digest, import a new artifact,
or cause another server to start. Private URLs must never be put in public
responses, manifests, receipts, argv, or logs. Child reader URLs go through stdin.
The ASGI path is redacted before server logging; HTSlib verbosity is disabled.

Set these system-owned settings for a supported deployment:

* `BMS_NATIVE_READ_ORIGIN`: literal loopback HTTP(S) origin for the **same API
  process**, e.g. `http://127.0.0.1:8000`. No hostname, userinfo, path, query or
  fragment is accepted. Non-local deployment is intentionally not admitted: a
  remote proxy/DNS redirect or unreachable remote C connect cannot currently
  satisfy this implementation's cancellation/credential contract.
* `BMS_NATIVE_READ_TRUST_LOOPBACK=1`: explicit acknowledgement that plain-HTTP
  loopback is a trusted process boundary (required for HTTP, not HTTPS).
* `BMS_NATIVE_READ_THREADS`: positive producer CPU-thread reservation.
* `BMS_NATIVE_READ_DRAM_BYTES`: positive transport buffer reservation. Concurrent
  stream admission is derived from this reservation and the existing verified
  chunk size, not an artifact-size ceiling or an arbitrary request count.

No defaults silently take compute or memory away from other workloads. Missing
configuration leaves native reads explicitly unavailable; invalid configured
resources fail startup. These are infrastructure controls, not scientific
parameters. The existing shared CPU/DRAM/disk ledger admits the transport pool,
cache imports/receipts, native compute, and generated-output headroom. Nested
readers reuse an existing compute reservation rather than double-charging CPU or
claiming another unbounded DRAM remainder. Idle cache generations retain their
existing accountable leases; actual capacity pressure remains a typed failure.

The producer executor is separate from native consumers' worker threads, so a
blocking HTSlib client cannot exhaust the workers needed to serve itself. Async
native work propagates cancellation through a scoped event, revokes its grants,
closes active responses, and waits for the native worker before releasing
resources. Child timeout/error paths terminate/reap readers before retiring the
grant. Grant closure waits for shielded producer work and independent read leases
to quiesce. Chunk verification failures poison the original operation even if a
native engine swallowed an I/O failure. No mutable cache descriptor escapes.

HTTP supports GET/HEAD, full reads, one bounded/open-ended/suffix byte range,
416 with current length, and rejects duplicate/multipart/malformed ranges. It
streams only immutable verified `bytes`, with no artifact-sized RAM image.
Optional sidecar probes outside the allowlist get 404. Wrong-worker capability
replay gets 404. The route has no ambient-user authority; proxy credentials,
HTSlib auth/header-file credentials, and conflicting ambient proxy settings are
rejected at startup. BAM and variant format guards reject htsget dispatch before
native autodetection can follow byte-specified external URLs. CRAM external
reference resolution is not supported.

## Deployment qualification (required, not claimed)

* Use one API process per configured origin. Multiple workers behind an
  unpartitioned listener are unsupported: capabilities are process-local, and
  accidental routing to a different worker fails closed. Do not use a shared
  reverse proxy or load balancer for this private origin.
* Build and qualify on each actual interpreter/platform/image. Current metadata
  resolution is not evidence that a platform compiled libcurl or passed tests.
* Exercise initial and `multiple_iterators=True` BAM index opens with absent,
  hostile and symlinked CWD sidecars; assert no sidecar is created or changed.
  Compare indexed/whole-file traversal, non-default contigs, M5/reference binding,
  FASTA/FAI, FASTX, VCF and explicit BCF/CSI, and both stdin-fed child seams.
* Exercise cancellation before grant, during native read, during response
  backpressure and during a child operation; assert worker/producer quiescence,
  cache lease counts, and global CPU/DRAM/disk release. Stress simultaneous
  readers with producer threads independent from exhausted consumer threads.
* Inject same-size cache mutation, replacement, truncation and corruption during
  delivery, and prove the operation cannot report success. Verify unauthorized
  sidecars, expired/wrong-worker tokens, invalid Range, disconnect and exhaustion.
* Inspect server and child logs for capability leakage; qualify direct loopback
  listener connection cancellation. No credentials or authorization headers may
  appear in native requests. Retain target evidence before enabling production.

The old SIF/samtools descriptor reader and sealed-memfd mirror are retired from
these API read paths. Existing bounded BAM writer/indexer subprocesses remain,
but their input is the managed verified transport and their output is still the
admitted private workspace. Semantic reference-binding regressions now mock the
supported native boundary; obsolete descriptor-runtime tests are replaced by
transport/binding qualification definitions.
