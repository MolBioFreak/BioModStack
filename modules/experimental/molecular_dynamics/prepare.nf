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
    path 'preparation_bundle', emit: preparation_bundle

    script:
    """
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.cli validate \
      --config ${source_config} \
      --output normalized_config.json \
      --base-dir ${config_base_dir}
    python3 - <<'PY'
import json
import os
from pathlib import Path
from scripts.bms_md.chemistry.prepare import build_preparation_bundle

config = json.loads(Path('normalized_config.json').read_text())
bundle = Path('preparation_bundle')
if config.get('schema') == 'bms.md.job.v2':
    chemistry = config['chemistry']
    runtime_image = Path(os.environ.get(
        'BMS_MD_PREPARATION_SIF', '/mnt/BioModStack/apptainer/md-preparation-v1.sif'
    ))
    if not runtime_image.is_file():
        raise SystemExit('pinned MD preparation SIF is unavailable')
    manifest = build_preparation_bundle(
        source_structure=Path(config['input']['structure']),
        destination=bundle,
        profile_id=chemistry['profile_id'],
        profile_sha256=chemistry['profile_sha256'],
        runtime_lock=Path(os.environ.get(
            'BMS_MD_PREPARATION_RUNTIME_LOCK',
            '/mnt/BioModStack/md-preparation/env-v1-explicit.txt',
        )),
        worker_command=['apptainer', 'exec', str(runtime_image), 'python'],
        runtime_image=runtime_image,
    )
    bundle_sha = manifest['bundle_sha256']
else:
    bundle.mkdir()
    (bundle / 'not_applicable.json').write_text(
        json.dumps({'schema': 'bms.md.preparation-not-applicable.v1'}) + chr(10)
    )
    bundle_sha = None
Path('md_metadata.json').write_text(json.dumps({
    'engine': config['engine'],
    'replicas': int(config['replicas']),
    'schema': config['schema'],
    'preparation_bundle_sha256': bundle_sha,
}, sort_keys=True) + chr(10))
PY
    """
}
