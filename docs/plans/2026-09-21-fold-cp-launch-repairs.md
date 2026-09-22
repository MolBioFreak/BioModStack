# Fold-CP launch repair candidate

Base: `test@02a57a2fc0c3034dfa05b777e3a54c178f4a1b08`.
This is a source/test review candidate, not a live execution or deployment claim.
The operator's Hermes handoff reports Development parity with this revision and
live image provisioning. The host-only WIP patch was not accessible through the
connected tools; these edits are independently implemented from the report and
an exact-tree-verified GitHub Actions source archive, not an application of that
parked patch. Do not apply both without comparing their overlapping edits.

## BMS-DEV-55: preparation-generated config ownership

A fresh native request need not name a path that input preparation creates later.
For the existing prepared Fold-CP manifest protocol, require a managed, nonsymlink
prepared root and authenticate both the manifest and every packaged config using
its declared SHA-256. A preparation-generated config owns its relative non-MSA
references when no source is declared in the original request. An explicitly
supplied source must still be absolute, managed, nonsymlink and hash-matched;
it retains ownership of relative template inputs. An invalid declared source
never falls back to generated ownership. Compiler/selected-plan binding and
approved original-input closure verification remain unchanged.

Regressions exercise generated ownership, manifest/config tampering, missing
files, escaped config paths, symlinks, unmanaged prepared roots and invalid
explicit sources. Existing original-template-owner coverage remains in place.

## BMS-DEV-54: read-only resume destination validation

Validate the optional resume source in both preview and canonical job admission,
before row creation, workflow preparation or output writes. A resume destination
must be an existing nonsymlink directory strictly below the configured results
root. Read access to other managed roots does not grant workflow-output write
permission there. Reject relative/traversing paths, the results root itself,
sibling-prefix matches, missing paths and files with named HTTP 422 errors.
Recheck the destination at the output boundary and do not recreate it with mkdir.
The helper does not mutate retained output, migrate old paths or prove ownership
of a particular prior Job; existing supported resume/replay rules still apply.

This deliberately rejects historical resume locations outside the active results
root. Use the supported retained-job resume path instead of relocating old output
or copying stale resume fields into a fresh request. Configuration-root filesystem
failures and later write failures are not claimed to be solved by preflight.

## BMS-DEV-53: explicit placement and complete approval handoff coverage

The structure form previously omitted execution_target_id from its job request,
allowing the shared submit helper to read ambient storage independently of the
form's GPU/target state. Include the form's explicit target (or explicit local
null) in the request used for both preview and submission. Reject a preview
response without a valid digest or with mismatched model/mode/target before
opening approval or sending a Job POST. Preserve explicit operator approval and
backend stale-digest rejection; never auto-approve or blindly replay a 409.

The prior Fold-CP mounted test aborted at the first POST, which was the preview
request; it did not exercise final Job submission. New coverage traverses the
actual form, shared submit helper, visible approval dialog and final POST,
asserting identical frozen science/placement plus the returned approval digest.

These are reproducible source-level gaps. They do not establish the exact cause
of the historical live 409. The reported message means missing approval; the
stale-approval gate has a different message. Verify both request/response payloads
on the next supervised attempt, without exposing sequences or credentials in logs.
Keep the deployed error display; do not mark the live issue closed on unit tests.

## BMS-DEV-56: no unsafe cleanup shortcut

Cancellation of interrupted staging remains open. The worker cancel path reads
status before publishing cancellation intent, but staging may not have written
status or its full envelope. Missing status, an idle GPU, elapsed time, or absence
of a visible supervisor does not prove that delayed staging/launch cannot write.
A supported fix needs a durable staging owner, cancellation/start serialization,
late-writer exclusion, and an identity-bound quiescence receipt before retirement.
Do not manufacture a terminal receipt, delete an attempt directory, or free a
lease solely because status.json is absent. This candidate makes no such change.
No worker, provider, running service or database was contacted or altered here.

## Verification and promotion

Run the new modules and the existing bundle, MSA, resume and remote-admission
selections in the locked API environment and route-free test namespace. Run the
mounted Fold-CP/approval and adjacent submission tests with the locked pnpm
workspace. Record baseline and candidate JUnit results separately. In particular,
the Hermes report names a pre-existing CM compiler failure in
`test_remote_bundle_path_gaps`; compare actual case identity and failure rather
than skipping it or treating a nonzero exit as a pass.

After source/tests/docs are final, create a record-free freeze commit and run the
canonical implementation-record builder against that commit's archive and raw
commit object. Commit the generated record separately, and require the updater's
validator plus checked-in authority test to accept the exact resulting revision.
Keep runtime/scientific acceptance fields unverified/open. Any integration onto
a changed test tip requires reconciliation and regeneration, not taking either
side's stale record. Production main is outside this change.

Manual follow-up: fresh generated-config and explicit source-config launches;
invalid historical resume rejected before writes; preview followed by explicit
approval and identical final POST; result return through native BMS mechanisms.
Do not declare cancellation/restart acceptance until BMS-DEV-56 has its own fix
and failure-injection evidence. BMS-DEV-51/52 remain outside this candidate.
