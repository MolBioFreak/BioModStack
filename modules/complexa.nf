process RunComplexa {
    label 'Complexa'
    label 'gpu'

    // The output is the directory 'results'; a 'results/**' pattern never matches it,
    // so nothing was published. Publish the directory itself as collected/complexa.
    publishDir "${params.out_dir}/collected", mode: 'copy', saveAs: { it == 'results' ? 'complexa' : null }

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
    def nativeDestination = quote(params.out_dir + '/collected/complexa')
    """
    native_status=0
    python ${quote(params.code_root + '/scripts/run_complexa_inference.py')} \\
        --request ${quote(request_dir.toString() + '/request.json')} \\
        --weights ${quote(params.complexa_weights_dir)} \\
        --output results ${source} ${evaluation} ${ag} || native_status=\$?
    if [ "\$native_status" -ne 0 ] && [ -d results ]; then
        mkdir -p ${nativeDestination} || true
        cp -R results/. ${nativeDestination}/ || true
    fi
    exit "\$native_status"
    """
}
