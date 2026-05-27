/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process DoradoBasecall {
    label 'dorado_gpu'
    label 'gpu'
    publishDir "${params.out_dir}/basecall", mode: 'copy'
    tag "basecall"

    input:
    path pod5_dir

    output:
    path "calls.bam", emit: bam
    path "basecall.log", emit: log
    path "sequencing_summary.tsv", emit: summary, optional: true

    script:
    def model = params.dorado_model ?: 'sup'
    def normalizedModifiedBases = params.modified_bases?.toString()?.trim()
    if (normalizedModifiedBases == '6mA 5mC') {
        normalizedModifiedBases = '6mA 4mC_5mC'
    }
    def modBases = normalizedModifiedBases && normalizedModifiedBases != 'none' ? "--modified-bases ${normalizedModifiedBases}" : ''
    def minQscore = params.min_qscore ? "--min-qscore ${params.min_qscore}" : ''
    def trimAdapt = params.trim_adapters != false ? '' : '--no-trim'
    def emitSummary = params.emit_summary != false ? '--emit-summary' : ''
    def batchSize = params.dorado_batch_size ? "--batchsize ${params.dorado_batch_size}" : ''
    def doradoDevice = (params.dorado_device ?: 'cuda:0').toString().trim()
    if (!doradoDevice) {
        doradoDevice = 'cuda:0'
    }
    """
    mkdir -p /weights/dorado

    dorado basecaller \\
        ${model} \\
        ${pod5_dir} \\
        --models-directory /weights/dorado \\
        ${modBases} \\
        ${minQscore} \\
        ${trimAdapt} \\
        ${emitSummary} \\
        ${batchSize} \\
        --device ${doradoDevice} \\
        > calls.bam \\
        2> basecall.log

    # Dorado emits sequencing_summary.txt by default; normalize to .tsv for pipeline consumers.
    if [[ -f sequencing_summary.txt && ! -f sequencing_summary.tsv ]]; then
        mv sequencing_summary.txt sequencing_summary.tsv
    fi
    """
}
