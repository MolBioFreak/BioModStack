# Importing retained native Protenix predictions

Use the existing `/api/jobs/imports/external/preview` and `/api/jobs/imports/external`
endpoints with `provider_hint` / `provider` set to `protenix_native`. Upload the
retained files through `/api/files/upload` under a managed input directory first.
Preview returns the content fingerprint required by create. The existing durable
import worker publishes a completed **external_import** Job, never a scheduler
execution, and one Design per explicitly named native sample. Retry and duplicate
requests use the existing external-import identity rules.

The source directory contains `protenix-import.json`, an **operator-authored
custody descriptor**, not an execution receipt:

```json
{
  "schema": "bms.protenix-external-import.v1",
  "id": "my-retained-run",
  "model_version": "protenix-v2",
  "native_input_path": "native-input.json",
  "entities": [
    {"entity_index": 0, "molecule_type": "protein", "chain_ids": ["A"], "sequence": "ACD"}
  ],
  "settings": {"seeds": [42], "samples": 1, "steps": 200, "cycles": 10, "use_template": false},
  "samples": [
    {"sample_id": "seed_42/sample_0", "structure_path": "output/task_sample_0.cif",
     "summary_path": "output/task_summary_confidence_sample_0.json",
     "aligned_error_path": "output/task_full_data_sample_0.json"}
  ],
  "evidence_paths": ["launch.json", "msa/target.a3m"],
  "original_paths": {"source_directory": "/original/run"}
}
```

All file paths are source-relative, regular files without symlinks. List actual
retained evidence only. `settings` records historical external settings; it does
not author or compile a new model run. Preserve native modifications and original
input bytes, and explicitly identify output chains in `entities`. No sequence
conversion, inference, MSA search, coordinate rewrite or binder-role inference is
performed. Optional native rank/best-sample labels must reflect evidence; absent
values remain unknown. Native sample suffixes are not diffusion generation order.

Import custody retains every listed file byte-for-byte with relative paths and
hashes, including native CIF, summary, full-data, input and MSA evidence. The
existing Protenix scientific consumer uses import hashes instead of fabricated
scheduler/remote receipts. Atom pLDDT and token PAE retain their native axes;
modified residues need not be one token per residue. Optional confidence mapping
failure makes that chart unavailable, not a reason to rerun or discard structures.
Native ranking_score remains native ranking, not a generic confidence_score.
Project membership is managed separately through existing Project owners.
