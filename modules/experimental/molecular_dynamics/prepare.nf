nextflow.enable.dsl=2

process MD_PREPARE_CONFIG {
    tag "md-prepare:${params.job_id ?: 'unassigned'}"
    label 'MolecularDynamicsCpu'
    publishDir "${params.out_dir}/preparation", mode: 'copy', overwrite: true

    input:
    path source_config
    val config_base_dir

    output:
    path 'normalized_config.json', emit: normalized_config
    path 'md_metadata.json', emit: metadata

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.cli validate \
      --config ${source_config} \
      --output normalized_config.json \
      --base-dir ${config_base_dir}
    python3 - <<'PY'
import json
from pathlib import Path
config = json.loads(Path('normalized_config.json').read_text())
Path('md_metadata.json').write_text(json.dumps({
    'engine': config['engine'],
    'replicas': int(config['replicas']),
}, sort_keys=True) + chr(10))
PY
    """
}
