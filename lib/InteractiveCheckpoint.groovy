import groovy.json.JsonSlurper

/** Scheduler-owned continuation verification, before constructing scientific DAG. */
class InteractiveCheckpoint {
    // Legacy directory-based review/resume enumerates candidates by filename.
    // Stabilize only that boundary, never the scientific producer's input order.
    static List reviewCandidates(paths) {
        def files = (paths instanceof Collection ? paths.flatten() : [paths])
        return files.sort(false) { left, right ->
            new File(left.toString()).name <=> new File(right.toString()).name
        }
    }

    static Map continuation(params) {
        def requested = params.get('checkpoint_continuation')
        def legacy = params.get('interactive_gate_continue') == true ||
            params.get('plr_backbone_input_pdbs') || params.get('plr_sequence_input_pdbs') ||
            params.get('plr_validation_input_pdbs')
        if (!requested) {
            if (legacy) throw new IllegalArgumentException('Unbound gate continuation/resume inputs prohibited')
            return [:]
        }
        if (!params.get('checkpoint_context') || !params.get('checkpoint_execution_id')) {
            throw new IllegalArgumentException('Continuation requires checkpoint context and execution identity')
        }
        def command = ['python3', "${params.code_root}/scripts/interactive_checkpoint.py".toString(),
            'verify-continuation', '--root', requested.toString(),
            '--context', params.checkpoint_context.toString(),
            '--execution-id', params.checkpoint_execution_id.toString()]
        def process = new ProcessBuilder(command).start()
        def stdout = new StringBuffer()
        def stderr = new StringBuffer()
        process.consumeProcessOutput(stdout, stderr)
        if (!process.waitFor(30, java.util.concurrent.TimeUnit.SECONDS)) {
            process.destroyForcibly()
            throw new IllegalStateException('Checkpoint verification timed out; no science submitted')
        }
        process.waitForProcessOutput()
        if (process.exitValue() != 0) {
            throw new IllegalArgumentException('Checkpoint verification failed: ' + stderr.toString())
        }
        return new JsonSlurper().parseText(stdout.toString()) as Map
    }
}
