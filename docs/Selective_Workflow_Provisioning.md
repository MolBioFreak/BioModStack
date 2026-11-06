# Selective workflow asset preparation

The worker's **Prepare → Workflow components → Structure Prediction** controls
select provisioning assets, not inference settings. Fresh structure preparation
selects **Protenix and FrustraMPNN**. ESMFold2, Boltz-2 and Fold-CP can each be
selected or deselected, as can the initial two components. **All components
(full workflow)** retains the existing whole-pack operation. Binder and De Novo
whole-pack defaults are unchanged.

Preview shows the selected native dependency closure, download/storage estimates
and blockers. Editing a component invalidates that preview. Start provision uses
the same selection and digest; the one-click preparation action performs the same
preview/start pair. Preparation never launches a Job, changes its settings or
removes already downloaded components. Dependencies shared by selected components
are deduplicated, and incomplete combined consumer layouts are not published.
Generic Protenix bulk template-search corpora remain outside the existing pack.

## Typed agent interface

`GET /api/execution-targets/provision/catalog` returns `components` on workflow
pack entries: `model_id`, registry-owned `label`, and `selected_by_default`.
Do not send this discovery metadata back as request fields.

POST the following selection to
`/api/execution-targets/{target_id}/provision/preview`:

```json
{
  "kind": "workflow_pack",
  "workflow_id": "structure_prediction",
  "component_ids": ["protenix", "frustrampnn"]
}
```

POST the same selection plus the returned `preview_sha256` to
`/api/execution-targets/{target_id}/provision`. Component identities are validated
against the workflow catalog and canonicalized as a sorted unique set. Omitted
or null `component_ids` preserves historical full-pack semantics; `[]` means no
components, never an implicit full pack. The browser requires a selection before
preparing. Receipt and retry selection retain the chosen component set.

## Qualification

`tests/test_workflow_pack_components.py` checks every structure subset, native
closure, full-pack equivalence, shared-layout filtering, typed catalog and
OpenAPI exposure, digest mismatch refusal, real offline provisioning/readback,
empty selection and retention of earlier assets after a disjoint preparation.
`independentProvisionMounted.test.tsx` uses the actual server catalog and real
parent controls/Axios transport, checks defaults, ESMFold2 on/off, all-components,
and stale or wrong-subset responses. Set `BMS_PROVISION_COMPONENT_WIRE` to a
private file in that mounted run and replay it with the API test to compare the
exact browser-emitted selections with server dependencies. These are isolated
inert-asset tests, not remote downloads or inference acceptance.
