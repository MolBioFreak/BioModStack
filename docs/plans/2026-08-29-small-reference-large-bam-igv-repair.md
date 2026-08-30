# Small-Reference Large-BAM IGV Repair Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make persisted-run IGV fast, readable, scientifically explicit, and locally complete for a large alignment against a short reference while preserving the full governed BAM as authority.

**Architecture:** Replace the current request-time record reservoir with a bounded presentation package derived once from exact source authority. The package contains a byte-capped deterministic primary-read preview, a full-source binned coverage track, and an operator-readable receipt. Add an explicit bounded full-source locus-slice operation for detailed inspection. IGV always labels the active source and loads a dedicated sequence track at base scale.

**Tech stack:** FastAPI, pysam 0.23.3, canonical JSON through rfc8785, React/TypeScript, IGV.js 3.7.3, pytest, Node test runner, Vitest.

---

## Controlling behavior

1. The complete source BAM/BAI remains the scientific and package authority.
2. Automatic browser loading is allowed only when source size is finite, positive, and within the browser threshold.
3. Large or size-unknown sources use a derived presentation package.
4. Preview selection operates on unique mapped primary read IDs. Secondary and supplementary records stay out of the global preview.
5. The preview is capped by selected reads, alignment records, and compressed output bytes.
6. The presentation receipt records source package-manifest identity, source BAM/BAI hashes and sizes, policy/runtime versions, selected-read-set digest, selection counts, flag/strand summaries, coverage bin size, and output hashes.
7. First materialization may scan the complete source. BAM/BAI and receipt delivery after materialization must not resolve or hash the complete source again.
8. The global presentation package is durable result authority and is materialized during terminal completion, never lazily by a browser GET. Only locus slices use a bounded cache with explicit byte, entry, and concurrency limits.
9. Whole-reference coverage comes from all mapped primary source records, not the preview.
10. A user-triggered locus operation uses the full governed BAM/BAI, enforces contig/span/read/record/byte/time bounds, and reports selected versus overlapping reads.
11. The UI always shows `Full alignment`, `Primary-read preview`, or `Bounded full-source locus slice` while the track is loaded.
12. Preview coverage is never presented as full-source depth.
13. Base-scale mode displays a dedicated reference-sequence track with visible A/C/G/T bases.
14. Existing Runs reopening, exact-shell fullscreen, resizable workbench, move-source priority, attempt history, signal modes, and package-integrity behavior remain unchanged.

## Task 1: Backend presentation package and receipt

**Objective:** Replace alignment-record reservoir semantics with a deterministic, byte-bounded primary-read presentation package plus full-source coverage.

**Files:**
- Modify: `platform/api/services/ngs_alignment_sessions.py`
- Modify: `platform/api/services/ont_ngs_completion.py`
- Modify: `platform/api/tests/test_ngs_alignment_sessions.py`
- Modify: `platform/api/tests/test_ont_ngs_completion.py`

**RED requirements:**
- Unknown/invalid size fails closed in the browser contract test owned by Task 3.
- A fixture with primary and supplementary records proves the global preview selects unique primary reads only.
- The receipt distinguishes selected read IDs from alignment records.
- Reordered coordinate-equivalent input produces the same selected-read-set digest under the same source authority.
- A tiny byte ceiling reduces the selected read count and keeps the BAM at or below the ceiling.
- Source BAMs above the shared snapshot-cache limit can be streamed through one verified descriptor for presentation generation.
- Full-source coverage includes all mapped primary records and reports exact bin width.
- Cache-hit package resolution does not open or hash the source BAM.
- A presentation GET fails closed when terminal finalization did not materialize the package.
- Terminal finalization materializes every ready primary or dimer-candidate session before publishing completed status.

**Implementation:**
- Add a versioned presentation-policy contract.
- Use stable SHA-256 ranking of `source digest + read ID` to select unique primary reads.
- Hold at most the configured selected-record bound in memory.
- Write candidates deterministically, reduce the selected set when compressed bytes exceed the ceiling, then index.
- Compute bounded binned full-source primary coverage during the source scan and write bedGraph.
- Persist canonical `manifest.json` with complete authority and output metadata.
- Use a no-follow verified source descriptor instead of the shared 2 GiB snapshot for first materialization.
- Publish the durable package atomically under the governed persisted result root and serialize producers with a host-wide file-lock fence.

**Focused command:**
```bash
platform/api/.venv/bin/python -m pytest platform/api/tests/test_ngs_alignment_sessions.py -k 'presentation or preview or coverage or cache' -q
```

## Task 2: Presentation and locus-slice API

**Objective:** Expose typed receipt data, immutable artifact URLs, and a bounded full-source locus slice without request-time full-source hashing.

**Files:**
- Modify: `platform/api/routers/ngs_alignment_sessions.py`
- Modify: `platform/api/services/ngs_alignment_sessions.py`
- Modify: `platform/api/tests/test_ngs_alignment_sessions.py`

**RED requirements:**
- `GET .../presentation` returns a typed ready receipt and digest-addressed BAM/BAI/coverage URLs.
- Preview artifact full, HEAD, conditional, and Range responses use cached output authority without calling session reconstruction.
- Invalid session, source-authority mismatch, unknown artifact kind, oversized locus, invalid contig, excessive read budget, and byte-cap exhaustion fail with typed responses.
- `POST .../locus-slices` returns a deterministic slice receipt with selected and overlapping counts.
- Locus artifact delivery does not hash the complete source after slice materialization.
- Every binary route retains capability/job ownership and exact ETag/Range semantics.

**Implementation:**
- Add closed Pydantic response models for presentation and locus receipts.
- Resolve only an already-materialized presentation package on GET; keep generation in terminal finalization.
- Keep compatibility for the existing preview BAM/BAI paths by resolving them through the new package manifest.
- Add receipt and coverage delivery.
- Add bounded locus-slice preparation, a host-wide two-producer limit, bounded cache lifecycle, and digest-addressed artifact delivery.
- Use source identity captured in the presentation manifest for constant-time unchanged-source admission. Revalidate fully only when device, inode, size, mtime, or ctime changes.

**Focused command:**
```bash
platform/api/.venv/bin/python -m pytest platform/api/tests/test_ngs_alignment_sessions.py -q
```

## Task 3: Typed frontend presentation contract

**Objective:** Make source selection fail closed and expose typed presentation/locus receipt APIs to the viewer.

**Files:**
- Modify: `platform/frontend/src/lib/ngsAlignmentViewer.ts`
- Modify: `platform/frontend/src/lib/ngsAlignmentSession.ts`
- Modify: `platform/frontend/tests/ngsAlignmentViewer.test.ts`

**RED requirements:**
- Missing, zero, negative, and non-finite source sizes never auto-load the full BAM.
- The presentation resolver returns a typed loading/ready/error state and retains full-source download authority separately.
- Track config names clearly distinguish preview, locus slice, and full alignment.
- Sequence-track and full-source coverage configs are present.
- Locus-slice request construction enforces the backend bounds before submission.

**Focused command:**
```bash
pnpm exec tsx --test tests/ngsAlignmentViewer.test.ts tests/ngsFullscreenOwner.test.ts
```

## Task 4: Honest and functional IGV workbench

**Objective:** Display persistent presentation truth, full-source coverage, reference bases, and an explicit full-source locus action.

**Files:**
- Modify: `platform/frontend/src/components/NGSToolkit.tsx`
- Modify: `platform/frontend/src/index.css`
- Modify: `platform/frontend/tests/ngsAlignmentViewer.test.ts`
- Modify: `platform/frontend/tests/vitest/readAndSignalWorkbench.test.tsx` only if mounted behavior requires it

**RED requirements:**
- A persistent badge remains visible after track load and identifies preview/full/locus mode.
- The badge shows selected/available counts, byte size, and policy label for a preview.
- Full-source coverage has its own explicit track name.
- `Load full-source reads for this locus` prepares and mounts a locus slice while preserving the selected range.
- The workbench labels capped locus results.
- `Read bases` mounts a sequence track and proves base letters are visible at the browser acceptance layer.
- Full BAM download is explicit and never automatic.
- Existing resize, fullscreen, persisted-session, and signal-workbench behavior remains intact.

**Focused commands:**
```bash
pnpm exec tsx --test tests/ngsAlignmentViewer.test.ts tests/ngsFullscreenOwner.test.ts
pnpm exec vitest run tests/vitest/readAndSignalWorkbench.test.tsx
pnpm run build
```

## Task 5: Governed runtime denominator and exact-tree review

**Objective:** Bind every changed runtime and test file to the NGS source denominator and reseal runtime authority after implementation stabilizes.

**Files:**
- Modify: `schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json`
- Modify: `platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json`
- Test: `platform/api/tests/test_ngs_molbio_runtime_record_builder.py`

**Steps:**
- Add any newly created source/test files to the denominator.
- Recalculate the denominator content digest.
- Generate the successor runtime record from the exact final source tree with the generated record excluded.
- Run the record-builder regression.
- Obtain fresh specification and quality reviews for the exact candidate.

## Task 6: Integration, Development deployment, and browser acceptance

**Objective:** Fast-forward `test`, deploy through the supported Development owner, and prove the normal persisted-run workflow.

**Gates:**
- Fetch and reconcile current `origin/test` without force.
- Rerun backend alignment-session tests, frontend viewer/fullscreen/workbench tests, production build, runtime-record test, and `git diff --check` after reconciliation.
- Push the exact candidate to `refs/heads/test`.
- Verify local HEAD, `origin/test`, and remote `test` match.
- Run the supported Development sync path.
- Prove API and frontend serve the same revision.
- Open NGS Toolkit → Runs → governed BFX6NB run → Read & Signal Workbench.
- Verify first-load preview receipt, full-source coverage, visible reference bases, persistent preview disclosure, and absence of allocation errors.
- Trigger `Load full-source reads for this locus` through a trusted click.
- Verify the active track changes to a bounded full-source locus slice and reports selected/overlapping counts.
- Confirm panel resizing and exact-shell fullscreen still work.
- Capture readable screenshots and preserve request timing, byte counts, ETags, and artifact digests.

## Acceptance verdict

The repair passes only when the deployed browser provides all of these:

- readable reference bases;
- truthful whole-reference coverage;
- bounded automatic preview;
- persistent preview disclosure;
- explicit full-source locus loading;
- no automatic full-BAM allocation;
- no per-range full-source hashing after materialization;
- exact source/output provenance receipt;
- matching frontend/API/source revision;
- unchanged governed package authority and signal-workbench modes.
