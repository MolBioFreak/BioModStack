process RunComplexa {
    label 'Complexa'
    label 'gpu'

    publishDir "${params.out_dir}/collected/complexa", mode: 'copy', pattern: 'results/**', saveAs: { it.replaceFirst('results/', '') }

    input:
    path request_dir

    output:
    path 'results', emit: native_results

    script:
    // Files/weights/image are system-owned bindings, never scientific JSON paths.
    def quote = { value -> "'" + value.toString().replace("'", "'\"'\"'") + "'" }
    def source = file("${request_dir}/source.pdb").exists() ? "--source ${quote(request_dir.toString() + '/source.pdb')}" : ''
    def evaluation = file("${request_dir}/evaluation").exists() ? "--evaluation-directory ${quote(request_dir.toString() + '/evaluation')}" : ''
    def ag = params.complexa_autoguidance_checkpoint ? "--autoguidance-checkpoint ${quote(params.complexa_autoguidance_checkpoint)}" : ''
    """
    python ${quote(params.code_root + '/scripts/run_complexa_inference.py')} \\
        --request ${quote(request_dir.toString() + '/request.json')} \\
        --weights ${quote(params.complexa_weights_dir)} \\
        --output results ${source} ${evaluation} ${ag}
    """
}
