nextflow.enable.dsl=2

process MD_ANALYZE_REPLICA {
    tag "md-analysis:${replica_index}"
    label 'MolecularDynamicsAnalysis'
    container params.md_analysis_container ?: "${params.container_dir}/md-analysis-1.0.0.sif"
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
    printf '%s  %s\n' '${manifest_sha256}' '${replica_dir}/manifest.json' | sha256sum --check --strict
    export PYTHONPATH="${params.code_root}:\${PYTHONPATH:-}"
    python3 -m scripts.bms_md.cli analyze \
      --manifest '${replica_dir}/manifest.json' \
      --output 'md_analysis_replica_${replica_index}.json' \
      --stride ${params.md_analysis_stride} \
      --max-points ${params.md_analysis_max_points}
    """
}
