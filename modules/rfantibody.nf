process RFANTIBODY {
    /*
     * RFantibody - De novo antibody backbone design
     * 
     * Uses antibody-finetuned RFdiffusion to generate antibody backbones
     * that bind to a target protein at specified epitope hotspots.
     * 
     * Follows official RFantibody documentation:
     * https://github.com/RosettaCommons/RFantibody
     * 
     * Supports multi-GPU parallelism via per-process gpu_id input
     */
    tag "${meta.id}_gpu${gpu_id}"
    label 'process_gpu'
    container "${params.container_dir}/rfantibody.sif"
    errorStrategy 'retry'
    maxRetries 2

    // Bind the full repo - uses host repo source code + weights.
    // All params referenced directly inside closure for correct DSL2 scoping.
    containerOptions {
        def rfantibodyRepo = "${params.weights_root}/rfantibody/rfantibody_repo"
        def repoBind = params.rfantibody_debug_repo_overlay ? "--bind ${rfantibodyRepo}:/opt/RFantibody" : ""
        def codeBind = params.code_root ? "--bind ${params.code_root}" : ""
        // Always bind the model-weight directory so checkpoint lookup is stable
        // even when repo overlay is disabled.
        def rfdModelsBind = params.rfd_models ? "--bind ${params.rfd_models}:/opt/rfantibody_weights" : ""
        return "--nv --env CUDA_DEVICE_ORDER=PCI_BUS_ID --env CUDA_VISIBLE_DEVICES=${gpu_id} ${codeBind} ${repoBind} ${rfdModelsBind} --writable-tmpfs"
    }

    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "*.log"
    publishDir "${params.out_dir}/run/rfantibody", mode: 'copy', pattern: "output/*.pdb"

    input:
    tuple val(meta), path(target_pdb), val(hotspot_residues), val(gpu_id), val(num_designs_for_this_gpu)
    path framework_pdb

    output:
    tuple val(meta), path("output/*.pdb"), emit: designs
    path "rfantibody_${meta.id}.log", emit: log

    script:
    // Format hotspots for RFantibody ppi.hotspot_res parameter
    // IMPORTANT: Use original chain IDs from the input PDB file!
    // Input format from UI: "A45,A46,A52" -> Keep as-is for ppi.hotspot_res
    // RFantibody will find these residues in the target PDB and guide design there
    // (The HLT naming is for OUTPUT chains, not the input hotspot parameter)
    def hotspots = hotspot_residues ? "[${hotspot_residues}]" : "[]"

    // Framework selection based on framework_type param
    // Options: 'standard-fv', 'nanobody', 'custom'
    def frameworkType = params.framework_type ?: 'standard-fv'
    def defaultDesignLoops = frameworkType == 'nanobody'
        ? "[H1:7-10,H2:6-8,H3:5-15]"
        : "[H1:7-10,H2:6-8,H3:5-15,L1:8-13,L2:7,L3:9-11]"
    def antibodyChainTokens = (params.antibody_chains ?: 'H,L')
        .split(',')
        .collect { it.trim().toUpperCase() }
        .findAll { it }
    def heavyChainId = antibodyChainTokens ? antibodyChainTokens[0] : 'H'

    // Design loops configuration
    // Accepts two formats:
    //   1. Simple loop names from UI: "H1,H2,H3" or "H1,H3,L1,L3"
    //   2. Full RFantibody format: "[H1:7-10,H2:6-8,H3:5-15]"
    // If simple format, auto-apply default length ranges per loop

    // Default length ranges for each CDR loop (based on typical antibody CDR statistics)
    def loopLengthDefaults = [
        'H1': '7-10',
        'H2': '6-8',
        'H3': '5-15',
        'L1': '8-13',
        'L2': '7',
        'L3': '9-11',
    ]

    // Start with RFantibody-specific loop spec if provided, then UI loop selection,
    // then framework defaults.
    def rawLoops = params.get('rfantibody_loop_length_ranges') ?: params.get('rfantibody_design_loops_custom') ?: params.rfantibody_design_loops ?: params.antibody_design_loops ?: ''

    def design_loops
    if (rawLoops && rawLoops.startsWith('[') && rawLoops.endsWith(']')) {
        if (rawLoops.contains(':')) {
            // Native RFantibody loop-length format, e.g. [H1:7-10,H2:6-8,H3:5-15]
            design_loops = rawLoops
        } else {
            // Absolute loop-position format, e.g. [B27-38,B56-65,B105-117].
            // Normalize unknown chain labels to heavyChainId and convert absolute
            // position ranges to native RFantibody loop-length specs.
            // Example: [B27-38,B56-65,B105-117] -> [H1:12,H2:10,H3:13]
            // This keeps legacy UI params compatible with RFantibody.
            def body = rawLoops.substring(1, rawLoops.length() - 1)
            def tokens = body.split(',').collect { it.trim() }.findAll { it }
            def normalized = tokens.collect { token ->
                def matcher = token =~ /^([A-Za-z])(\d+)-(\d+)$/
                if (matcher.matches()) {
                    def chainId = matcher[0][1].toUpperCase()
                    def start = Integer.parseInt(matcher[0][2])
                    def end = Integer.parseInt(matcher[0][3])
                    if (end < start) {
                        def tmp = start
                        start = end
                        end = tmp
                    }
                    if (!(chainId in ['H', 'L'])) {
                        chainId = heavyChainId
                    }
                    def loopPrefix = chainId == 'L' ? 'L' : 'H'
                    def loopIndex = start >= 100 ? 3 : (start >= 50 ? 2 : 1)
                    def loopLength = (end - start) + 1
                    if (loopLength < 1) {
                        return null
                    }
                    return "${loopPrefix}${loopIndex}:${loopLength}"
                }
                return null
            }.findAll { it }
            design_loops = normalized ? "[${normalized.join(',')}]" : defaultDesignLoops
        }
    }
    else if (rawLoops && rawLoops.trim()) {
        // Simple loop names from UI: "H1,H2,H3" -> convert to RFantibody format
        def loopList = rawLoops.split(',').collect { it.trim().toUpperCase() }
        def loopSpecs = loopList
            .findAll { loopLengthDefaults.containsKey(it) }
            .collect { "${it}:${loopLengthDefaults[it]}" }
        design_loops = loopSpecs ? "[${loopSpecs.join(',')}]" : defaultDesignLoops
    }
    else {
        // No selection - use framework-based defaults (all loops)
        design_loops = defaultDesignLoops
    }

    // Number of designs - use per-GPU allocation from input (supports multi-GPU splitting)
    def num_designs = num_designs_for_this_gpu ?: params.rfantibody_num_designs ?: 10

    // Quality parameters — clamped to literature-backed maximums
    // Ref: RFdiffusion (Watson et al. 2023, Nature), RFantibody base.yaml
    // T=50 is the trained inference default; T>50 stretches the beta schedule
    // beyond what the model expects, increasing degenerate-frame probability.
    def requested_diffusion_steps = (params.rfantibody_diffusion_steps ?: 50) as int
    def diffusion_steps = Math.min(requested_diffusion_steps, 50)
    // noise_scale: 0 = best binder success rate, 1 = max diversity (paper default)
    def noise_scale_ca = Math.min((params.rfantibody_noise_scale_ca ?: 1.0) as double, 1.0)
    def noise_scale_frame = Math.min((params.rfantibody_noise_scale_frame ?: 1.0) as double, 1.0)
    // guide_scale: 2-10 typical, 20 upper bound for strong hotspot guidance
    def guide_scale = Math.min((params.rfantibody_guide_scale ?: 10) as int, 20)

    def presetFrameworks = [
        'standard-fv': '/opt/RFantibody/scripts/examples/example_inputs/hu-4D5-8_Fv.pdb',
        'nanobody': '/opt/RFantibody/scripts/examples/example_inputs/h-NbBCII10.pdb',
    ]
    def framework = framework_pdb.name != 'NO_FRAMEWORK'
        ? framework_pdb
        : presetFrameworks[frameworkType] ?: presetFrameworks['standard-fv']
    def frameworkArgPath = framework_pdb.name != 'NO_FRAMEWORK'
        ? "\${WORK_DIR}/${framework_pdb.name}"
        : framework

    // Resolve RFantibody checkpoint candidates in order of preference.
    // 1) Explicit override if provided
    // 2) Host-path model dir passed as params.rfd_models
    // 3) Explicit in-container bind mount path (/opt/rfantibody_weights)
    // 4) Legacy in-container repo path (/opt/RFantibody/weights)
    def ckptCandidates = []
    if (params.rfantibody_ckpt_override) {
        ckptCandidates << params.rfantibody_ckpt_override.toString()
    }
    if (params.rfd_models) {
        ckptCandidates << "${params.rfd_models}/RFdiffusion_Ab.pt"
    }
    ckptCandidates << "/opt/rfantibody_weights/RFdiffusion_Ab.pt"
    ckptCandidates << "/opt/RFantibody/weights/RFdiffusion_Ab.pt"
    def ckptCandidatesBash = ckptCandidates.collect { "\"${it}\"" }.join(' ')


    """
    set -euo pipefail

    # Save work directory path for absolute file references
    WORK_DIR=\$(pwd)
    LOG_FILE="\${WORK_DIR}/rfantibody_${meta.id}.log"

    echo "=== RFantibody De Novo Antibody Design ===" | tee "\${LOG_FILE}"
    echo "Target PDB: ${target_pdb}" | tee -a "\${LOG_FILE}"
    echo "Framework PDB: ${framework}" | tee -a "\${LOG_FILE}"
    echo "Hotspot residues: ${hotspots}" | tee -a "\${LOG_FILE}"
    echo "Design loops: ${design_loops}" | tee -a "\${LOG_FILE}"
    echo "Num designs: ${num_designs}" | tee -a "\${LOG_FILE}"
    echo "Quality params: T=${diffusion_steps}, noise_ca=${noise_scale_ca}, noise_frame=${noise_scale_frame}, guide=${guide_scale}" | tee -a "\${LOG_FILE}"
    
    mkdir -p output

    if [ "${framework_pdb.name}" != "NO_FRAMEWORK" ]; then
        python3 - <<'PY' "${frameworkArgPath}" 2>&1 | tee -a "\${LOG_FILE}"
import sys
from pathlib import Path

framework_path = Path(sys.argv[1])
chains = set()
for line in framework_path.read_text().splitlines():
    if line.startswith(("ATOM", "HETATM")):
        chain = line[21:22].strip()
        if chain:
            chains.add(chain)

if not ({'H', 'L'} & chains):
    raise SystemExit(
        f"[RFA-ERROR] Framework file {framework_path} does not contain antibody chains labeled H or L. "
        f"Found chains: {sorted(chains)}. RFantibody expects an HLT-style framework."
    )
PY
        if [ \${PIPESTATUS[0]} -ne 0 ]; then
            echo "Framework preflight failed. Aborting." >> "\${LOG_FILE}"
            exit 1
        fi
    fi

    # Run preflight guard to ensure runtime is healthy
    python3 ${params.code_root}/scripts/check_rfantibody_runtime.py \\
        2>&1 | tee -a "\${LOG_FILE}"

    # End if preflight fails
    if [ \${PIPESTATUS[0]} -ne 0 ]; then
        echo "Preflight check failed. Aborting." >> "\${LOG_FILE}"
        exit 1
    fi

    # Run RFantibody RFdiffusion inference
    # Script is at /opt/RFantibody/scripts/rfdiffusion_inference.py
    # PYTHONPATH is set in container environment to include src/ and include/
    # Support both container layouts by resolving config path dynamically.
    cd /opt/RFantibody

    RFA_CONFIG_PATH=""
    for candidate in \\
        /opt/RFantibody/scripts/config/inference \\
        /opt/RFantibody/src/rfantibody/rfdiffusion/config/inference
    do
        if [ -d "\$candidate" ]; then
            RFA_CONFIG_PATH="\$candidate"
            break
        fi
    done

    if [ -z "\$RFA_CONFIG_PATH" ]; then
        echo "RFantibody config directory not found in known locations." | tee -a "\${LOG_FILE}"
        exit 1
    fi

    echo "RFantibody config path: \$RFA_CONFIG_PATH" | tee -a "\${LOG_FILE}"

    CKPT_PATH=""
    CKPT_CANDIDATES=(${ckptCandidatesBash})
    for candidate in "\${CKPT_CANDIDATES[@]}"; do
        [ -n "\$candidate" ] || continue
        if [ -f "\$candidate" ]; then
            CKPT_PATH="\$candidate"
            break
        fi
    done

    if [ -z "\$CKPT_PATH" ]; then
        echo "[RFA-ERROR] Could not locate RFantibody checkpoint: RFdiffusion_Ab.pt" | tee -a "\${LOG_FILE}"
        echo "[RFA-ERROR] Checked checkpoint candidates:" | tee -a "\${LOG_FILE}"
        for candidate in "\${CKPT_CANDIDATES[@]}"; do
            echo "  - \$candidate" | tee -a "\${LOG_FILE}"
        done
        echo "[RFA-ERROR] /opt/rfantibody_weights contents:" | tee -a "\${LOG_FILE}"
        ls -la /opt/rfantibody_weights 2>&1 | tee -a "\${LOG_FILE}" || true
        echo "[RFA-ERROR] /opt/RFantibody/weights contents:" | tee -a "\${LOG_FILE}"
        ls -la /opt/RFantibody/weights 2>&1 | tee -a "\${LOG_FILE}" || true
        exit 1
    fi

    echo "RFantibody checkpoint path: \$CKPT_PATH" | tee -a "\${LOG_FILE}"

    # Reduce extremely verbose icecream debug output to keep logs bounded.
    export IC_DISABLE=1

    python3 scripts/rfdiffusion_inference.py \\
        --config-path \$RFA_CONFIG_PATH \\
        --config-name antibody \\
        antibody.target_pdb=\${WORK_DIR}/${target_pdb} \\
        antibody.framework_pdb=${frameworkArgPath} \\
        inference.ckpt_override_path=\${CKPT_PATH} \\
        'ppi.hotspot_res=${hotspots}' \\
        'antibody.design_loops=${design_loops}' \\
        inference.num_designs=${num_designs} \\
        diffuser.T=${diffusion_steps} \\
        denoiser.noise_scale_ca=${noise_scale_ca} \\
        denoiser.noise_scale_frame=${noise_scale_frame} \\
        potentials.guide_scale=${guide_scale} \\
        inference.output_prefix=\${WORK_DIR}/output/${meta.id} \\
        2>&1 | sed -u '/^ic|/d' | tee -a "\${LOG_FILE}"
    
    # Return to work directory where output was written
    cd \${WORK_DIR}
    
    echo "RFantibody complete" | tee -a "\${LOG_FILE}"
    ls -la output/ | tee -a "\${LOG_FILE}"
    """
}
