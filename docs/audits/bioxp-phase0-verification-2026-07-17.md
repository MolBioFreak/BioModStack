# BioXP Phase 0 verification — 2026-07-17

**Authorization:** Phase 0 completed. Phase 1 was separately authorized after this capture; phases 2–8 have not started.

## Repository state

- Authorization baseline: branch `test`, HEAD `a69711f7e55786f3867e3952b546b3d6b8c48c11`.
- Current captured tree: branch `test`, descendant HEAD `c8d14c223cc7d8107048f7a466c726f18d6cdcab`.
- Concurrent HEAD movement: one descendant documentation commit; it contains no Phase 0-owned path.
- Current dirty entries at capture: **213**; this includes unrelated concurrent shared-tree work.
- Phase 0-owned files: **19**.
- Implementation fingerprint: `83c3ec1683620803df240b12e00133e5feba12a652d10eb57246682482def2d0`.
- Owned-artifact fingerprint: `424d643c24508243e3c42e34cd50f62f2fbdf369ffec0999f13b96e5cd8e819f`.
- Canonical verification-record checksum: `1226c1c260030acc755f5a7b4546c3f405b59db991c6b1ce27d8c187dc65e93b`.
- Verification JSON file SHA-256: `ea3e18a305c830289a77fa01762e4165af0acaf90aed9e50a1b98518189b5519`.

## Final Phase 0 gates

| Gate | Final result |
|---|---|
| Automatic namespace bootstrap | Normal `uv run pytest` invocation exits 0 |
| Default network-policy contract | **29 passed, 2 skipped in 0.03s**, seed `20260723` |
| Exact live dual opt-in | **2 passed in 0.02s**; INET construction only |
| Live endpoint containment | `connect`, `connect_ex`, `bind`, `sendto`, DNS, and child processes denied; route-free namespace retained |
| Child-process bypasses | `Popen`, shell/spawn/exec, `fork`, `forkpty`, and multiprocessing-fork probes denied |
| Focused seeded BioXP/API lane | **139 passed, 2 skipped in 7.78s**, seed `20260723` |
| Process-tree network trace | Traced rerun **139 passed, 2 skipped in 8.81s**; `AF_INET=0`, `AF_INET6=0`, `AF_UNIX=228`, `AF_NETLINK=0`, `AF_PACKET=0` |
| Lifecycle state restoration | Session and linkage globals restored |
| Static gates | Lock, Ruff, `py_compile`, and scoped diff checks passed after final edits |
| Manifest checks | Normal and temporary full-dirty-tree-index checks passed; policy self-reference regression passed |
| Isolated frontend build | Previously passed; 4,619 modules transformed |

The last broad API and frontend test observations remained separately attributed dirty-tree failures: three unrelated API failures and one unrelated ESMFold2 naming-contract failure. Those observations are disclosed in the JSON and are not represented as fresh post-edit full-suite passes.

## Deterministic inventory

- BioXP routes: **145**.
- Frontend client exports: **169**.
- Unconsumed frontend exports: **50**.
- Direct BioXP endpoint literals outside the client: **0**.
- Root temporary BioXP scripts: **7**.
- Tracked runtime references to those scripts: **0**.

## Review disposition

Independent review was explicitly **waived by Christian on 2026-07-17 for Phases 0 and 1**. The last independent pre-waiver verdict was **NOT APPROVABLE**. Its findings were remediated and the implementation/verification gates above passed, but this record does **not** claim an independent approval verdict.

**Phase 0 disposition: ACCEPTED BY EXPLICIT USER REVIEW WAIVER, WITH IMPLEMENTATION GATES PASSED.**

## Deferred host evidence

**NOT RUN** — the BioXP Linux runtime remained offline. Listener ownership, installed units, launch behavior, readiness, hardware behavior, reboot recovery, and real integration are deferred to a separately authorized maintenance window.

No robot host was contacted; no server/service was started; no temporary BioXP motion script was imported, executed, moved, or deleted.
