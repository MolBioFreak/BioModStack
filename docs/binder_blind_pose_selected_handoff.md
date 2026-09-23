# Selected blind-pose handoff (base `0b2be3bd8c62a3cdf777f30c8f99d3fb83f33b51`)

This isolated commit owns only `platform/api/routers/binder_blind_pose.py`,
`platform/api/services/binder_blind_pose_selected.py` and its tests. It does **not**
claim production route admission or native GPU qualification. Request: `POST
/api/blind-pose/selected` with `{source_job_id, design_ids: [...],
binder_chains: {design_id: [chain]}, target_chains: [chain], settings:
{model_variant: fast|full, model_id_or_path, num_loops,
num_sampling_steps, num_diffusion_samples, seed?}}`. The target sequence is
resolved from the source Job's declared `params.target_pdb`; arbitrary target
paths are not accepted from the client. Only exact source-Job-owned Design IDs
may be selected. Result: `GET /api/blind-pose/{job_id}/result` after publication.
The model-owned receipt preserves selected Design IDs, source/target content
hashes, native sample IDs, raw metrics, artifacts and an explicit unclassified
state. It does not label Boltz/Protenix native-pose scores blind.

## Shared-file integrator: exact proposed minimal hunks

Do not apply only a subset: the route deliberately returns 503 until the
scheduler points to the selected workflow. These are proposed handoff hunks,
**not applied or tested in the shared files**:

```diff
*** platform/api/main.py
@@
 from frustrampnn_upload_limit import FrustraMPNNUploadLimitMiddleware
+from routers import binder_blind_pose
@@
 app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
+app.include_router(binder_blind_pose.router, prefix="/api/blind-pose", tags=["blind-pose"])
*** platform/api/services/nextflow.py
@@ MODEL_MODE_WORKFLOW_ENTRYPOINTS
     ("esmfold2", "complex"): STRUCTURE_PREDICTION_ENTRYPOINT,
+    ("esmfold2", "blind_pose"): "workflows/binder_blind_pose.nf",
*** platform/api/services/result_ingester.py
@@ async def ingest_job_results(...): near line 5015, before generic ESMFold2 dispatch
     if current_job is not None and current_job.model_id == "bindcraft2":
         from services.bindcraft2_publication import publish_native_results
         return await publish_native_results(current_job, output_path, session, commit=False)
+    if current_job is not None and current_job.model_id == "esmfold2" and current_job.mode == "blind_pose":
+        from services.binder_blind_pose_selected import publish_selected, read_selected
+        await publish_selected(current_job, output_path, session)
+        await read_selected(current_job, session)
+        return 0  # Native assessment records are attached; no new Design.
```

Also add an explicit `blind_pose` mode to `platform/api/config/models/esmfold2.yaml`
with typed native settings (existing ESMFold2 `model_variant`,
`model_id_or_path`, `num_loops`, `num_sampling_steps`,
`num_diffusion_samples`, `seed`) and selected inputs, without changing
existing `predict`/`complex` semantics; ensure generic ESMFold2 server
compilation does not rewrite this mode to structure-prediction params.
`nextflow_schema.json` must declare the selected workflow's parameters if the
current Nextflow config validator applies. The process currently uses GPU and
`--device cuda`; a CPU fixture runner tests the selected request/result
contract, not an actual native inference.

Before exposing a production launcher: preserve exact typed settings in saved
jobs, implement the browser's selected Design and binder/target chain controls,
verify result finalizer commit/readback and remote staged-input transport,
then run native local/remote model qualification. Never infer pass/fail from
iptm or other raw metrics without an operator-versioned policy. Never use
Boltz/Protenix native-pose confidence as if it were sequence-only blind
cofold evidence.
