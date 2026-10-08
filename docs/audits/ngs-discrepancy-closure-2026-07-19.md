# NGS discrepancy closure — 2026-07-19

## Scope and exact source identity

This closure pass started from clean fetched `origin/test` commit
`8ac5d61967f0d6c5f5252230b94570623aa2aabf`, tree
`aa4f5f2b409e672f3af52e8a0673217a634c364a`. Commit `3d67db4b53d1bc7eda4bf9e6c1aa0db5772a6e84`
is an ancestor of that exact remote tree.

The three discrepancies below are resolved separately. A workflow exit code is
never promoted to a scientific result.

## 1. Exact current-tree NGS regression matrix

The current remote tree was materialized in a clean isolated worktree. The
combined API matrix covered 22 ONT/NGS, construct-verification, alignment,
security, runtime, and cache-hygiene test modules.

- API: **303 passed, 0 skipped, 0 failed** in 63.368 seconds.
- Frontend: TypeScript build **PASS**; **389 passed, 0 failed** across five
  suites; production bundle **PASS**, 6,010 modules transformed in 34.47
  seconds.
- Start/end commit, tree, and tracked/untracked status seals were identical.

This supersedes the stale claim that the NGS regression matrix had not been run
on the post-`3d67db4` remote lineage.

## 2. FAST5 conversion/rebasecall status

### Provenanced fixture found

The local EPI2ME `wf-basecalling` 1.5.7 demo package contains three real FAST5
files and a bundled checksum manifest. The first fixture was used here:

- path: `wf-basecalling-demo/fast5/PAM14583_ee1ba89e_188.fast5`
- bundled MD5: `1f11457576852eac4a24e3108ff07ac6`
- verified SHA-256:
  `a7dd49537c984adb1e198b9c3d93ff3c5bcffd02d722c87d7175a3e1e2a5dc35`
- size: 140,528,906 bytes
- format: HDF5
- official workflow tag inspected: `v1.5.7`, commit
  `35f5dbc7ecc95a97f1403467ce3e14780d925aec`

Official `pod5` 0.3.35 converted the fixture successfully:

- output reads: 1,116
- output size: 121,422,400 bytes
- output SHA-256:
  `376cb570b2685a706619adff46e61d181a3334307dabe02cafdee4cab7817260`
- chemistry/run metadata: FLO-PRO114M, SQK-LSK114, R10.4.1 E8.2 400 bps,
  4,000 Hz sample rate.

### Why current rebasecalling must reject it

The production Dorado SIF is 1.3.1. Its available v4.2+ R10.4.1 models are
5,000 Hz models. A real GPU invocation against the converted 4,000 Hz fixture
failed before basecalling with:

`Sample rate for model (5000) and data (4000) are not compatible.`

This is correct scientific fail-closed behavior, not a missing-fixture problem.
Dorado 1.0+ dropped 4 kHz model support; legacy 4 kHz data requires a separately
pinned legacy Dorado 0.9.6 + v4.1.0 lane. That legacy lane is not part of the
current BioModStack runtime and is not silently substituted.

BioModStack therefore now:

- advertises canonical basecalling as POD5-only;
- removes FAST5 from device-to-analysis handoff capability metadata;
- rejects FAST5 and every workflow-incompatible handoff before scheduling;
- keeps the canonical workflow registry POD5-only;
- capability-gates `--emit-summary` because active Dorado releases differ;
- probes completed help output without a `pipefail`/SIGPIPE false negative (validated
  as unsupported on host Dorado 1.1.1 and supported on production SIF Dorado
  1.3.1);
- never reports the optional sequencing-summary path as a completed stage output
  when no summary file was emitted.

**Disposition:** FAST5 is explicitly unsupported by the current runtime rather
than falsely “pending because no fixture exists.” Conversion is independently
validated; rebasecalling is rejected when model/sample-rate compatibility is
not satisfied. No FAST5 acceptance claim is made.

## 3. BC114 scientific FAIL

The exact current verifier was replayed against the retained BC114 final9
artifacts. It reproduced:

- execution: `SUCCEEDED`, exit 0;
- scientific verdict: `FAIL`;
- reasons: `MIXED_ALLELES_DETECTED`, `TOPOLOGY_EVIDENCE_INSUFFICIENT`,
  `VARIANTS_DETECTED`;
- variant: position 9, `A -> AT` insertion;
- exact inserted-`T` support: 246/264 = 0.9318181818181818;
- identity: 0.9984779299847792;
- replay manifest SHA-256:
  `2810d3ebbad80cc649e8b79100dfd2c82a8c3564e8441db764e40432d4155a87`.

An independent primary-BAM CIGAR/read audit found:

- 246 exact one-base `T` insertions;
- forward support: 141 reads;
- reverse support: 105 reads;
- inserted-base median Q score: 30;
- other insertion alleles: one `TT`, one `TTT`, and one `TAT`.

The variant is therefore neither a strand-only artifact nor an aggregate
allele-counting bug. The deposited Sanger reference selected the ONT subset, so
this dataset cannot independently adjudicate whether the strongly supported
insertion is an ONT systematic error, a reference error, or sample biology.
No chromatogram/orthogonal truth artifact is available to resolve that question.

**Disposition:** BC114 is a valid fail-closed discordance/negative-control run,
not a software acceptance failure and not a candidate for relabeling as PASS.
A biological PASS requires an independent orthogonal truth artifact; absent
that evidence, `FAIL` is the only defensible verdict.
