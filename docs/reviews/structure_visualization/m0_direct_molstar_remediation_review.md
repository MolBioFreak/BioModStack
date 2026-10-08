# M0 Direct Mol* Runtime Remediation Review

**Date:** 2026-07-19
**Decision:** **GO — source/runtime contract remediation complete**
**Current-tree execution:** intentionally not performed by operator instruction

## Supersession

This decision supersedes the `STOP` in `m0_runtime_contract_review.md` for the retired PDBe embedding. The STOP remains historical evidence of why that embedding was removed; it is not the active production decision.

## Remediated contract

- Production owns direct `molstar@4.5.0`; no PDBe custom element or private-instance lifecycle remains.
- `MolstarEngineOwner` owns each React root and plugin attempt and disposes both deterministically.
- `StructureViewerHost` is the single public composition boundary.
- `MolstarEngineAdapter` is the engine boundary; consumers do not receive `PluginUIContext`.
- Scene generations own documents, presentation, selection, filters, camera, measurements, events, and resources.
- Browser fixtures receive diagnostics-only probes; the production adapter and plugin are not exposed.
- Process-global quality-query registration remains idempotent.

## Retained browser baseline

The retained accepted baseline predates the current edits and is cited only as baseline evidence, not as execution of this tree:

- 55 lifecycle cycles
- warnings: 0
- listener growth: 0
- retention failures: 0
- final plugins: 0
- final canvases: 0
- evidence: `evidence/m1_direct_molstar_runtime_probe_final_chrome150.json`
- evidence SHA-256: `1a93c222ee4077766473c23d86a224d10fe75b99994e1df773bb5484f5aa2607`

## Current source identities

- `MolstarEngineAdapter.ts`: `f378811c1f10bd317171c7fcd7a611aa3b32d52b880852da5688b1833e19906a`
- `StructureSceneController.ts`: `6695d5a911bfd661db830925f6973719e2cfc33893ecfda221a9b4e28e736266`
- `MolstarDirectSceneEngineAdapter.ts`: `1485c0d5267ca095448035cf6a08bedca4ee81af509103bb01cced23ffd563fc`
- `StructureViewerHost.tsx`: `55ea043e50802e279c19ed68837515c3713063d68b62561a200bd2e6f567023d`
- `MolstarDirectAdapter.ts`: `489906b80290f3f32c35dabc4e6fb9f7f4788433289a589f2fc70839d9bfa069`

## Verification boundary

`git diff --check` passed. No test, typecheck, build, browser probe, or route acceptance was run against these hashes, because the operator explicitly required code insertion while skipping all testing. The GO decision is therefore a source/runtime-contract decision, not a claim of newly executed browser evidence.
