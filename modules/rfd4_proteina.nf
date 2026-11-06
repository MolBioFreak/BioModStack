nextflow.enable.dsl = 2

process RunRFD4Proteina {
    tag 'rfd4-proteina'
    label 'RFD4Proteina'
    label 'gpu'
    stageInMode 'copy'
    publishDir "${params.out_dir}/collected", mode: 'copy'

    input:
    path closure, stageAs: 'rfd4_closure'

    output:
    path 'rfd4_proteina', emit: native_outputs

    script:
    def image = System.getenv('BMS_SELECTED_IMAGE_RFD4_PROTEINA_SIF') ?: params.rfd4_proteina_container_path ?: "${params.container_dir}/rfd4_proteina.sif"
    def bundle = params.rfd4_proteina_bundle
    // Runtime authority is staged separately from the immutable scientific request.
    def runtimeJson = System.getenv('BMS_RFD4_ORACLE_RUNTIME')
    def oracleRuntime = runtimeJson != null ? new groovy.json.JsonSlurper().parseText(runtimeJson) :
        (new groovy.json.JsonSlurper().parseText(closure.toRealPath().resolve('request.json').text).oracle_runtime ?: [:])
    def quote = { value -> "'" + value.toString().replace("'", "'\"'\"'") + "'" }
    def oracleBinds = oracleRuntime.values().collectMany { family ->
        family.findAll { key, value -> !key.endsWith('_JAX_CACHE_DIR') }.collect { key, value ->
            def resource = new File(value.toString())
            // Prefix databases need their companions; caches live in task PWD.
            key.endsWith('_MODEL_DIR') ? resource.path : resource.parent
        }
    }.unique().collect { value -> '--bind ' + quote(value + ':' + value + ':ro') }.join(' ')
    def cacheRoot = params.cache_root
    // Resolve the existing selected-image alias, never hash the SIF at launch.
    def runtimeImage = new File(image.toString()).canonicalPath
    def cacheAvailable = cacheRoot && new File(cacheRoot.toString()).isDirectory()
    def cacheBind = cacheAvailable ? '--bind ' + quote(cacheRoot.toString() + ':/cache') : ''
    def executable = System.getenv('BMS_CONTAINER_EXECUTABLE') ?: 'apptainer'
    def destination = quote("${params.out_dir}/collected/rfd4_proteina")
    // Host-side refolding-oracle launcher daemons (upstream *_LAUNCHER_SOCKET_TEMPLATE contract): rfproteina's own
    // fallback spawns the daemon inside this container, where nested `apptainer run` fails on rented workers.
    // Only a resolved AF3/OF3 or AF2 runtime adds anything; otherwise the script below is exactly as before.
    def af3Family = ['af3', 'of3'].find { name -> oracleRuntime[name]?.AF3_IMAGE && oracleRuntime[name]?.AF3_MODEL_DIR }
    def hostDaemons = []
    if (af3Family) hostDaemons << "bms_oracle_start AF3 ${quote(oracleRuntime[af3Family].AF3_IMAGE)} ${quote(oracleRuntime[af3Family].AF3_MODEL_DIR)} --lazy-warmup"
    if (oracleRuntime['af2']?.AF2_IMAGE && oracleRuntime['af2']?.AF2_MODEL_DIR) hostDaemons << "bms_oracle_start AF2 ${quote(oracleRuntime['af2'].AF2_IMAGE)} ${quote(oracleRuntime['af2'].AF2_MODEL_DIR)}"
    def daemonSetup = hostDaemons ? """
    . '${params.code_root}/scripts/bms_rfd4/oracle_daemons.sh'
    bms_oracle_begin ${quote(executable)} '${image}' '${params.code_root}' ${quote(params.gpu_id)}
    ${hostDaemons.join('\n    ')}""" : ''
    def daemonArgs = hostDaemons ? ' ${BMS_ORACLE_ARGS[@]+"${BMS_ORACLE_ARGS[@]}"}' : ''
    """
    set -euo pipefail
    mkdir -p .cache/matplotlib${daemonSetup}
    native_status=0
    # TMPDIR stays short on purpose: multiprocessing binds its listener at TMPDIR/pymp-*/listener-*, and a
    # rented worker's task directory pushes that past the 107-character AF_UNIX limit, so the data worker
    # dies silently and the run waits forever (same failure RunCalibyResequence documents).
    ${quote(executable)} exec --no-home --nv \
      --env BMS_RFD4_COMPILE_CACHE_ROOT=${quote(cacheAvailable ? '/cache' : '')} --env BMS_RFD4_RUNTIME_IMAGE=${quote(runtimeImage)} \
      ${cacheBind} \
      --env TMPDIR=/tmp --env MPLCONFIGDIR="\$PWD/.cache/matplotlib" \
      --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${params.gpu_id} \
      --env PYTHONPATH=/bms/scripts \
      --bind "\$PWD:\$PWD" \
      --bind '${params.code_root}/scripts:/bms/scripts:ro' \
      --bind '${params.weights_root}:${params.weights_root}:ro' \
      ${oracleBinds}${daemonArgs} \
      '${image}' python -m bms_rfd4.run \
      --request rfd4_closure/request.json --bindings rfd4_closure/bindings.json \
      --bundle '${bundle}' --output rfd4_proteina || native_status=\$?
    if [ "\$native_status" -ne 0 ]; then
      # publishDir skips failed tasks. Retain the producer's own partial/zero
      # publication, without making a failed scientific process successful.
      mkdir -p ${destination} || true
      [ ! -d rfd4_proteina ] || cp -a rfd4_proteina/. ${destination}/ || true
    fi
    exit "\$native_status"
    """
}
