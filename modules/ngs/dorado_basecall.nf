/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

def doradoShellQuote(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

def doradoBoundedInteger(rawValue, String name, int minimum, int maximum) {
    if (rawValue == null || !rawValue.toString().trim()) {
        return null
    }
    def text = rawValue.toString().trim()
    if (!(text ==~ /[0-9]+/)) {
        throw new IllegalArgumentException("${name} must be an integer between ${minimum} and ${maximum}")
    }
    def parsed = text.toBigInteger()
    if (parsed < minimum || parsed > maximum) {
        throw new IllegalArgumentException("${name} must be an integer between ${minimum} and ${maximum}")
    }
    return parsed.toString()
}

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
    def model = doradoShellQuote(params.dorado_model ?: 'sup')
    def normalizedModifiedBases = params.modified_bases?.toString()?.trim()
    if (normalizedModifiedBases == '6mA 5mC') {
        normalizedModifiedBases = '6mA 4mC_5mC'
    }
    def allowedModifiedBases = [
        '6mA 4mC_5mC',
        '6mA 5mC',
        '6mA',
        '5mC',
        '5mC_5hmC',
        '4mC_5mC',
        '5mCG_5hmCG',
        '5mCG',
        '5hmCG',
        'none',
    ] as Set
    if (normalizedModifiedBases && !allowedModifiedBases.contains(normalizedModifiedBases)) {
        throw new IllegalArgumentException("Unsupported modified_bases preset: ${normalizedModifiedBases}")
    }
    def modBaseArgs = normalizedModifiedBases?.split(/\s+/)?.collect { value -> doradoShellQuote(value) }?.join(' ')
    def modBases = normalizedModifiedBases && normalizedModifiedBases != 'none' ? "--modified-bases ${modBaseArgs}" : ''
    def minQscoreValue = doradoBoundedInteger(params.min_qscore, 'min_qscore', 0, 100)
    def minQscore = minQscoreValue != null ? "--min-qscore ${minQscoreValue}" : ''
    def trimAdapt = params.trim_adapters != false ? '' : '--no-trim'
    def emitSummary = params.emit_summary != false ? '--emit-summary' : ''
    def batchSizeValue = doradoBoundedInteger(params.dorado_batch_size, 'dorado_batch_size', 1, 1000000)
    def batchSize = batchSizeValue != null ? "--batchsize ${batchSizeValue}" : ''
    def doradoDevice = (params.dorado_device ?: 'cuda:0').toString().trim()
    if (!doradoDevice) {
        doradoDevice = 'cuda:0'
    }
    def device = doradoShellQuote(doradoDevice)
    """
    set -euo pipefail

    DORADO_MODELS_DIR=/weights/dorado
    mkdir -p "\${DORADO_MODELS_DIR}"
    if [[ -z "\$(find "\${DORADO_MODELS_DIR}" -mindepth 1 -print -quit)" ]]; then
        echo "Dorado model directory is empty: \${DORADO_MODELS_DIR}" >&2
        echo "Expected host bind: \${params.weights_root}/dorado -> \${DORADO_MODELS_DIR}" >&2
        echo "Install or bind Dorado models before launching ONT basecalling." >&2
        exit 1
    fi

    dorado basecaller \\
        ${model} \\
        "${pod5_dir}" \\
        --models-directory "\${DORADO_MODELS_DIR}" \\
        ${modBases} \\
        ${minQscore} \\
        ${trimAdapt} \\
        ${emitSummary} \\
        ${batchSize} \\
        --device ${device} \\
        > calls.bam \\
        2> basecall.log

    # Dorado emits sequencing_summary.txt by default; normalize to .tsv for pipeline consumers.
    if [[ -f sequencing_summary.txt && ! -f sequencing_summary.tsv ]]; then
        mv sequencing_summary.txt sequencing_summary.tsv
    fi
    """
}
