# PL-07: reference-free reconstruction is held, not silently substituted for verification

Status: integration contract and guard tests only; the reconstruction workflow is
NOT implemented, registered, deployed, or scientifically qualified by this commit.
This is the explicitly unfinished extension in the corrective commit series.

## Existing behavior that must remain intact

`wf_clone_validation` and `ont_construct_screening` require an authoritative
expected reference. Never bypass this with an empty file, dummy sequence, an
assembly relabeled operator-supplied, or a hidden mode inside the strict verifier.
Reconstruction of an unknown plasmid and verification of an expected construct
are distinct scientific operations. They may reuse the same assembly engine.

## Requirements for the follow-on implementation

1. Factor the pinned assembly invocation from expected-reference verification.
   The new entrypoint consumes qualified reads and an explicit size assumption;
   a 7 kb default must not silently become evidence of the unknown molecule's size.
2. Register a separate reconstruction intent in the existing global model and
   workflow contracts. Complete typed operator controls, agent API parity,
   preview/digest binding, retry persistence, launch compilation, artifact
   registration, and ordinary result reopening before enabling admission.
3. Keep final FASTA/FASTQ, candidate-selection evidence, annotation products,
   coverage, junction support and unresolved structures identifiable. Compare to
   an expected sequence only when the operator supplies one in a separate step.
4. Reuse the repaired model-provenance guard. Imported reads with unknown model
   history must not acquire HAC provenance from a polishing override. A separately
   supported exploratory mode needs its own visible limitations and qualification.
5. Do not collapse multiple candidates into a fabricated single circular result.
   Partial/ambiguous reconstructions remain readable. Assembly failure is not a
   clean sample; sequence-origin support alone does not prove physical topology.
6. Retain original read-population scope and selection/filtering receipts. Reads
   delivered only after consensus-alignment selection cannot establish sample purity.
7. Validate withheld-reference reconstruction externally against known truth and
   adversarial size/repeat/concatemer/multiple-candidate cases. Do not supply the
   truth reference to the reconstruction path in its own validation experiment.
8. Refresh the existing source-bound implementation record through its established
   builder/owner. Do not add a second runtime or qualification registry.

## Explicit non-claims

This commit supplies neither a live new workflow nor a biological accuracy claim.
It must not block release of a correctly scoped reference-backed review workflow.
The follow-on is accepted only after the complete request-to-result path and the
specific reconstruction claim pass qualification. No promotion or live run is
performed by this file or by the guard tests.
