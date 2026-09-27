process RunCalibyNative {
    label 'Caliby'
    label 'gpu'

    publishDir "${params.out_dir}", mode: 'copy', pattern: 'caliby_native'

    input:
    path request_dir

    output:
    path 'caliby_native', emit: native_results

    script:
    def quote = { value -> "'" + value.toString().replace("'", "'\"'\"'") + "'" }
    """
    set -euo pipefail
    python3 ${quote(params.code_root + '/scripts/run_caliby_experimental.py')} \\
        --request-dir ${quote(request_dir)} \\
        --output-dir caliby_native
    """
}
