# BNS Molecular Tools and Alignments upstream reconciliation

## Pinned scope

- BNS base: `4aeaee783cda69035522a823e80824a86e3512df`.
- Upstream test: `7e662af00f56bdd70a74978ba0ff5fd54a6704fd`.
- Upstream main: `84476f5622e7a8f257f77fd823bd41756544dd94`.
- Comparison merge-base: `dc701125bf1ee0c9813f6974886cbd9c48c2df38`.
- Target: isolated `task/bns-molbio-alignments-port-20261106`; local commits only.
- Scope: Molecular Tools/cloning and Alignments owners, their persistence/schema,
  routing, dependency and generated-viewer prerequisites. No full-product surface restore.

## Outcome and shared hooks

- Ported `cad5406` and `a29baa3`: the complete Gibson/HiFi, Golden Gate and
  restriction-ligation tranche, not merely the Assembly UI. Includes physical ends
  and phosphorylation observations, end operations, junction observations, digest
  exclusions/redigestion, annotation/primer propagation, suggestions, saved-child
  reuse, revision relatives/chooser, branch transfer, origin/import/export fidelity.
- `main.py`: retain the edition import list; register only the existing Golden Gate
  design router and new Gibson suggestion, end-operation, relatives and branch
  routers. No public sharing, protein construction, geometry, CAD or robot routes.
- `MolBioToolkitV2.tsx`: apply assembly/history/import/export hooks without adding
  ShareDialog. Extract the existing local sequence-record normalization functions
  into the source-owned helper so browser parsing and server import share it;
  remove the duplicate local definitions and their now-unused imports.
- `src/lib/api.ts`: additive cloning types/callers only; no wholesale source copy.
  Optional unavailable junction observations carry no invented coordinates.
- Digest saving now uses the native Open saved digest action, not a raw API link.
  Remove the obsolete URL-resolver import; preserve the resolver elsewhere.
- `a42a11e`: port the missing MolBio collections fixture dependency mock; no NGS
  implementation changes. Mark the manual special-enzyme fixture as double-stranded
  so its existing substrate contract is satisfied; no scientific assertion removed.
- Keep original parsability, physical-geometry, requested-source and persistence
  validation. Do not import stricter alignment qualification gates. An unavailable
  optional ligation junction-site observation is returned as evidence instead of
  raising an HTTP error; native joining still executes with unchanged geometry.

## Migration and source scopes

- MolBio migration `0009_operation_edge_revision_indexes` adds revision-edge
  lookup indexes. `0010_digest_child_features` replaces the current digest-child
  integrity trigger to allow its optional mapped features array.
- Both migrations are registered in `MOLBIO_MIGRATIONS` and executed by the normal
  owner. Historical 0007 trigger bytes/attestation remain pinned; expected current
  trigger uses the additive 0010 version. Fresh-DB and populated-upgrade/reopen tests
  cover the ledger and saved pre-0010 digest fixture.
- Experiment family-MSA migration v23 and later existing ledger/attestation/run_all
  hooks were already present. Upstream experiment_migrations.py differs only by
  two blank lines; no migration rewrite is necessary.
- Product-scope model IDs already include only family_msa_protein and
  family_msa_nucleic in addition to the existing NGS list. The protein alignment
  adapter remains the explicit exception; nucleic alignment uses ngs_molbio.
  Edition adapter/model scope tests are retained, not replaced with full-BMS tests.
- Project workflow source scopes, alignment-only receiving, DNA workspace
  discrimination, saved shelf handoff, settings presets, saved MUSCLE settings,
  output-check receipts, imports/exports and renderer state match pinned upstream.

## Dependencies and generated viewers

- API pyproject.toml/uv.lock already match pinned upstream, including the first-party
  bms-alignment-core path dependency and the pinned cloning dependencies.
- All three viewer packages, workspace membership, locked dependencies, production
  build steps, direct-Vite missing/stale-bundle plugin, first-party wheel source
  cache keys and alignment mounted-test glob were already present.
- No dependency-manifest change is required for this tranche. Frozen pnpm workspace
  install succeeds. Do not replace package.json merely to restore build:share or
  copy unrelated lockfile snapshot churn from full BMS.
- Normal isolated production build builds JBrowse, AV2 and BioJS successfully;
  generated artifacts remain ignored, never committed. Direct Vite configuration
  is also exercised with all three task-owned dist directories initially absent.

## Deliberate exclusions / remaining tree differences

- Guest/public sharing and its lockout/install surfaces remain trimmed.
- GoldenGateDesignEditor.tsx and GoldenGateScienceEditor.tsx, plus the old
  molbio-sanity-golden-gate-raw test, were deleted by edition trimming. They are
  unused standalone authoring leaves, not the supported GoldenGateWorkflowWorkspace.
  Do not restore them to make source trees superficially equal. Supported workflow
  authoring and its mounted/API tests remain present.
- The native_publication optional argument added to family_msa/publication.py on
  full BMS serves binder diagnostic-return roots, not ordinary family-MSA output.
  No BNS caller passes it; do not restore the binder-publication dependency.
- Do not replace App/ProjectManager, source/model unions or installer owners with
  full-BMS copies. Their unrelated product changes belong outside this lane.

## Source-commit inventory

120 source commits: 110 already present, 3 ported, 2 superseded and 5 excluded.

The table covers the union of non-merge source commits since the pinned merge-base
that touch the feature owners, plus explicit shared UI/startup/fixture prerequisites.
`already-present` means ancestry, a recorded pick, or final supported-owner parity;
not that every historical patch survives unchanged. Final-tree comparison is also
performed, so a merge-owned difference is not hidden by patch IDs. Two historical
alignment restriction commits are superseded by the already-present nonblocking fixes.

| Source commit | Disposition | Change / reconciliation |
|---|---|---|
| `2d109e9143123a9eed1e21fc03545f9a9bb6457c` | excluded | Add tailnet Share button and guest share management to MolBio viewer — Guest/public sharing remains outside the edition surface; no share server or UI restored. |
| `392677eca29516f2ad30399929ed4cbbfbf86c0f` | excluded | Add read-only guest viewer for temporary MolBio shares — Guest/public sharing remains outside the edition surface; no share server or UI restored. |
| `9a8a4424c09bb7d9b813f1cf5fd9382a1479c275` | excluded | Ban share-server clients after three failed attempts and surface attempts to the tailnet — Guest/public sharing remains outside the edition surface; no share server or UI restored. |
| `4482278b39907fe74198efda118ae1700677295c` | excluded | Harden share management origins, installer staging and malformed guest inputs — Guest/public sharing remains outside the edition surface; no share server or UI restored. |
| `3cfe4a2e8f9d87e2e73487a4aae5367d267cf217` | already-present | Add initial standalone alignment core and MAFFT runner — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e87b20b7d59d4b1d164785964e86954a7095480a` | already-present | Add PM-backed family alignment artifact custody — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `92682425fb7d0200476502bbd0ebfb342ffcee24` | already-present | Add alignment workspace host and coordinate primitives — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `c50a5d0bb4bc3fe62b2dfa5537339973223a2f2e` | already-present | Validate alignment identities and portable bundle manifests — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `fca18d0dfa09ecf6045681cc94db2fcf5b229f26` | already-present | Fix alignment artifact catalog races and replay identity — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e026ea8830107f9f9a546fcde80b981a8a43393c` | already-present | Add pinned project dataset resource inventory — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `ec285447d607214ff2bf5045a36a21c4d0ab06b7` | already-present | Connect alignment workspace navigation and project setup discovery — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `3590dd96a9d645f3a28649936b45c317345d151e` | already-present | Improve standalone alignment comparison and portable exports — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `5623737f30b98655294ad971c3112a58dc0070df` | already-present | Require explicit residue maps for alignment comparison — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `0973391e15f9f15369214df6e80e55d498b084ef` | already-present | Add initial native alignment engine adapters — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f458c74cb060bafa1cdabefef2278c0f6558eb77` | already-present | Isolate alignment attempts and qualify native tree mapping — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `109b9329781dfcfb490aaf7bf2c20592140ce37e` | already-present | Resolve exact molecular dataset sequence occurrences — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `ab84f742dc21b132295eb186ff0f9398106f561e` | already-present | Add verified JobArtifact to PM custody primitive — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f12f1d1507b9c7ed96b3f993e13a482b1e0d7788` | already-present | Add family alignment authoring workspace — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `3c144e0baa88a95a57fcb63b5113e860b97d41e4` | already-present | Stream verified job artifacts into experiment custody — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `63eea4dc831a7b5f1e33e33d7cb614025246b998` | already-present | Add family MSA request and native command projections — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `a54e13c7c0786030f2acda59d9a07727d79677b7` | already-present | Execute family alignment jobs in owned CPU workflow runner — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `93e2e01fb81a7b3ebf0e29160ef9e22b501588e3` | already-present | Execute native family MSA engines in owned CPU jobs — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `cdc1e21e1efcf112e5bebd771f70ec9610b813c3` | already-present | Fence family alignment terminal writes by execution authority — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `4e7d9793d4f0f9718e05fab3fba460b715340aab` | already-present | Publish family alignment artifact manifests and retain PM results — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `c2e276efe849e5c061f3f1a5f08558854505794d` | already-present | Handle alignment job query receipt routes — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `736633218b970f1ec2f9b3b8421d2544f64e95ec` | already-present | Register family alignment discovery and managed CPU image — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `0cb5d2f45562053d0f12bba91e7e5da269555ba9` | already-present | Add family alignment document row projection — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `de5a31363a7a9dabaf9720cfd4de8a307aea6eda` | already-present | Record alignment observations and safely map tree leaves — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `5b7161e0747904162ea40b99543880147820f934` | already-present | Connect family alignment project launches to typed Jobs — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `1bb873d59e3f8ae8e68358b76ffed17e7298d777` | already-present | Expose authorized family alignment result and retained artifact readers — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `396a1301957aab243e4a65162617850f3c6d9171` | already-present | Add partial typed alignment HTTP client and CLI — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `b2603716e76da1f1a4066821cc76bcdc9af75575` | already-present | Preserve alignment project launch handoff for recovery — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e0ef4b0d78421915083b9d5931334f31a9ac3b5c` | already-present | Poll alignment job results until publication — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `deed990878a55ce828f2b2b9e92d9ee09dc4b54b` | already-present | Persist project shared alignment state as dataset revisions — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `70076ced642c81f049ab3d3779fb4bef9fc1ecef` | already-present | Connect alignment editor to Project Dataset sources — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `46d4710684d340b70fb48badb295aa0ca160013e` | already-present | Add experimental local JBrowse viewer and security probe — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `d7b83d9f15a18a8bedba1ca837e1c62ad9b6c556` | already-present | Connect JBrowse alignment viewer using pnpm workspace — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `def76dfef685b04234c3a054bec0561c8973987d` | already-present | Fix alignment dataset picker provenance and async races — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `115a27c3379ac72e9de7c2729bdf00a129cacaf1` | already-present | Display alignment row labels in JBrowse — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `968b942bf131db713f9c79c2fbad42d2e2666607` | already-present | Integrate AV2 and BioJS alignment viewer adapters — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `695fed01bc6ef61339fb5477961ec9d64fd756ad` | already-present | Connect project alignment shared state controls — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `b54379dc2ff7bbc407252bb3ad462afdce687c69` | already-present | Preserve shared alignment drafts across saves and navigation — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e969eae24841b497182a4e0c6e78520697f1898b` | already-present | Add partial Project alignment bundle export and inspection — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f9fff202d93bb2e39209cb09f793de68535aeb12` | already-present | Expose incremental native MSA settings inventory — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `fe6dde0f5e77656359696ca31d7e5d90dc30d9b7` | already-present | Receive Project alignment results and native model routes — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `65e92863c26fbe24879940b5bf8728ae11d57c0d` | already-present | Preserve explicit molecular source position maps — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f66b03e06930d4b75ebd214926191eafac169c9a` | already-present | Resolve protein Design chain occurrences for family alignments — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `ec62ae2bd424a600331d5a4cc2e2b93eef177a02` | already-present | Persist alignment host view selection and row layout — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `66270e626f7a0743c6101b1f207f1985a46efd2e` | already-present | Distinguish observed protein chain source identities — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `c1ff98e1c75559e1c154e66ae0b0334ad391ae6c` | already-present | Retain portable alignment imports in Project custody — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `17e4445f4f08d6f6e67a45674fd2200a4c889476` | already-present | Observe native alignment case and residue differences — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `8732a8e0ce4098b804ed2f051731c09c1997b7bf` | already-present | Stream portable alignment project imports — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `c0258861c334adc872ecd00a6dec883e9e4d33f8` | already-present | Validate portable Project inventory and map explicit references — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `633bf3cd7d17e51871f99d22890fc2ee80533e13` | already-present | Restore portable alignment shared state in Project revisions — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `585e020498ed9e3d727cbe226c695e12d9542f2f` | already-present | Add Project alignment receipt library and bundle import — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `952eb836c06a9f19191715ecc62e2bf38c9bbaa4` | already-present | Retain observed structure residue identities for alignment sources — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `27fb41723ab2af26b50cace766375040192fe188` | already-present | Add project alignment file imports — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `524f8b7cb9467414e4e7c47440e6464fd00a1868` | already-present | Expose MAFFT refinement overrides and native verbosity — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `fe89b455956a4064706b96051d535d3ae92bd863` | already-present | Report unresolved references in imported alignment state — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `ebfe971935fa77b02a4a9deee02600f2193ee5b6` | already-present | Add portable alignment bundle download for shared results — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `998f789b57188106d92510f994e70b69b8ff37ca` | already-present | Import portable alignment ZIPs into selected Project domains — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `bbe451fe613bda723410006daaf2b631bc3080bd` | already-present | Resolve validated dataset occurrence ancestry — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `34198d6a66f66ee0f109072d6edc11a5952ee40e` | already-present | Show Project identity on direct alignment result routes — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `a683ae24733f48157da2098f7260c2fe9b671c9d` | already-present | Batch dataset ancestry roster reads — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `68d0b49d7fd2cc78fb277da861cf2392d9beb58f` | already-present | Retain Stockholm annotations and map A3M insertions — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `667ee6888aff50818ee5fee5ff33c6d6113ec08c` | already-present | Add source residue-pair alignment agreement — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `1dced750bd77b65430f7babf6ed3a71e54eb86a8` | already-present | Derive alignment editor defaults from request schema — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `c78d54e1940b53953ba6151154c0ba4ed5db9f34` | already-present | Refresh browser alignment request schema — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `a7c7e4d929e1a8377534e1d1cac3564e649ba411` | already-present | Test native cross-engine pair agreement and label ensemble support — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `d6cc29686ea05f4f7ff0657949c92ba5f62b96bb` | already-present | Preserve separate Stockholm records in alignment imports — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `5bcdde7ef7380fa58e39941073732b6f9c2c7392` | already-present | Fix TWILIGHT auxiliary options and exercise typed settings — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f699973b5e062bdc8812edae92ff0282e2bc3014` | already-present | Expose on-demand project ensemble support — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e9ce7c1e629469035be17646a414b401c747222c` | already-present | Preserve source positions in alignment viewer selections — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f6dc9b7acf8fa4c44ecca8c08e67587dbd1ac32e` | already-present | Expose MAFFT pairwise scoring and substitution options — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `3196bd53d86b240d5671b1447c85df7ecadeb9b1` | already-present | Require explicit unambiguous alignment source maps — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `ecd0deee65eb06995e815830c3a4a851ab334e05` | already-present | Reset alignment settings on project hydration — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `9465b63215693e6931656ddbb4e1fa19bf21f130` | already-present | Expose native alignment tree matrix and expert settings with explicit dispositions — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `71d3a8dabebd93f99714afb0c9639f83d06fa0dd` | already-present | Complete managed alignment comparison workspace and typed HTTP workflows — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `f6c66e7e8c5f8275a8c6b79ea8ed21860f6f99bd` | already-present | Restore native alignment viewer state and marks with lazy renderer loading — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `5670c019b2b3a8f84a8a0b0a5a42c491c1449584` | already-present | Run alignment comparison and export through owned CPU Jobs — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `94ca44b0390a88fd1795fd450d875320d19aec02` | already-present | Route shared alignment exports through managed Jobs and preserve view SDK state — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `29b8c7f305942af15889d5de33433d2813f5aa50` | already-present | Reopen managed alignment analyses and exports from Job results — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `d8f0ea5168d0fbfff9b2f24d6554a8e1b3322129` | already-present | Complete reachable MAFFT and MUSCLE native controls with source proof — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `917b3172419fc4ae0ab09a7659f79f9ff885a2ac` | already-present | Repair full-width Alignments workspace and library states — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `c80307a271dcaf54ac2b26d15f85a0cf0c051c24` | already-present | Restore native molecular Project attachments and export plasmid history — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `ea88dd6182c38063385665cc8b3272b33834ccfb` | already-present | Restore direct native Project intake and focus-view quick actions — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `883847408e2ee9699aa024a23fa352b0eea01601` | already-present | Keep MolBio controls and sequence palettes readable across themes — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `1abc46b2e3b2634421b94ef12f206f738f101256` | already-present | Add batch reference entry, barcode mapping and deletion controls; redesign GC analytics — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `640ac12f58732a046b26598140cdf964ebef538b` | already-present | Keep the MolBio canvas usable with analytics tracks open — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `07815395e7da66799d50a3a9af7f77edd725f7b1` | already-present | Hide empty sequencing notice in Molecular Tools — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e3ac9b6d5b7f48b8a8859a3b39725ffd6fe284f4` | already-present | Keep alignment comparison links intact across viewport widths — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `a42a11eb874882f7ce22497290a21121e529fbd9` | ported | Mock raw-signal retention in the MolBio collections sanity test — Collections fixture declares its existing retention-query dependency. |
| `f0d91e2ab10365774a173e1839f93041c27ecb30` | already-present | Route phone camera and resource links through the configured API owner — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `a4d5f517da0d3836621257112a5dbf88460a049d` | already-present | Send selected saved MolBio sequences to the alignment editor — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `e38f519646d4c14131690ce2d41bc9163681d43b` | excluded | Repair Project draft, native operation retries and retained result custody — Only differing family-MSA hunk adds binder-specific native publication roots; no BNS caller requires it. |
| `bf3e98a0f3c38af9d1e48d4fa40683b11a7e5fc7` | already-present | Recover oversized enzyme scans and separate source metadata from annotations — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `34c05ee66079a4e8661f4c8bb2c8c7f5eaecb01d` | already-present | Condense same-site restriction labels by recognition specificity — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `44111a781ab40b98e4dcabff9ec6da1ff89f0acc` | already-present | Improve project card contrast and open grouped enzyme details on click — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `61da3517ed7008b71a0cf32ee1fe3033dbd885dc` | already-present | Keep sequence selection in the viewer and recover interrupted mouse drags — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `5189ac5528a11d484e49140daf4b9ed7bbfd2504` | already-present | Keep alignment receiving separate from native DNA workspace reads — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `1956ec0fd37c2c23f94dab04693afef337c19d36` | already-present | Backport missing alignment viewer and settings presentation prerequisites — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `b4b426653f79edae2753f3330e263f308dec7058` | already-present | Improve BNS alignment saving, navigation and sequence comparisons — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `00dbc1068d6011b2f3eb4821d1d988b7b3b5925d` | already-present | Keep MolBio tools at a shared width across tab changes — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `0182dc94de7b07d6ce1fdfd5d38803df03f35ba9` | already-present | Keep older NGS domains as DNA workspaces and fix shelf-to-alignment handoff edges — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `1c1f89e4ceaad68cb1728da116752320496058e1` | already-present | Plain alignment setting descriptions — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `80eb8b94c5ba75bb7755900393d4cb39dba5ec4c` | already-present | Plain wording across the Alignments screens — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `4cd0ce9b691e62811c80bc7b7f9322f3013f0d41` | already-present | Standard plasmid alignment settings and plain explainers across Alignments — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `15a9889f5d2722e2f78b9f89eec7bb9091edf1dd` | already-present | Complete alignment managed-start hooks and mounted test discovery — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `991e6dbc7f0791c32a65b6ee09ba93dc6b1d001e` | already-present | Correct molecular scientific outputs and close the independent audit — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `3fdb10f936dadac8c87f1b64cf15d5905de4edf3` | already-present | Invalidate alignment wheel cache on Python source changes — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `4bdc76b54967443542c329bc7c3aac1dca4fd93f` | superseded | Correct alignment integrity, source coordinates and native capability checks — Correctness retained; blocking alignment qualification replaced by 45da706/5e3e61e nonblocking output checks already in BNS. |
| `838db7ac536108aaa57bef07583f1365a94413e7` | superseded | Correct alignment displays, export classification and visible qualification notices — Correctness retained; blocking alignment qualification replaced by 45da706/5e3e61e nonblocking output checks already in BNS. |
| `45da7068a0b5957ce35854c70922f0226c002043` | already-present | Keep MUSCLE and alignment results usable; report output checks instead of blocking — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `5e3e61ec7094ebda412961736f28af2fe18b7ad6` | already-present | Fix MUSCLE nucleotide identity scoring without overriding saved settings — Final supported-owner content reconciled against pinned source; earlier changes subsumed by later fixes/edition import. |
| `cad54061d4016396ae7110cb558895ad066afb88` | ported | Molecular Tools: complete cloning feature set (Gibson/HiFi, Golden Gate, restriction-ligation) — Full cloning tranche; scoped router conflict resolutions, helper extraction, no sharing restore. |
| `a29baa3a9b979139f2f7ccadd203c6f0245b2e37` | ported | Molecular Tools: keep saved-molecule ends when choosing an Assembly mode; keep excluded cut sites listed — Saved-molecule ends survive mode changes; excluded cuts remain listed. |
| `1c8b4914a8cb51262bcd5c727469ec8c1f36f295` | already-present | Add domain-specific family alignment project setups — Dedicated router/capability owner is byte-identical to pinned test. |
| `8760caa954f4db5865a7608f64265bafce2c6a06` | already-present | Expose paged family alignment dataset resource reads — Dedicated router/capability owner is byte-identical to pinned test. |
| `2f6bec2290937d2e629585744734704786e6c494` | already-present | Spool streamed alignment import metadata before parsing — Dedicated router/capability owner is byte-identical to pinned test. |
| `df3f37761c5f28b05cd840fe3c0a7a11cffeb812` | already-present | Preserve dataset occurrence ancestry in alignment source API — Dedicated router/capability owner is byte-identical to pinned test. |

## Entire cloning-tranche file/hunk ownership

Every file in cad5406 is represented below; no keyword-only patch selection.

| Owner | Disposition |
|---|---|
| `platform/api/config/ngs_molbio/capability_inventory_v2.json` | ported; byte-identical to pinned test final tree |
| `platform/api/config/ngs_molbio/schema_registry_v2.json` | ported; byte-identical to pinned test final tree |
| `platform/api/main.py` | ported scoped registration only; full-product router/import list excluded |
| `platform/api/molbio_database.py` | ported; byte-identical to pinned test final tree |
| `platform/api/molbio_migrations.py` | ported; byte-identical to pinned test final tree |
| `platform/api/molbio_models.py` | ported; byte-identical to pinned test final tree |
| `platform/api/routers/molbio_branches.py` | ported; byte-identical to pinned test final tree |
| `platform/api/routers/molbio_end_operations.py` | ported; byte-identical to pinned test final tree |
| `platform/api/routers/molbio_gibson_suggest.py` | ported; byte-identical to pinned test final tree |
| `platform/api/routers/molbio_golden_gate_design.py` | ported; byte-identical to pinned test final tree |
| `platform/api/routers/molbio_ops.py` | ported; optional junction observation failure converted to returned evidence |
| `platform/api/routers/molbio_relatives.py` | ported; byte-identical to pinned test final tree |
| `platform/api/routers/molbio_restriction.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/common.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/end_operations.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/feature_mapping.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/feature_types.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/gibson.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/gibson_children.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/gibson_suggest.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/gibson_tm.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/golden_gate_design.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/golden_gate_design_types.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/golden_gate_exports.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/golden_gate_suggest.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/golden_gate_suggest_parts.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/golden_gate_workflow_persistence.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/junction_sites.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/ligation.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/ligation_contract.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/physical_ends.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/product_content.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/pydna_gibson.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/region_match.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/restriction_cloning.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/assembly/types.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/molbio_branch_bundle.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/molbio_persistence.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/molbio_relatives.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/restriction_digest.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/restriction_digest_save_receipt.py` | ported; byte-identical to pinned test final tree |
| `platform/api/services/restriction_digest_substrate.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/fixtures/restriction_digest/pre0010_saved_digest.json` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_acceptance_cloning.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_assembly_product_content.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_end_operations_and_cloning.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_gibson_l3.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_gibson_suggest.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_batch_receiving.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_design_core.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_exports_portable.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_receiving_repairs.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_suggest.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_suggest_modes.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_golden_gate_workflow_receiving.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_molbio_branches.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_molbio_database.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_molbio_relatives.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_molbio_sanity_e.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_phosphate_product_rules.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_physical_ends.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_primer_design_circular_origin.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_region_match.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_restriction_digest_l2b.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_restriction_digest_persistence.py` | ported; byte-identical to pinned test final tree |
| `platform/api/tests/test_set_origin_fixture_and_import.py` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/ExportDropdown.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/FragmentChooser.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/MolBioToolkitV2.tsx` | ported cloning hooks and helper extraction; guest sharing excluded |
| `platform/frontend/src/components/MolBioToolkit/SequenceHeader.tsx` | edition adaptation retained; see shared hooks above |
| `platform/frontend/src/components/MolBioToolkit/panels/AssemblyPanel.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/DigestPanel.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/EditPanel.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/GibsonDesignWorkspace.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/HistoryPanel.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/HistoryRelatives.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/PrimerPanel.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/SuggestPlan.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/panels/golden-gate/GoldenGateWorkflowWorkspace.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/assemblyFeatures.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/branchTransfer.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/currentRevision.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/exportData.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/sequenceRecord.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/serverImport.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/setOrigin.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/components/MolBioToolkit/utils/topology.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/lib/api.ts` | ported into edition client; unavailable observation coordinates optional |
| `platform/frontend/src/lib/digestAssemblyTransfer.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/lib/restrictionAnalysis.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/lib/retainedDigestFragment.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/src/types/bio-parsers.d.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/fixtures/primer-model-options.json` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/fixtures/set-origin-cases.json` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/molBioGenbankFidelity.test.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/molBioSelectionWorkflowContract.test.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/molBioSetOrigin.test.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/molBioTopology.test.ts` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molBioDigestApiAuthority.test.tsx` | ported Open saved digest assertion; preserve configured-server fixture variants |
| `platform/frontend/tests/vitest/molBioTrafficDigestScience.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molBioTrafficPairingMounted.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molbio-sanity-golden-gate-workflow.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molbio-sanity-l7-chooser-history.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molbio-sanity-l7-derive.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molbio-sanity-l8-assembly-ui.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molbio-sanity-w2d1-suggest-plan.test.tsx` | ported; byte-identical to pinned test final tree |
| `platform/frontend/tests/vitest/molbio-sanity-w2d2-branch.test.tsx` | ported; byte-identical to pinned test final tree |
| `schemas/ngs_molbio/molbio-assembly-golden_gate-design-v1.schema.json` | ported; byte-identical to pinned test final tree |
| `schemas/ngs_molbio/molbio-golden-gate-workflow-request-v1.schema.json` | ported; byte-identical to pinned test final tree |
| `schemas/ngs_molbio/molbio-golden-gate-workflow-save-v1.schema.json` | ported; byte-identical to pinned test final tree |

## Shared-owner hunk disposition

- `family_msa_results.py`, `family_msa_project_capabilities.py`, both family-MSA
  model declarations and `alignment_access.py`: already byte-identical to test.
- `project_manager.py`: preserve the protein-alignment adapter exception and
  ordinary alignment routes. Exclude full-product retained-native-binder routes.
- `global_experiments/{adapters,receipts,result_surfaces,launch_contexts,workflow_setups}.py`:
  preserve family-MSA registration, retention, launch/resource normalization and
  schema hooks. Remaining source deltas extend binder/MD/de-novo behavior or
  retire unrelated RFD3 owners; none is needed for the supported family-MSA path.
- Frontend App/ProjectManager shared changes restore Global scopes/candidate views
  and native protein authoring. Keep BNS's edition surface and existing alignment
  no-DNA-workspace branch. Do not copy these full files.
- The upstream `fetchJobs` exclude_alignments client option serves full-product
  result listing; the BNS alignment screens use their dedicated query owners.
  Existing supported alignment routes/types and Project setup domain unions stay.

## Verification

- Frozen pnpm workspace install: succeeds without any manifest/lock edits.
- Strict `tsc --noEmit -p tsconfig.app.json`: succeeds.
- Full normal `pnpm run build:isolated`: succeeds, including all three viewer
  workspaces and the trimmed application bundle.
- Direct Vite `createServer` configuration, no listener started: succeeds after
  removing all three task-owned viewer dist directories. All outputs are rebuilt;
  configuration prints `DIRECT_VITE_CONFIG_OK` and closes cleanly.
- API focused roster after the final bounded-observation fix: **485 passed, 2 failed, no skips**, 34 files, actual JUnit
  cases counted. Covers cloning/end geometry, retained save/reopen, feature and
  primer mapping, Golden Gate receiving/export, digest migration/attestation and
  populated upgrades, family-MSA edition scope and v22 upgrade/reopen.
- Additional alignment API roster: **42 passed, 4 skipped**, 10 files. Exercises
  setup, sources, routes, direction projection, export classification, shared state,
  discovery and CPU resource handling. The four native receipt/journey cases skip
  because BMS_TEST_FAMILY_MSA_SIF is not configured; they are not claimed as run.
- Mounted Molecular Tools / Alignments roster: **412 passed**, 55 suites; every
  requested test file has JUnit cases. The schema-owned typed-nondefault case is
  enabled with the actual AlignmentRequest schema. Serial file execution.
- Changed Node suites: **16 passed** (GenBank fidelity, selection contract,
  set-origin and topology).
- Alignment-core selected offline roster: **127 passed, 75 skipped**. Skips:
  BMS_MSA_SIF not configured (54), BMS_TEST_MUSCLE not configured (12), native
  binaries not configured (4), BMS_TEST_MAFFT not configured (3), and
  BMS_TEST_FAMSA not configured (2). These are not native-runtime passes.
- Both initially failing mounted tests reproduce on untouched BNS 4aeaee7;
  repairing their missing retention mock / explicit dsDNA fixture makes the
  complete selected mounted roster pass. No assertions were relaxed.
- Shared API failures reproduce on untouched pinned test 7e662af using the same
  interpreter and exact file. They are not new edition failures:
  - `test_golden_gate_wire_receiving.py::test_real_wire_roundtrip[aliases]`:
    portable ZIP entry byte comparison differs (workflow/primer serialization).
  - `test_golden_gate_wire_receiving.py::test_actual_browser_wrapper_requests_reach_native_owners`:
    captured browser normalized-result fixture differs from current response.
  The source file run reports 5 passed / the same 2 failed; cause is not asserted
  beyond the observed serialization/fixture differences.
- `git diff --check` validates the entire base-to-port commit range.

Live ONT deployment, native alignment image runs and a live browser/service journey
are outside this local port. No push or deployment is performed. Test logs, full
rosters and JUnit remain in active-profile task scratch, not in tracked source.
