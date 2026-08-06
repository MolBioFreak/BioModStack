# Frustration-guided mutation reorchestration — future workflow contract

Status: planned; not implemented or exposed as an executable control.

## Scientific intent

Use exact persisted FrustraMPNN residue/mutation evidence to help an operator define an explicit mutation set, then evaluate a new lineage-linked sample. FrustraMPNN remains an analysis model; it does not silently redesign a sequence or assert that a higher/lower frustration score is universally beneficial.

## Required workflow boundary

1. Start from an immutable source `Design` and exact FrustraMPNN result.
2. Select evidence by stable dataset, job, invocation, chain, author residue, insertion code, native amino acid, and proposed mutation identity.
3. Record the operator-approved mutation set and the descriptive evidence/formula used to nominate it.
4. Materialize a new child `Design`; never modify or relabel the source `Design`.
5. Submit the child through the single Python BioModStack scheduler. No request-thread, browser-thread, direct CLI, or unmanaged inference.
6. Run the configured structure/design workflow and fresh FrustraMPNN analysis through first-class model entry points.
7. Persist parent/child lineage, scheduler receipt, source hashes, runtime/checkpoint identity, and before/after comparable multidimensional records.
8. Present deltas descriptively with missingness and threshold-policy identity; do not fabricate causal improvement.

## Release gates

- Explicit operator confirmation of the mutation set.
- Fail-closed exact source and residue identity validation.
- Bounded mutation count and incompatible-edit checks.
- Scheduler admission and physical-GPU authority.
- Immutable artifacts and atomic terminal persistence.
- Dataset-scale before/after comparison with point-to-source drill-down.
- No executable UI button until the API contract, lineage model, scheduler path, and focused end-to-end tests are accepted.
