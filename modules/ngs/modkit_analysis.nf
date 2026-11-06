// Shared optional tandem analysis: preserve the original tagged BAM.
include { ValidateModifiedBaseBam; ModkitPileup } from './modkit_pileup.nf'
include { ModkitSummary } from './modkit_summary.nf'
include { reportStage } from './stage_reporting.nf'

workflow ModkitAnalysis {
    take:
    aligned
    reference
    main:
    ValidateModifiedBaseBam(aligned)
    ModkitPileup(ValidateModifiedBaseBam.out.bam, reference)
    ModkitSummary(ValidateModifiedBaseBam.out.bam)
    ModkitPileup.out.log.subscribe { ignored ->
        reportStage(params, "modkit_pileup", [
            "${params.out_dir}/methylation/modified_base_input.bam",
            "${params.out_dir}/methylation/modified_base_input.bam.bai",
            "${params.out_dir}/methylation/modified_base_tag_check.log",
            "${params.out_dir}/methylation/methylation.bed",
            "${params.out_dir}/methylation/pileup.log",
        ])
    }
    ModkitSummary.out.log.subscribe { ignored ->
        reportStage(params, "modkit_summary", [
            "${params.out_dir}/methylation/modkit_summary.tsv",
            "${params.out_dir}/methylation/summary.log",
        ])
    }
}
