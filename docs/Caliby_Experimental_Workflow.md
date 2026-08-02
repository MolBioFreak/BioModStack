# Caliby Integration Status

Caliby is a **fixed-backbone sequence designer**. It is not a de novo backbone generator.

## Primary product target

Caliby's first-priority BioModStack placement is the **general de novo binder workflow** (`binder_design` migration target), where it can redesign sequences on compatible binder backbones generated upstream and then hand candidates to independent complex validation.

That general binder integration is not exposed yet. It requires complete binder chain-role, lineage, result-ingestion, validation, and real GPU acceptance.

## Secondary validated use: retired antibody workflow

Caliby is also scientifically useful inside:

**Job Launcher → retired antibody workflow → Sequence Designer → Caliby**

In that path it performs fixed-backbone sequence design on nanobody candidates produced upstream. It is not a peer backbone generator to RFantibody, BoltzGen, or seeded PPIFlow.

## Installed checkpoint contract

The current runtime exposes only:

- `soluble_caliby_v1`

Other historical checkpoint names are not selectable. The compatibility-only `caliby_experimental` registry model is disabled.

Runtime preflight occurs before any Caliby package import or model load and checks:

- `MODEL_PARAMS_DIR`;
- the expected checkpoint file;
- writable Hugging Face cache directories;
- explicit download authorization.

Implicit checkpoint download is disabled unless explicitly authorized.

## Producer and review contract

Nested outputs identify:

- `generator_family: caliby`;
- `artifact_class: sequence_designed_complex`;
- `result_set: sequence_designs`;
- `review_profile_id: sequence_design_v1`;
- source-backbone lineage and artifact identity;
- Caliby Potts-energy scoring/selection semantics.

The server accepts Caliby-owned producer authority only for `antibody_child + generator_family=caliby`. The same payload fails closed for unrelated workflows.

Selecting Caliby from either nanobody sequence-designer selector automatically targets the `post_caliby` review gate unless the operator already chose another gate.

## Retired standalone surface

The standalone workflow/module/template/profile/API entrypoint and frontend inventory card were retired. Direct API job creation and root Nextflow compatibility launches using `caliby_experimental` fail closed before normal execution.

Historical metadata may remain for old-job compatibility but is disabled for fresh discovery.

## General binder integration requirements

Before Caliby becomes selectable in the general binder workflow:

1. Accept canonical binder backbones plus binder/target chain roles.
2. Preserve source-backbone and mutation lineage.
3. Emit one authoritative Design row per accepted sequence-designed complex.
4. Keep Caliby Potts scores distinct from independent binding validation metrics.
5. Run independent complex validation after sequence design.
6. Expose sequence mutations, Caliby provenance, validator provenance, and interface metrics in browser results.
7. Pass stub, managed Nextflow preview, ingestion, real GPU runtime, and browser-visible acceptance.

## Internal implementation

- runtime utilities: [`scripts/caliby_runtime.py`](../scripts/caliby_runtime.py)
- nested runner: [`scripts/run_caliby_sequence_design.py`](../scripts/run_caliby_sequence_design.py)
- nested module: [`modules/caliby.nf`](../modules/caliby.nf)
- parent workflow: [`workflows/antibody_child.nf`](../workflows/antibody_child.nf)
- compatibility metadata: [`platform/api/config/models/caliby_experimental.yaml`](../platform/api/config/models/caliby_experimental.yaml)
