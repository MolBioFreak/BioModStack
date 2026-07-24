nextflow.enable.dsl = 2

process CanonicalProtenixEnsemble {
    tag "cm-protenix:${request_id}"
    label 'Protenix'
    label 'gpu'
    stageInMode 'copy'

    input:
    tuple val(request_id), path(request_root)

    output:
    tuple val(request_id), path('canonical_protenix'), emit: canonical
    path 'canonical_protenix/cm_native_artifacts_v1.json', emit: native_manifest
    path 'canonical_protenix/cm_ensemble_v1.json', emit: ensemble_manifest

    script:
    """
    #!/bin/bash
    set -euo pipefail
    REQUEST="${request_root}/cm_request_v1.json"
    SNAPSHOTS="${request_root}/cm_complex_snapshots_v1.json"
    REGISTRY="${request_root}/cm_runtime_registry_v1.json"
    test -f "\$REQUEST"
    test -f "\$SNAPSHOTS"
    test -f "\$REGISTRY"

    mkdir -p native_protenix/runtime native_protenix/predictions
    ${params.api_python} ${params.code_root}/scripts/prepare_protenix_conformational_mapping.py \
      --request "\$REQUEST" --snapshots "\$SNAPSHOTS" --out prepared_protenix
    cp prepared_protenix/protenix_input.json native_protenix/runtime/input.json
    cp prepared_protenix/cm_protenix_composition_audits_v1.json native_protenix/runtime/composition-audit.json
    cp prepared_protenix/cm_protenix_coordinate_context_v1.json native_protenix/runtime/coordinate-context.json
    python3 - "\$REQUEST" native_protenix/runtime/feature-policy.json native_protenix/runtime/config.json <<'PY'
import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
Path(sys.argv[2]).write_text(json.dumps(request['feature_policy'], sort_keys=True, separators=(',', ':')), encoding='utf-8')
Path(sys.argv[3]).write_text(json.dumps(request['runtime_policy'], sort_keys=True, separators=(',', ':')), encoding='utf-8')
PY
    python3 - "\$REQUEST" native_protenix/runtime <<'PY'
import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
root = Path(sys.argv[2])
policy = request['feature_policy']
mode = policy['mode']
records = {
  'preprocessing-record.json': {'kind':'preprocessing','mode':mode,'status':'configured','policy':policy},
  'msa-record.json': {
    'kind':'msa_features','mode':mode,
    'protein_msa_enabled':policy['protein_msa_enabled'],
    'rna_msa_enabled':policy['rna_msa_enabled'],
    'status':'enabled' if policy['protein_msa_enabled'] or policy['rna_msa_enabled'] else 'disabled',
  },
  'template-record.json': {
    'kind':'template_features','mode':mode,
    'templates_enabled':policy['templates_enabled'],
    'status':'enabled' if policy['templates_enabled'] else 'disabled',
  },
}
for name, value in records.items():
    (root / name).write_text(json.dumps(value, sort_keys=True, separators=(',', ':')), encoding='utf-8')
PY
    SEEDS="\$(python3 -c 'import json,sys; print(",".join(map(str,json.load(open(sys.argv[1]))["ordered_seeds"])))' "\$REQUEST")"
    SAMPLES="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["samples_per_seed"])' "\$REQUEST")"
    USE_DEFAULT="\$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["runtime_policy"]["use_default_params"]).lower())' "\$REQUEST")"
    USE_MSA="\$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["feature_policy"]["protein_msa_enabled"]).lower())' "\$REQUEST")"
    USE_TEMPLATE="\$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["feature_policy"]["templates_enabled"]).lower())' "\$REQUEST")"
    USE_RNA_MSA="\$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["feature_policy"]["rna_msa_enabled"]).lower())' "\$REQUEST")"
    EXTRA=()
    if [ "\$USE_DEFAULT" = false ]; then
      CYCLES="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_policy"]["n_cycle"])' "\$REQUEST")"
      STEPS="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_policy"]["n_step"])' "\$REQUEST")"
      EXTRA+=(--cycle "\$CYCLES" --step "\$STEPS")
    fi
    export PROTENIX_ROOT_DIR=/protenix_weights
    export XDG_CACHE_HOME=/protenix_weights/common
    export TRITON_CACHE_DIR=/protenix_weights/triton
    export MPLCONFIGDIR=/protenix_weights/matplotlib
    export PYTHONNOUSERSITE=1 PIP_NO_USER=1
    STARTED_AT="\$(python3 -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat().replace("+00:00","Z"))')"
    python3 ${params.code_root}/scripts/run_protenix_inference.py \
      --input prepared_protenix/protenix_input.json \
      --out_dir native_protenix/predictions \
      --model_name protenix-v2 --seeds "\$SEEDS" --sample "\$SAMPLES" \
      --use_default_params "\$USE_DEFAULT" \
      --use_msa "\$USE_MSA" \
      --use_template "\$USE_TEMPLATE" \
      --use_rna_msa "\$USE_RNA_MSA" \
      --cm-coordinate-ledger native_protenix/cm_protenix_coordinate_ledger_v1.jsonl \
      --cm-coordinate-context prepared_protenix/cm_protenix_coordinate_context_v1.json \
      "\${EXTRA[@]}" 2>&1 | tee native_protenix/runtime/process.log

    python3 - "\$REQUEST" "\$REGISTRY" native_protenix runtime.json "\$STARTED_AT" <<'PY'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
registry = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
root = Path(sys.argv[3])
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
checkpoint = Path('/protenix_weights') / registry['checkpoint_relative_path']
if sha(checkpoint) != registry['checkpoint_sha256']:
    raise RuntimeError('installed Protenix checkpoint differs from authenticated registry')
runtime = {
  'backend_version': registry['backend_version'], 'backend_commit': registry['backend_commit'],
  'runtime_identity': registry['runtime_identity'],
  'container_digest': registry['container_digest'],
  'checkpoint_sha256': registry['checkpoint_sha256'], 'model_id': registry['model_id'],
  'started_at': sys.argv[5],
  'completed_at': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
  'command': ['run_protenix_inference.py', '--seeds', ','.join(map(str, request['ordered_seeds'])), '--sample', str(request['samples_per_seed'])],
  'global_artifacts': [
    {'semantic_role':'runtime_input','relative_path':'runtime/input.json'},
    {'semantic_role':'feature_policy','relative_path':'runtime/feature-policy.json'},
    {'semantic_role':'log','relative_path':'runtime/process.log'},
    {'semantic_role':'runtime_config','relative_path':'runtime/config.json'},
    {'semantic_role':'composition_audit','relative_path':'runtime/composition-audit.json'},
    {'semantic_role':'coordinate_ledger','relative_path':'cm_protenix_coordinate_ledger_v1.jsonl'},
    {'semantic_role':'coordinate_context','relative_path':'runtime/coordinate-context.json'},
    {'semantic_role':'preprocessing_record','relative_path':'runtime/preprocessing-record.json'},
    {'semantic_role':'msa_record','relative_path':'runtime/msa-record.json'},
    {'semantic_role':'template_record','relative_path':'runtime/template-record.json'},
  ],
}
Path(sys.argv[4]).write_text(json.dumps(runtime, sort_keys=True, separators=(',', ':')), encoding='utf-8')
PY
    ${params.api_python} ${params.code_root}/scripts/finalize_protenix_conformational_mapping.py \
      --request "\$REQUEST" --snapshots "\$SNAPSHOTS" \
      --native-root native_protenix --runtime runtime.json --out canonical_protenix
    """
}

workflow CONFORMATIONAL_MAPPING_PROTENIX {
    take:
    request_tuples

    main:
    CanonicalProtenixEnsemble(request_tuples)

    emit:
    canonical = CanonicalProtenixEnsemble.out.canonical
    native_manifest = CanonicalProtenixEnsemble.out.native_manifest
    ensemble_manifest = CanonicalProtenixEnsemble.out.ensemble_manifest
}
