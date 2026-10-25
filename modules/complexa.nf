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
    def retained = request_dir.toRealPath()
    def sourcePointer = retained.resolve('source-path.txt')
    def sourceName = java.nio.file.Files.exists(sourcePointer) ? sourcePointer.text.trim() : 'source.pdb'
    def source = java.nio.file.Files.exists(retained.resolve(sourceName)) ? "--source ${quote(request_dir.toString() + '/' + sourceName)}" : ''
    def evaluation = java.nio.file.Files.exists(retained.resolve('evaluation')) ? "--evaluation-directory ${quote(request_dir.toString() + '/evaluation')}" : ''
    def ag = params.complexa_autoguidance_checkpoint ? "--autoguidance-checkpoint ${quote(params.complexa_autoguidance_checkpoint)}" : ''
    """
    python ${quote(params.code_root + '/scripts/run_complexa_inference.py')} \\
        --request ${quote(request_dir.toString() + '/request.json')} \\
        --weights ${quote(params.complexa_weights_dir)} \\
        --output results ${source} ${evaluation} ${ag}
    """
}
