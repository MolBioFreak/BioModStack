# Selected-model provision → validation handoff

## Implemented boundary

The supported setup CLI now has **offline `verify`**. It reads a completed provision
operation under the existing configuration lock, re-resolves the current acquisition
authority, revalidates every image/object/member layout and its recorded filesystem
identity, then passes the bindings to the existing **Protenix observed-runtime
attestation validator**. It revalidates provisioned bytes again after the evidence
check. No second registry, qualification certificate, registration transaction,
scientific parameter surface, model execution or service activation is added.

```sh
./start_ui.sh verify --json --model protenix \
  --operation-id EXISTING_PROVISION_OPERATION \
  --expect-plan-sha256 REVIEWED_CURRENT_PROVISION_PLAN_DIGEST

# Optional: check an existing observed execution attestation, NOT an approval file.
./start_ui.sh verify --json --model protenix \
  --operation-id EXISTING_PROVISION_OPERATION \
  --expect-plan-sha256 REVIEWED_CURRENT_PROVISION_PLAN_DIGEST \
  --runtime-attestation /external/evidence/cm_protenix_runtime_attestation_v1.json
```

This does not obtain missing bytes. Run the separately authorized provision/resume
path for acquisition. `verify` cannot accept licenses or supply manifests/URLs,
and has no CLI test-mode flag. The attestation option requires exactly one
`--model protenix`; other model selections retain byte checks but explicitly return
`qualification_handoff_unsupported`. This is not ESMFold2 qualification support.

A managed core-release generation change invalidates the old provision plan and
journal binding. The existing explicit new-plan/new-operation reconciliation is
still required. Verification never rewrites an old operation into a new generation.
Tests cover configure → interrupted provision → resume → core acceptance → new
operation → verify for both Protenix and ESMFold2 fixture selections. Model blockers
do not alter the core release's accepted receipt.

## Report semantics (not interchangeable)

| Field/state | Meaning |
|---|---|
| `status: bytes-materialized` from provision/resume | Byte acquisition/publication only. |
| `bytes_materialized: true` on a verify model row | Current authority, deterministic paths, complete frozen layout, hashes/sizes and saved filesystem identities match on this invocation. |
| `status: validator-blocked` | Byte verification succeeded, but at least one evidence/qualification/registration gate remains open. |
| `validator.status: passed`, `provision_binding: matched` | The existing attestation validator passed and its image/checkpoint observations match this operation. **Not** a successful scientific result or scientific qualification. |
| `scientifically_qualified: false`, `qualification: not-qualified` | Setup has no model-wide scientific acceptance authority. These remain false/not-qualified even with a valid attestation. No `scientifically-qualified` transition is implemented or inferred. |
| `registered: false`, `registration.status: not-performed` | No registry mutation or registration is attempted. Model-definition presence in YAML is not runtime registration. |
| Aggregate `ready: false`, exit **3** | Selected-model readiness has not been established; in this bounded implementation verify never returns a readiness success. |

The report identifies the validator authority, evidence file SHA-256, structured
failure details and `required_evidence` authorities. An evidence digest identifies
the supplied document; it is not a signature, release approval or proof that an
execution occurred. The existing validator checks attestation structure and
internal consistency, not arbitrary operator assertions of scientific success.

Failures before byte revalidation leave `bytes_materialized: false`. Corrupt,
missing, writable, symlinked/hardlinked, same-size changed, or same-byte replaced
objects/members cannot retain the saved byte verdict. Receipt and binding path
changes are rejected against paths reconstructed from current manifests. Saved
qualification/registration/ready flags are ignored. A missing or malformed journal,
license record, selection, configuration generation or manifest fails closed.
No download, repair, journal/receipt rewrite, approval generation or native-output
publication happens in verify. The existing configuration lock may create its lock
file/directory; this is not an entirely filesystem-write-free command.

## Existing authorities and exact remaining gates

1. **Approved acquisition authority:** reviewed entries in the existing model YAML
   registry, immutable runtime SHA-256/size/build/provenance, complete member-pinned
   licensed weights, dependency closure, immutable delivery URLs, source/approval
   references and applicable reviewed license identities. Production entries are
   still absent: `approved_acquisition_metadata_missing` remains a blocker.
2. **Observed Protenix runtime evidence:**
   `services.conformational_mapping.protenix._validate_runtime_attestation`, the
   same entrypoint used by `finalize_protenix`, and
   `schemas/conformational_mapping/cm_protenix_runtime_attestation_v1.schema.json`.
   It requires the image host receipt, checkpoint/wrapper execution snapshot
   receipt, measured backend source manifest/commit/version, command/timestamps,
   model identity, global artifact roles and canonical attestation digest. The
   handoff additionally checks the host-observed image path/device/inode against
   the provisioned image, and checkpoint relative path/hash/size/source inode/device
   against the provisioned member layout. This is deliberately exact-local-identity
   validation, not portable acceptance of a different same-byte filesystem copy.
3. **Native scientific result acceptance:** actual canonical request and ordered
   snapshots, native files, coordinate ledger, composition/modification/bond
   fidelity, confidence, full seed/sample accounting and global artifacts through
   `services.conformational_mapping.protenix.finalize_protenix`. Setup does **not**
   invoke this publishing finalizer with fabricated/empty native results.
4. **Consistent result and persistence evidence:**
   `services.conformational_mapping.contracts.validate_contract_bundle` and the
   existing `services.conformational_mapping.persistence` ingestion authorities:
   exact request/runtime/attestation/artifact hashes across native-artifacts,
   ensemble, analysis and handoff, with managed job/result persistence. Naming
   files in an attestation does not prove those files exist or satisfy these gates.
5. **Model-wide acceptance:** all gates in
   `Model_Configuration_Operator_Control_and_Agent_Parity.md`, including exact
   released capabilities/effective settings, supported modes, operator/agent
   parity, execution mapping, saved settings and global result experience plus
   live scientific acceptance. A Protenix CM attestation does not qualify all
   standalone Protenix modes, much less another model.
6. **Production installation acceptance:** real toolchain/hardware/GPU, core image
   provenance, managed ownership/readiness, clean-machine and independent-worker
   evidence remain separate. The core release fixture does not satisfy them.

No safe existing installer-level registration authority was found for turning
these read-only checks into model-wide approval. Consequently this change exposes
the existing validator's truthful bounded result and required downstream evidence;
it does not invent a self-approved registry. Native scientific execution and
release acceptance must precede any separately reviewed registration integration.

## Reproduced local validation

- **131 root tests passed**, including **27 new handoff tests** and the additional
  Protenix integrated-install variant.
- **81 focused API tests passed**, including the existing Protenix runtime
  attestation, acquisition/layout and bootstrap suites.
- Tests used a disposable HOME/XDG, tiny loopback HTTP bytes, segregated
  `test-fixtures-not-scientific-assets` stores, and visibly named
  `TEST-ONLY-NOT-SCIENTIFIC-EVIDENCE` builder inputs. Real image/layout verification,
  execution-attestation builders and attestation validator ran. No Protenix
  inference, services, jobs, production license acceptance or host registry writes.
- A valid **test-only** attestation still exits 3 with
  `scientific_qualification_evidence_required` and `registration_not_authorized`.
  Tests also cover equal-size corruption, same-byte inode replacement, image/layout
  and attestation path drift, symlinks, missing files, malformed evidence/receipts,
  forged saved readiness, stale metadata/roots/selection and missing real manifests.

The root run used the parent's existing locked API interpreter read-only; API
validation used that environment with `uv run --frozen --no-sync --group dev`.
No dependency/lockfile update, push, deployment or host configuration change occurred.
