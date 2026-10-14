def reportStage(params, stageName, files) {
    def jobId = params.containsKey('job_id') ? params.job_id : null
    if (!jobId) return
    try {
        def reportFiles = files.findAll { it != null && it.toString().trim() }
        if (reportFiles.isEmpty()) return
        def args = [jobId.toString(), stageName, "complete"] + reportFiles.collect { it.toString() }
        def proc = (["python3", "${params.code_root}/scripts/stage_reporter.py"] + args).execute()
        def rc = proc.waitFor()
        if (rc != 0) throw new IllegalStateException("Stage reporting failed for ${stageName} (exit ${rc})")
    } catch (Exception e) {
        throw new IllegalStateException("Stage reporting failed for ${stageName}", e)
    }
}
