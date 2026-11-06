def reportStage(params, stageName, files) {
    def jobId = params.containsKey('job_id') ? params.job_id : null
    if (!jobId) return
    try {
        def reportFiles = files.findAll { it != null && it.toString().trim() }
        if (reportFiles.isEmpty()) return
        def args = [jobId.toString(), stageName, "complete"] + reportFiles.collect { it.toString() }
        // The managed API interpreter owns reporter dependencies; PATH may be
        // the clean host Python on a fresh native installation.
        def python = params.containsKey('pod5_python') && params.pod5_python ? params.pod5_python.toString() : (System.getenv('BMS_API_PYTHON') ?: 'python3')
        def proc = ([python, "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
        def rc = proc.waitFor()
        if (rc != 0) throw new IllegalStateException("Stage reporting failed for ${stageName} (exit ${rc})")
    } catch (Exception e) {
        throw new IllegalStateException("Stage reporting failed for ${stageName}", e)
    }
}
