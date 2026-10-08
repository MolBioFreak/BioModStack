nextflow.enable.dsl=2

process MD_ANALYZE_REPLICA {
    tag "md-analysis:${replica_index}"
    label 'MolecularDynamicsAnalysis'
    container params.md_analysis_container ?: "${params.data_root}/apptainer/md-analysis-1.0.0.sif"
    publishDir "${params.out_dir}/analysis", mode: 'copy', overwrite: true
    errorStrategy 'retry'
    maxRetries 2

    input:
    tuple val(replica_index), path(replica_dir), val(manifest_sha256)

    output:
    tuple val(replica_index), path("md_analysis_replica_${replica_index}.json"), emit: reports
    tuple val(replica_index), path("md_analysis_replica_${replica_index}.artifacts.json"), emit: artifact_manifests
    tuple val(replica_index), path("md_analysis_replica_${replica_index}.timeseries.parquet"), emit: timeseries
    tuple val(replica_index), path("md_analysis_replica_${replica_index}.residue_metrics.parquet"), emit: residue_metrics

    script:
    """
    runtime_sha256='${params.md_analysis_sif_sha256 ?: ''}'
    if ! printf '%s\n' "\${runtime_sha256}" | grep -Eq '^[0-9a-f]{64}\$'; then
      printf 'MD_ANALYSIS_RUNTIME_IDENTITY_INVALID: expected 64 lowercase hex characters\n' >&2
      exit 64
    fi

    printf '%s  %s\n' '${manifest_sha256}' '${replica_dir}/manifest.json' | sha256sum --check --strict
    printf '%s  %s\n' "\${runtime_sha256}" '/opt/bms-md-analysis-runtime.sif' | sha256sum --check --strict
    export BMS_MD_ANALYSIS_SIF_SHA256="\${runtime_sha256}"
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    implementation_sha256='${params.md_analysis_implementation_sha256 ?: ''}'
    if ! printf '%s\n' "\${implementation_sha256}" | grep -Eq '^[0-9a-f]{64}\$'; then
      printf 'MD_ANALYSIS_IMPLEMENTATION_IDENTITY_INVALID: expected 64 lowercase hex characters\n' >&2
      exit 65
    fi
    actual_implementation_sha256="\$(python3 -c 'from scripts.bms_md.analysis import _implementation_sha256; print(_implementation_sha256())')"
    if [ "\${actual_implementation_sha256}" != "\${implementation_sha256}" ]; then
      printf 'MD_ANALYSIS_IMPLEMENTATION_IDENTITY_MISMATCH\n' >&2
      exit 66
    fi
    export BMS_MD_ANALYSIS_IMPLEMENTATION_SHA256="\${implementation_sha256}"
    python3 -m scripts.bms_md.cli analyze \
      --manifest '${replica_dir}/manifest.json' \
      --output 'md_analysis_replica_${replica_index}.json' \
      --runtime-sha256 "\${runtime_sha256}" \
      --stride ${params.md_analysis_stride} \
      --max-points ${params.md_analysis_max_points}
    """
}
