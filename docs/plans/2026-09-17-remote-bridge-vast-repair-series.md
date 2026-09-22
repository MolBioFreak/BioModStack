# Remote bridge repair series: Vast first, shared execution core

## Review branch and status

**September 20 correction:** this document records the original four-commit
candidate. For the R1-R6 disposition, corrected commands, cost decision, and
freeze/bind verification procedure, see
[the worker-review closeout](2026-09-20-remote-bridge-review-closeout.md).

Prepared for manual verification on `test-remote-bridge-repairs-20260917`, based on
`test@9ad10eea97cf784da47b07d6d9b2896da35e455e`. This branch is separate from the
canonical auto-synced Development branch. Creating this review branch does not
modify `test`, `main`, managed services, provider instances or scientific data.

The first three commits implement the reviewed provisioning fixes:

| Commit | Change |
| --- | --- |
| `ef51f647d11931e364955e12838b5570b9cbc4e5` | Isolate provisioning and observation fences per worker. |
| `2b9db518b8d59d000dd269b88d129e1a1022f34c` | Admit only additional verified provisioning bytes. |
| `c4d9688e605b75bd0d49c4e891b0ea7334b92be2` | Preserve allowlisted helper failures in preload progress. |

This document is the fourth commit. The large-document by-reference repair in
`072edca` and its baseline source record in `9ad10ee` are retained. Request size
remains 8 MiB and referenced documents remain bounded at 64 MiB. No selected
scientific settings, model implementations, GPU assignment, batching, result
return authorization or provider lifecycle policy is changed.

**At the original `f953aeec` tip, the runtime-source record had NOT been regenerated.**
The inherited `platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json`
contains the baseline source binding. Review and focused unit tests can proceed;
source-gated live execution and integration require the companion generation in
section 7. Do not bypass source verification or interpret this branch as deployed,
release-ready or scientifically accepted.

## 1. Pull into a separate worktree

Run from an ordinary clone, not by editing the managed Development checkout:

```bash
git fetch origin
git worktree add -b review/remote-bridge ../BMS-remote-bridge-review \
  origin/test-remote-bridge-repairs-20260917
cd ../BMS-remote-bridge-review
git log --oneline 9ad10eea97cf784da47b07d6d9b2896da35e455e..HEAD
git diff --check 9ad10eea97cf784da47b07d6d9b2896da35e455e..HEAD
```

Use the repository's locked environment, from `platform/api`:

```bash
uv run --frozen --group dev python -m pytest \
  tests/test_remote_provision_isolation.py \
  tests/test_remote_incremental_admission.py \
  tests/test_remote_helper_diagnostics.py \
  tests/test_remote_inventory_supersession.py \
  --junitxml=/tmp/bms-remote-bridge-focused.xml
```

Then run the owning subsystem regressions:

```bash
uv run --frozen --group dev python -m pytest \
  tests/test_managed_runtime_safety.py \
  tests/test_ngs_molbio_runtime_record_builder.py \
  tests/test_remote_managed_inventory.py \
  tests/test_remote_preloading.py \
  tests/test_multiworker_scheduling.py \
  tests/test_multiworker_migration.py \
  tests/test_artifact_cache.py \
  tests/test_cache_batches.py \
  tests/test_remote_cache_integration.py \
  tests/test_remote_runtime_images.py \
  tests/test_managed_ssh_transport.py \
  tests/test_vast_ssh_endpoint_selection.py \
  tests/test_remote_telemetry.py \
  --junitxml=/tmp/bms-remote-bridge-regressions.xml
```

The authoring environment ran 64 isolated checks of the proposed predicates,
accounting and diagnostic code. Those checks use fixture dependencies and are NOT
execution of the complete frozen BMS test environment. Source-context patch and
syntax checks were performed; no live worker or scientific run was performed.
The commands above remain manual acceptance tasks. Do not infer failed or absent
Development testing merely from a lack of GitHub Actions results.

## 2. Official Vast documentation and provider boundaries

Official references reviewed for this work, including a September 18, 2026
recheck of SSH, instance pagination and local-volume constraints:

| Official source | Design implication |
| --- | --- |
| [SSH connections](https://docs.vast.ai/guides/instances/connect/ssh) | Direct and proxy routes are distinct. Direct requires open ports. Retain strict host identity and managed-key checks; do not switch routes to evade authentication or changed-key failures. |
| [Show instances](https://docs.vast.ai/api-reference/instances/show-instances) | The documented v1 inventory API is paginated, with at most 25 instances per page. A page or cursor-bearing partial response is not complete fleet inventory. |
| [Storage types](https://docs.vast.ai/guides/instances/storage/types) | Container disk allocation is fixed at creation; stopping preserves data while storage billing continues, and destruction removes instance data. Do not repair accounting by destroying or silently replacing a worker. |
| [Local volumes](https://docs.vast.ai/guides/instances/storage/volumes) | Local volumes are tied to one physical host; the documentation excludes VM instances. Do not promise interchangeable cross-rental or cross-worker caches. |
| [Virtual machines](https://docs.vast.ai/guides/instances/virtual-machines) | VM and container rentals are different execution environments. Observed backend qualification remains authoritative. |
| [Managing instances](https://docs.vast.ai/guides/instances/manage-instances) | Provider running, scheduling, connecting and stopped states are separate from BMS attachment, runtime readiness and current capacity evidence. |

Keep discovery, ownership/status normalization and endpoint selection in provider
adapters. Keep content verification, incremental storage accounting, transport
ownership, telemetry freshness and native result handling shared. Vast is the
initial provider; this series neither implements nor advertises other adapters.
Future providers must pass the same execution/ownership/import conformance suite
plus their own documented discovery and lifecycle tests.

## 3. Worker-isolated admission and inventory observation

Files: `platform/api/services/remote_execution/preloading.py` and
`platform/api/tests/test_remote_provision_isolation.py`.

Remove the fleet-wide `NOT EXISTS (... state = 'probing')` predicate. Worker A
must not be rejected merely because worker B is attaching. Preserve A's own
active/ready state, lease, preload state, endpoint/key identity and fresh provider
inventory requirements.

Separate invalidating an observation from admitting new work. A failed read must
be able to invalidate its own predecessor even if a lease begins or provider
inventory expires during the read. Bind successful publication and failure
invalidation to the exact prior managed inventory, endpoint, activation time and
preload operation. A late failure must not invalidate a newer observation or
replacement attachment. Report supersession rather than claiming newer evidence
was made stale.

The implementation uses the existing SQLite JSON and compare-and-swap patterns.
It removes a fleet-wide rejection, not every possible source of serialization:
`PreloadController.lock` remains unchanged. Do not claim unrestricted parallel
operations based on this patch alone.

Tests cover unrelated-worker states, own-worker conflicts, stale inventory,
invalidation after admission eligibility changes, new observations, replacement
attachments/endpoints/operations, absent prior observations and shared success/
failure generation identity. Integration must additionally race actual controller
sessions: attach B while provisioning or failing inventory refresh on A, and
verify that one process cannot invalidate a subsequent observation from another.

## 4. Incremental verified storage admission

Files: `platform/api/tools/bms_managed_runtime.py` and
`platform/api/tests/test_remote_incremental_admission.py`.

Additional payload accounting follows the existing upload and install path:

`2 x uncached unique content objects + each missing/unverified non-image destination`

Existing metadata allowance and minimum headroom are added afterwards. Two copies
per uncached object conservatively cover incoming upload and atomic CAS
publication. Duplicate content is acquired once, but distinct installed
locations each need their own copy allowance. Runtime images remain shared
references; links are generated metadata rather than uploaded cache objects.

Credit reuse only after `Cache.probe` verifies content and the existing `observe`
verifier checks installed members. A valid installed destination cannot substitute
for a missing cache object because the controller still stages the selected cache
object set. Refuse conflicting sizes, mismatched observation identities, unknown
states and corrupt shared-image evidence.

Preserve admission locking, boot and filesystem fences, immutable activated
release protections, metadata/headroom and install-time free-space checks. This
is a point-in-time preflight, not a reservation. Competing writes or quota changes
can still cause a safe later failure.

Tests cover cold, cache-warm, installed and installed-without-cache cases,
corruption/incompatibility, deduplication, links, image namespaces, unknown or
mismatched evidence, headroom, warm retries and an 88,037-destination metadata
case. Integration must run real helper admission with verified fixture bytes,
interrupted upload/retry and disk exhaustion after admission. Measure extra
hashing/readback cost on the real dependency closure; do not replace verification
with size or modification-time trust for speed.

## 5. Safe typed helper diagnostics

Files: `platform/api/services/remote_execution/transport.py`,
`platform/api/services/remote_execution/preloading.py` and
`platform/api/tests/test_remote_helper_diagnostics.py`.

Accept only a bounded final JSON error envelope containing exactly `state` and
`error`, state `failed`, no duplicate keys and a code in the closed seven-code
allowlist. Raise `RemoteHelperError`, a subtype of `RemoteTransportError`, with a
fixed safe operator message and stable code. Preload failure publication carries
that safe message rather than reducing every case to a generic retry instruction.

Raw stderr, worker paths, credentials, request data and acquisition URLs do not
become operator display authority. Unknown errors retain generic handling. SSH
exit 255 retains authentication-failure precedence. Cancellation and unconfirmed
remote quiescence retain existing recovery ownership behavior.

Distinguish request/document size limits, invalid references, unavailable
staged documents, integrity mismatch and incompatible document shapes. Integrity
failure requires investigation rather than automatic retry advice. A retained
recovery fence still takes precedence over ordinary retry messaging.

Tests cover all seven codes, malformed/oversized/duplicate-key or extra-field
envelopes, secret-bearing preceding stderr, transport mapping, authentication
precedence, cancellation and safe fallback. Integration must verify each safe
message persists across actual API/UI reload, without exposing worker content,
and separately exercise helper failure followed by uncertain cleanup.

## 6. Follow-on commits: explicitly not implemented here

### Repeated slow telemetry

Proposed subject: `feat(remote): stream bounded telemetry over one managed SSH session`.

The current separate 30-second connection and six-second probe budgets solve the
original timeout bug. Repeating a 24-second handshake cannot continuously satisfy
a 20-second freshness window. Moving the next poll earlier is not a complete
solution. Do not relabel stale observations as fresh or substitute local GPUs.

Design a lifespan-owned, bounded-duration sampling session over the existing
managed strict SSH transport, not ambient multiplexing. Bound messages and
sequence numbers; verify target, attachment, boot and timing; specify buffered
sample staleness, teardown, renewal and finite reconnect/backoff. Test delayed
initial connection followed by repeated samples, network stalls, buffering,
restart, reboot, endpoint changes and cancellation on actual Vast routes.

### Attachment recovery ownership

Proposed subject: `fix(remote): retain attachment ownership until installer quiescence is proven`.

Persist attachment operation generations, identity/boot binding, cancellation
intent and verified terminal/recovery state. Block overlapping successors while
predecessor ownership is uncertain, and expose a usable explicit recovery action.
This is a source-review concern; no surviving live installer was demonstrated.

Do not blindly wrap the whole installer in `_provision_transport.py`: that wrapper
explicitly supports reviewed commands that do not daemonize or escape their
session. First qualify package-manager and service behavior for each supported
worker environment. Do not require a new interpreter before the bootstrap step
that installs it. Failure injection must cover root checking, package install,
critical-runtime upload/activation, repeated API restarts, reattachment races,
SSH loss during cancellation and changed boot/endpoint. Unknown ownership must
not become false-ready or permit overlapping installers.

### Bounded complete Vast inventory

Proposed subject: `fix(vast): traverse bounded v1 inventory pages before publishing presence`.

The adapter currently refuses cursor-bearing responses. Implement documented
provider-specific pagination with finite pages/bytes/deadlines and bounded
rate-limit handling. Validate uniqueness, cursor progression, counts and final
completeness. Never publish partial pages as complete ownership evidence or use
an inconsistent scan to deactivate missing targets. Preserve selected authenticated
endpoints and leases; exclude raw provider secrets/cursors from public diagnostics.
Test 0, 1, 25 and 26+ instances, second-page failures, 429, duplicate/repeated
cursors, changing totals and endpoint drift. This is not evidence pagination
caused the current small-fleet failure.

## 7. Mandatory generated-source companion before live validation

Proposed subject: `chore(dev): bind runtime authority to reviewed bridge repairs`.

Do not hand-edit the runtime implementation record, invent Git identities, weaken
source checks or present a source binding as test/scientific acceptance. After
review, use the existing builder against an exact frozen record-free source
commit. Its supplied raw commit object must reside outside the frozen snapshot;
the snapshot must contain no virtual environments, bytecode, test output or logs.

The following is an explicit local preparation step for the isolated review
worktree after review, not authorization to push to canonical `test`:

```bash
set -eu
test -z "$(git status --porcelain)"
record=platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json
locked_python=$(cd platform/api && uv run --frozen --group dev python -c 'import sys; print(sys.executable)')
git rm "$record"
git commit -m "chore(dev): freeze reviewed remote bridge source"
source_commit=$(git rev-parse HEAD)
source_tree=$(git rev-parse 'HEAD^{tree}')
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/source"
git archive --format=tar "$source_commit" -o "$scratch/source.tar"
tar -xf "$scratch/source.tar" -C "$scratch/source"
git cat-file commit "$source_commit" > "$scratch/commit-object"
PYTHONDONTWRITEBYTECODE=1 "$locked_python" \
  "$scratch/source/scripts/build_ngs_molbio_runtime_implementation_record.py" \
  --successor-source-commit "$source_commit" \
  --successor-source-tree "$source_tree" \
  --successor-commit-object "$scratch/commit-object"
cp "$scratch/source/$record" "$record"
git add "$record"
git diff --cached --check
git commit -m "chore(dev): bind runtime authority to reviewed bridge repairs"
```

The builder verifies the commit object, tree, denominator and source bytes.
If it refuses the snapshot or the locked interpreter lacks a declared dependency,
stop and resolve that issue; do not manually forge the output. Retain unverified
acceptance flags. Reconcile with current `origin/test` only when integration is
authorized, rerun affected tests and regenerate source authority for the actual
reviewed integrated source. Never force-push or retarget the managed dev sync to
this review branch merely to try it.

## 8. Live acceptance and non-goals

With an explicitly authorized worker and isolated Development state, record exact
API/frontend/source/runtime identities, provider/target/boot IDs and approved
workflow settings without secrets. Verify cold and warm provisioning of the
production-shaped dependency selection, interrupted transfer/retry, worker
isolation, remote launch, retained outputs, explicit authorized retrieval and
native result import/viewing. No scientific payload may be fetched merely by
polling; failed attempts must not be finalized as successful.

Exercise SSH loss, API restart, integrity failures and disk exhaustion. Show
observed free space and additional required bytes without silently evicting,
resizing, stopping, destroying or replacing provider instances. Test telemetry,
attachment and pagination follow-ons independently before calling them fixed.
No additional provider support, universal workflow acceptance, live deployment or
complete remote-bridge 1.0 certification is claimed by this review branch.
