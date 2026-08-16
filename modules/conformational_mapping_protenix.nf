nextflow.enable.dsl = 2

process PrepareProtenixExecution {
    tag "cm-protenix-preflight:${request_id}"
    stageInMode 'copy'

    input:
    tuple val(request_id), path(request_root)

    output:
    tuple val(request_id), path('protenix_preflight'), emit: prepared

    script:
    def image_path = params.protenix_container_path ?: "${params.container_dir}/protenix.sif"
    """
    #!/bin/bash
    set -euo pipefail
    REQUEST_ROOT="${request_root}"
    REQUEST="\$REQUEST_ROOT/cm_request_v1.json"
    REGISTRY="\$REQUEST_ROOT/cm_runtime_registry_v1.json"
    test -f "\$REQUEST"
    test -f "\$REQUEST_ROOT/cm_complex_snapshots_v1.json"
    test -f "\$REGISTRY"
    mkdir -p protenix_preflight/execution-snapshot
    mkdir -p protenix_preflight/request
    cp -a "\$REQUEST_ROOT"/. protenix_preflight/request/

    ${params.api_python} ${params.code_root}/scripts/prepare_runtime_image_attestation.py \
      --image "${image_path}" --registry "\$REGISTRY" \
      --snapshot protenix_preflight/runtime-image.sif \
      --receipt protenix_preflight/runtime-image-receipt.json
    ${params.api_python} ${params.code_root}/scripts/prepare_protenix_execution_snapshot.py \
      --registry "\$REGISTRY" --weights-root "${params.protenix_weights}" \
      --wrapper "${params.code_root}/scripts/run_protenix_inference.py" \
      --runtime-root protenix_preflight/execution-snapshot \
      --receipt protenix_preflight/execution-snapshot-receipt.json
    """
}

process CanonicalProtenixEnsemble {
    tag "cm-protenix:${request_id}"
    label 'Protenix'
    label 'gpu'
    container { "${preflight}/runtime-image.sif" }
    stageInMode 'copy'

    input:
    tuple val(request_id), path(preflight)

    output:
    tuple val(request_id), path('canonical_protenix'), emit: canonical
    path 'canonical_protenix/cm_native_artifacts_v1.json', emit: native_manifest
    path 'canonical_protenix/cm_ensemble_v1.json', emit: ensemble_manifest

    script:
    """
    #!/bin/bash
    set -euo pipefail
    PREFLIGHT="${preflight}"
    REQUEST="\$PREFLIGHT/request/cm_request_v1.json"
    SNAPSHOTS="\$PREFLIGHT/request/cm_complex_snapshots_v1.json"
    REGISTRY="\$PREFLIGHT/request/cm_runtime_registry_v1.json"
    IMAGE_RECEIPT="\$PREFLIGHT/runtime-image-receipt.json"
    EXECUTION_RECEIPT="\$PREFLIGHT/execution-snapshot-receipt.json"
    SNAPSHOT_ROOT="\$PREFLIGHT/execution-snapshot"
    test -f "\$REQUEST"
    test -f "\$SNAPSHOTS"
    test -f "\$REGISTRY"
    test -f "\$IMAGE_RECEIPT"
    test -f "\$EXECUTION_RECEIPT"

    mkdir -p native_protenix/runtime native_protenix/predictions
    cp "\$IMAGE_RECEIPT" native_protenix/runtime/runtime-image-receipt.json
    cp "\$EXECUTION_RECEIPT" native_protenix/runtime/execution-snapshot-receipt.json
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
    CHECKPOINT_REL="\$(python3 - "\$REGISTRY" <<'PY'
import json, sys
from pathlib import PurePosixPath
value = json.load(open(sys.argv[1], encoding='utf-8'))['checkpoint_relative_path']
path = PurePosixPath(value)
if path.is_absolute() or any(part in {'', '.', '..'} for part in path.parts):
    raise SystemExit('unsafe checkpoint relative path')
print(path.as_posix())
PY
)"
    CHECKPOINT_SNAPSHOT="\$SNAPSHOT_ROOT/\$CHECKPOINT_REL"
    WRAPPER="\$SNAPSHOT_ROOT/bms-wrapper/run_protenix_inference.py"
    test -f "\$CHECKPOINT_SNAPSHOT"
    test -f "\$WRAPPER"

    mkdir -p protenix_runtime_weights
    for entry in /protenix_weights/*; do
      [ -e "\$entry" ] || continue
      name="\$(basename "\$entry")"
      [ "\$name" = "\$(printf '%s' "\$CHECKPOINT_REL" | cut -d/ -f1)" ] && continue
      ln -s "\$entry" "protenix_runtime_weights/\$name"
    done
    mkdir -p "protenix_runtime_weights/\$(dirname "\$CHECKPOINT_REL")"
    cp --reflink=auto "\$CHECKPOINT_SNAPSHOT" "protenix_runtime_weights/\$CHECKPOINT_REL"
    export PROTENIX_ROOT_DIR="\$PWD/protenix_runtime_weights"
    export XDG_CACHE_HOME="\$PROTENIX_ROOT_DIR/common"
    export TRITON_CACHE_DIR="\$PROTENIX_ROOT_DIR/triton"
    export MPLCONFIGDIR="\$PROTENIX_ROOT_DIR/matplotlib"
    export PYTHONNOUSERSITE=1 PIP_NO_USER=1

    EXTRA=()
    if [ "\$USE_DEFAULT" = false ]; then
      CYCLES="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_policy"]["n_cycle"])' "\$REQUEST")"
      STEPS="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_policy"]["n_step"])' "\$REQUEST")"
      EXTRA+=(--cycle "\$CYCLES" --step "\$STEPS")
    fi
    COMMAND_ARGS=(
      --input prepared_protenix/protenix_input.json
      --out_dir native_protenix/predictions
      --model_name protenix-v2 --seeds "\$SEEDS" --sample "\$SAMPLES"
      --use_default_params "\$USE_DEFAULT"
      --use_msa "\$USE_MSA" --use_template "\$USE_TEMPLATE" --use_rna_msa "\$USE_RNA_MSA"
      --cm-coordinate-ledger native_protenix/cm_protenix_coordinate_ledger_v1.jsonl
      --cm-coordinate-context prepared_protenix/cm_protenix_coordinate_context_v1.json
      "\${EXTRA[@]}"
    )
    python3 - native_protenix/runtime/command.json "\${COMMAND_ARGS[@]}" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(['run_protenix_inference.py', *sys.argv[2:]], separators=(',', ':')), encoding='utf-8')
PY
    STARTED_AT="\$(python3 -c 'from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat().replace("+00:00","Z"))')"
    python3 "\$WRAPPER" "\${COMMAND_ARGS[@]}" 2>&1 | tee native_protenix/runtime/process.log

    cat > native_protenix/runtime/global-artifacts.json <<'JSON'
[
  {"semantic_role":"runtime_input","relative_path":"runtime/input.json"},
  {"semantic_role":"feature_policy","relative_path":"runtime/feature-policy.json"},
  {"semantic_role":"log","relative_path":"runtime/process.log"},
  {"semantic_role":"runtime_config","relative_path":"runtime/config.json"},
  {"semantic_role":"composition_audit","relative_path":"runtime/composition-audit.json"},
  {"semantic_role":"coordinate_ledger","relative_path":"cm_protenix_coordinate_ledger_v1.jsonl"},
  {"semantic_role":"coordinate_context","relative_path":"runtime/coordinate-context.json"},
  {"semantic_role":"preprocessing_record","relative_path":"runtime/preprocessing-record.json"},
  {"semantic_role":"msa_record","relative_path":"runtime/msa-record.json"},
  {"semantic_role":"template_record","relative_path":"runtime/template-record.json"},
  {"semantic_role":"runtime_attestation","relative_path":"runtime/protenix-runtime-attestation.json"},
  {"semantic_role":"runtime_image_receipt","relative_path":"runtime/runtime-image-receipt.json"},
  {"semantic_role":"execution_snapshot_receipt","relative_path":"runtime/execution-snapshot-receipt.json"}
]
JSON
    RUNTIME_IMAGE="\$PREFLIGHT/runtime-image.sif"
    test -f "\$RUNTIME_IMAGE"
    python3 ${params.code_root}/scripts/attest_protenix_runtime.py \
      --registry "\$REGISTRY" --image-receipt "\$IMAGE_RECEIPT" \
      --runtime-image "\$RUNTIME_IMAGE" --checkpoint "\$PROTENIX_ROOT_DIR/\$CHECKPOINT_REL" \
      --wrapper "\$WRAPPER" --execution-receipt "\$EXECUTION_RECEIPT" \
      --command-json native_protenix/runtime/command.json \
      --global-artifacts-json native_protenix/runtime/global-artifacts.json \
      --started-at "\$STARTED_AT" --model-name protenix-v2 \
      --output native_protenix/runtime/protenix-runtime-attestation.json
    ${params.api_python} ${params.code_root}/scripts/finalize_protenix_conformational_mapping.py \
      --request "\$REQUEST" --snapshots "\$SNAPSHOTS" \
      --native-root native_protenix \
      --runtime native_protenix/runtime/protenix-runtime-attestation.json \
      --out canonical_protenix
    """
}

workflow CONFORMATIONAL_MAPPING_PROTENIX {
    take:
    request_tuples

    main:
    PrepareProtenixExecution(request_tuples)
    CanonicalProtenixEnsemble(PrepareProtenixExecution.out.prepared)

    emit:
    canonical = CanonicalProtenixEnsemble.out.canonical
    native_manifest = CanonicalProtenixEnsemble.out.native_manifest
    ensemble_manifest = CanonicalProtenixEnsemble.out.ensemble_manifest
}
