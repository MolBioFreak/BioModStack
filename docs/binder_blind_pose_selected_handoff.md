# Selected blind-pose action handoff (base `342305d76413aa8a7914152b80e3208247615928`)

The isolated action owns selected materialization, ESMFold2 `blind_pose` mode and Nextflow compiler mapping, native CIF binder-sequence extraction, and remote selected-directory transport. The source target comes from the independently declared `target_pdb` or BC2 `bindcraft2_settings.targets[].target_path`, never candidate complex coordinates. `POST /api/blind-pose/selected` accepts `{source_job_id, design_ids, binder_chains, target_chains, target_name?, settings}`; `target_name` disambiguates multiple BC2 declared targets. `GET /api/blind-pose/{job_id}/result` reads attached native samples, always unclassified.

## Parent-owned minimal integration hunks

```diff
*** platform/api/main.py
@@
 from frustrampnn_upload_limit import FrustraMPNNUploadLimitMiddleware
+from routers import binder_blind_pose
@@
 app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
+app.include_router(binder_blind_pose.router, prefix="/api/blind-pose", tags=["blind-pose"])
*** platform/api/services/result_ingester.py
@@
     if current_job is not None and current_job.model_id == "bindcraft2":
         from services.bindcraft2_publication import publish_native_results
         return await publish_native_results(current_job, output_path, session, commit=False)
+    if current_job is not None and current_job.model_id == "esmfold2" and current_job.mode == "blind_pose":
+        from services.binder_blind_pose_selected import publish_selected, read_selected
+        await publish_selected(current_job, output_path, session)
+        await read_selected(current_job, session)
+        return 0  # Native evidence attached; no new Design.
```

The parent should check successful-job finalization handles a zero-new-Design diagnostic and reads back the attached receipt. Do not recast generic ESMFold2, Boltz or Protenix native-pose validation as blind cofold. CPU fixtures and pinned Nextflow `inspect` are not native ESMFold2 GPU qualification. No push, deployment, rental or GPU campaign was performed.
