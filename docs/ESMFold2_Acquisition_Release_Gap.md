# ESMFold2: exact acquisition critical path

Investigation base: `914d8fb22ddb27a616b7990ed25bccfceb203698`.
This is an upstream identity investigation, **not a release approval, working
installer, acquired runtime, or scientific qualification**. Registry gates remain
unchanged. Network observations were made on 2026-09-07.

## Outcome

The missing artifact is a **release-owner reviewed, redistributable ESMFold2
runtime distribution**: one immutable BMS SIF plus a versioned, member-pinned
weight layout covering ESMFold2-Fast, ESMFold2 (the exposed full variant), and
ESMC-6B. It needs stable final HTTPS delivery URLs compatible with the existing
no-redirect downloader, exact SHA-256/size, license notices/acceptance identity,
and scientific acceptance bound to those exact runtime/member identities.
None of the inspected sources supplies that distribution. Adding invented
`approval_ref` values would conceal rather than close the availability blocker.

## Existing authority inspected

* Registry closure is `image: esmfold2.sif` and `weights: esmfold2`;
  `model_registry.py:105-153` requires reviewed acquisition entries but has no
  weight member/destination schema. `runtime_acquisition.py` acquires opaque byte
  objects; it does not materialize an HF cache. Thus entering several weight
  objects under the same dependency alone would not make inference work.
* `apptainer/esmfold2.def` pins Biohub ESM source
  `c94ed8d763bbd7088b296949e5b401e8ea12073a` and transformers
  `3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf`. The upstream ESM `pyproject.toml`
  at that exact commit independently confirms the transformers dependency.
  `services/esmfold2_scientific_consumer.py` binds its scalar dialect to these
  same two commits. Substituting a current upstream container is not equivalent.
* That definition is **not a reproducible locked build**: CUDA base is a tag,
  apt repositories/packages are mutable, uv is fetched from a mutable installer,
  and transitive pip packages are unpinned. `pip freeze` runs after resolution;
  it does not constrain inputs or attest binary identity. `%test` is import-only,
  deliberately skips the GPU-dependent model import, and is not live acceptance.
* Shared-images worktree inspected at
  `9c06faa484cd2b7795345852a81ae8a80761403e`. Its publisher accepts only local
  `{source, sha256}` inputs, with lane keys for NGS, Confornets and Protenix,
  not ESMFold2. The actual Development reference file has those three keys only.
  Its Protenix digest reference is
  `a7458325d37cca00b2f63a75e1334e5bf43e6f694eb6f9d3e820a6194e4ea060`;
  this is local transport identity, not an official remote release source.
* The shared-image reseal changes
  `platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json`.
  It records `implementation_state: implemented_unverified` and
  `release_acceptance_state: open`, not an ESMFold2 distribution manifest.
* Anonymous GitHub releases API for `MolBioFreak/BioModStack` returned one release,
  `android-beta-006-archive`, with an APK and checksum only; no model/SIF assets.
  This observation does not exclude private unpublished artifacts.
* Protenix is a worse cache-free starting point: `apptainer/protenix.def` uses
  `Bootstrap: localimage`, `From: /mnt/BioModStack/apptainer/protenix.sif`, and
  copies `/home/dalab/.local/bin/uv`. Its upstream code pin does not reconstruct
  its inherited dependency stack. No local SIF was hashed or promoted as a release.

## Independently verified upstream weight identities

Candidate revisions were discovered from local HF `refs/main` files, then
resolved independently at the official anonymous HF revision API. Local cache
references alone provide no scientific approval. **Do not follow today's main**:
ESMFold2-Fast main already resolves to a different revision.

| Upstream repository | Exact revision | Inference members | Total bytes |
|---|---|---:|---:|
| biohub/ESMFold2-Fast | `0438ea0d932a314950665e0b4d0af4322ae88250` | 2 | 755419262 |
| biohub/ESMFold2 | `e1e189d0f5fb70c2693da2332eca4443c0ccccd6` | 2 | 939507565 |
| biohub/ESMC-6B | `89c554c46a44d825fbfbe3ce2a6bdc539770bdaa` | 11 | 25408251020 |

Total: **15 inference files, 27103177847 bytes**, excluding SIF, notices,
filesystem metadata, staging and copies. The executable probe emits every file's
exact SHA-256, byte count and commit-qualified candidate URL. It downloads only
small metadata, validates small-file Git blob IDs and computes their SHA-256,
and checks the ESMC shard index covers exactly the six declared shards. Large
weight digests are official LFS declarations, **not locally byte-verified hashes**.

For example Fast `model.safetensors` is 755416924 bytes, SHA-256
`60ca19f2898188beba92944365f7b909efd9c99212f5018af75cc47cd9a6184a`.
A HEAD to its commit-qualified `/resolve/.../model.safetensors` returned **302**
to `us.aws.cdn.hf.co`, with matching `X-Linked-Size` and `X-Linked-Etag`.
The existing downloader rejects redirects. An expiring signed CDN location is
not a durable pinned distribution URL; do not paste it into the registry or
weaken the redirect gate.

Pinned HF model cards declare **MIT + other** and link a mutable
`Biohub/esm/.../main/THIRD_PARTY_NOTICE.md`. ESM `LICENSE.md` at the BMS source pin
is MIT, copyright 2026 Chan Zuckerberg Biohub, Inc. However
`THIRD_PARTY_NOTICE.md` at that source pin returns 404. The current notice was
resolved separately at `bf343ba264b650dff7a073643725f9aaa1fdbe8d` and lists third-party
BSD/MIT/Apache dependencies. It must not silently replace the historical notice
or be treated as a complete license determination for all runtime dependencies.
A release owner must resolve/pin the applicable notices and approve distribution;
this investigation cannot truthfully supply a single approved weight license ID.

## Layout that must be reviewed and tested

Runtime environment expects `/weights/esmfold2/hf_home/hub`. For each repository:

```
weights/esmfold2/hf_home/hub/models--biohub--<repo>/
  refs/main                         # exact reviewed 40-hex revision
  snapshots/<revision>/<member>     # verified regular file, not external symlink
```

Use the inventory's exact config/checkpoint/tokenizer/index names. ESMFold2 and
Fast configs reference `biohub/ESMC-6B`; supplying only the folding checkpoint
will fail. The pinned transformers implementation calls
`ESMCModel.from_pretrained` for that dependency. BMS inference defaults offline.
Materialization must provide a complete private HF root, preserve exact ref and
member bytes, reject extra/path-traversing/symlink members, atomically publish,
and bind its receipt to the approved layout digest. Do not modify configs to
redirect model IDs or silently drop the full variant. This layout is a candidate
for release review, not an existing approved layout or validated installer.

## Minimal executable route and exact handoff

**Executable now, without weights, credentials, services or builds:**

```sh
python scripts/probes/esmfold2_upstream_inventory.py > /tmp/esmfold2-upstream.json
python tests/test_esmfold2_upstream_inventory.py
```

The first command was exercised against real upstream: 3 exact revisions, all
15 members, six-shard index agreement. It fails on changed identity, invalid
sizes/hashes, missing config, unknown dependency, unsafe members or redirects.
The second runs synthetic *probe-contract* tests, not scientific acceptance.

**Release engineering work still required (not performed or claimed):**

1. Review/lock the existing ESMFold2 build inputs: immutable OCI base digest,
   package snapshots and exact hashed wheel/tool sources, preserving both BMS
   source pins. Resolve the runtime/weight redistribution notices. This is the
   prerequisite to a cache-free build, not merely an installer transport change.
2. On an authorized isolated builder with an explicit disk/network budget, build
   that reviewed definition with `apptainer build "$OUT/esmfold2.sif" "$REVIEWED_DEF"`.
   Fetch the inventory's immutable HF revisions through a separately reviewed
   upstream ingestion path; verify every full weight body against the inventory
   digest and size. The existing acquisition executor cannot perform this HF
   ingestion because of its deliberate redirect policy.
3. Produce/review a safe member-layout manifest and materializer (or a reviewed
   deterministic bundle with safe unpacking); exercise from an empty managed
   store with no host caches. Hash SIF and any bundle using `sha256sum`, measure
   exact sizes with `stat -c %s`, and retain the exact build/materialization
   provenance. A build success is not scientific acceptance.
4. Run existing model admission and end-to-end live acceptance through supported
   BMS launch paths on those exact bytes. Preserve request-to-result settings,
   scalar dialect, native outputs, both variants and relevant input modes per
   `Model_Configuration_Operator_Control_and_Agent_Parity.md`. No generic
   ESMFold2 build-to-qualification command was found; `normalize_esmfold2_validation.py`
   only normalizes outputs and must not be relabeled a qualification publisher.
5. Release owner publishes the accepted SIF/weight distribution to an authorized
   immutable HTTPS object endpoint that returns the final bytes directly and
   supports Range. The missing publish destination/authorization is an external
   prerequisite; there is no honest repository command or URL to invent for it.
   Return exact URL, authority, digest, size, pinned license/notices, reviewed
   layout and qualification references. Only then add `acquisition` entries to
   the existing model YAML using the real external `approval_ref`, extend the
   existing registry with reviewed materialization metadata, and exercise
   `preview_model_acquisition` / `acquire_model` plus the unchanged admission gates.

No services, jobs, configuration transaction, other worktree files, credentials,
large downloads, builds, release publication, pushes or deployments were changed.
