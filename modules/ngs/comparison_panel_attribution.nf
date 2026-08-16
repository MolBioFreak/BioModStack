/* Optional, separate comparison alignment.  This must never replace or merge
 * the primary expected-plasmid BAM, and it is intentionally unavailable for
 * malformed or non-local snapshots. */
process ComparisonPanelAttribution {
    label 'local_cpu'
    publishDir "${params.out_dir}/comparison_panel", mode: 'copy'
    tag 'comparison_panel_attribution'

    input:
    path fastq
    path expected_reference
    path snapshot

    output:
    path 'comparison_panel.fasta', emit: reference
    path 'comparison_panel_expected_reference.fasta', emit: expected_reference
    path 'comparison_panel_source.fastq', emit: source_fastq
    path 'comparison_panel_normalized.fastq', emit: normalized_fastq
    path 'comparison_panel_occurrence_map.json', emit: occurrence_map
    path 'comparison_panel.bam', emit: bam
    path 'comparison_panel.bam.bai', emit: bai
    path 'comparison_panel_summary.json', emit: summary

    script:
    def codeRoot = params.code_root ?: projectDir
    def minMapq = (params.comparison_panel_min_mapq ?: 20) as Integer
    def minScoreMargin = (params.comparison_panel_min_score_margin ?: 10) as Integer
    """
    set -euo pipefail
    python3 '${codeRoot}/scripts/build_comparison_panel_attribution.py' \\
      --snapshot '${snapshot}' --expected-fasta '${expected_reference}' \\
      --fastq '${fastq}' \\
      --normalized-fastq comparison_panel_normalized.fastq \\
      --occurrence-map comparison_panel_occurrence_map.json \\
      --source-fastq-artifact comparison_panel_source.fastq \\
      --expected-reference-artifact comparison_panel_expected_reference.fasta \\
      --combined-fasta comparison_panel.fasta --summary comparison_panel_prepare.json \\
      --min-mapq ${minMapq} --min-score-margin ${minScoreMargin}
    minimap2 -ax '${params.fastq_minimap2_preset ?: 'map-ont'}' comparison_panel.fasta comparison_panel_normalized.fastq | \\
      samtools sort -o comparison_panel.bam
    samtools index comparison_panel.bam
    python3 '${codeRoot}/scripts/build_comparison_panel_attribution.py' \\
      --snapshot '${snapshot}' --expected-fasta '${expected_reference}' \\
      --fastq '${fastq}' \\
      --normalized-fastq comparison_panel_normalized.fastq \\
      --occurrence-map comparison_panel_occurrence_map.json \\
      --source-fastq-artifact comparison_panel_source.fastq \\
      --expected-reference-artifact comparison_panel_expected_reference.fasta \\
      --combined-fasta comparison_panel.fasta --panel-bam comparison_panel.bam \\
      --samtools samtools --min-mapq ${minMapq} --min-score-margin ${minScoreMargin} --summary comparison_panel_summary.json
    """
}
