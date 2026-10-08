/**
 * NGS module extracted from the legacy aggregate Dorado module.
 * Process names are preserved to avoid behavior-changing call-site churn.
 */

process RunCloneValidation {
    // Host-side process; runs a nested Nextflow workflow that uses its own containers.
    label 'wf_clone'
    publishDir "${params.out_dir}/assembly", mode: 'copy'
    tag "clone_validation"

    input:
    tuple path(bam), val(reference_fasta)

    output:
    path "wf_clone_out", emit: out
    path "wf_clone.log", emit: log
    path "wf_clone_out/wf-clone-validation-report.html", emit: report, optional: true
    path "wf_clone_out/sample_status.txt", emit: sample_status, optional: true

    script:
    def sampleName = (params.wf_clone_sample ?: params.name ?: (params.job_id ? "nanopore_${params.job_id}" : "nanopore"))
        .toString()
        .replaceAll(/[^A-Za-z0-9._-]/, "_")
    def approxSize = (params.wf_clone_approx_size ?: 7000) as Integer
    def assmCoverage = (params.wf_clone_assm_coverage ?: 60) as Integer
    def minQuality = (params.wf_clone_min_quality != null ? params.wf_clone_min_quality : (params.min_qscore ?: 9)) as Integer
    def trimLength = (params.wf_clone_trim_length ?: 0) as Integer
    def assemblyTool = (params.wf_clone_assembly_tool ?: 'flye').toString()
    def largeConstruct = params.wf_clone_large_construct ? "--large_construct" : ""
    def wfCloneDir = (params.wf_clone_workflow_dir ?: "${System.getenv('HOME') ?: '/tmp'}/.nextflow/assets/epi2me-labs/wf-clone-validation").toString()
    def wfCloneSource = (params.wf_clone_source ?: "epi2me-labs/wf-clone-validation").toString()
    def wfCloneRevision = (params.wf_clone_revision ?: "").toString()
    def wfCloneRevisionLabel = wfCloneRevision ? "@${wfCloneRevision}" : ""
    def wfCloneProfileRaw = (params.wf_clone_profile ?: "singularity").toString()
    def wfCloneProfiles = wfCloneProfileRaw
        .split(',')
        .collect { it.trim() }
        .findAll { it }
    // Standard+singularity can enable multiple container engines in nested runs.
    // Force singularity-only for stable local/apptainer execution.
    def wfCloneProfile = wfCloneProfiles.contains('singularity') ? 'singularity' : (wfCloneProfiles ? wfCloneProfiles.join(',') : 'singularity')
    def referencePath = reference_fasta ? reference_fasta.toString().trim() : ""
    def dataRoot = (params.data_root ?: "/mnt/BioModStack").toString()
    def defaultContainerRoot = (params.container_dir ?: "${dataRoot}/apptainer").toString()
    def wfCloneSingularityCache = (params.wf_clone_singularity_cache ?: "${defaultContainerRoot}/singularity_cache").toString()
    def wfCloneNxfHome = (params.wf_clone_nxf_home ?: "${dataRoot}/nextflow").toString()
    def wfCloneOverrideBasecallerCfg = (params.wf_clone_override_basecaller_cfg ?: "").toString().trim()
    if (!wfCloneOverrideBasecallerCfg) {
        def doradoModel = (params.dorado_model ?: "sup").toString().toLowerCase()
        if (doradoModel == "hac") {
            wfCloneOverrideBasecallerCfg = "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
        } else if (doradoModel == "fast") {
            // wf-clone-validation schema does not accept a FAST profile here;
            // use HAC v5.0.0 as the closest supported fallback.
            wfCloneOverrideBasecallerCfg = "dna_r10.4.1_e8.2_400bps_hac@v5.0.0"
        } else {
            wfCloneOverrideBasecallerCfg = "dna_r10.4.1_e8.2_400bps_sup@v5.0.0"
        }
    }
    """
    set -euo pipefail
    export NXF_DISABLE_CHECK_LATEST=true
    export NXF_DOCKER_ENABLED=false
    export NXF_SINGULARITY_ENABLED=true
    export NXF_SINGULARITY_CACHEDIR="${wfCloneSingularityCache}"
    export NXF_HOME="${wfCloneNxfHome}"
    mkdir -p "\${NXF_SINGULARITY_CACHEDIR}" "\${NXF_HOME}"
    echo "wf-clone using NXF_SINGULARITY_CACHEDIR=\${NXF_SINGULARITY_CACHEDIR}" >&2

    resolved_wf_clone_dir="${wfCloneDir}"

    # Attempt to self-bootstrap the EPI2ME workflow if local assets are missing.
    if [[ ! -d "\${resolved_wf_clone_dir}" ]]; then
        echo "wf-clone-validation workflow directory not found: ${wfCloneDir}" >&2
        echo "Attempting to pull ${wfCloneSource}${wfCloneRevisionLabel}..." >&2
        pull_cmd=(nextflow pull "${wfCloneSource}")
        if [[ -n "${wfCloneRevision}" ]]; then
            pull_cmd+=(-r "${wfCloneRevision}")
        fi
        "\${pull_cmd[@]}" || true
        pulled_dir="\${HOME:-/tmp}/.nextflow/assets/${wfCloneSource}"
        if [[ -d "\${pulled_dir}" ]]; then
            resolved_wf_clone_dir="\${pulled_dir}"
        fi
    fi

    if [[ ! -d "\${resolved_wf_clone_dir}" ]]; then
        echo "wf-clone-validation workflow directory not found: ${wfCloneDir}" >&2
        echo "Set --wf_clone_workflow_dir to a valid local checkout of epi2me-labs/wf-clone-validation." >&2
        exit 1
    fi

    mkdir -p wf_clone_assets wf_clone_out
    cp -a "\${resolved_wf_clone_dir}/." wf_clone_assets/

    # Compatibility patch for wf-clone-validation on Nextflow >=25:
    # report process re-declares a variable named 'metadata' in the same scope.
    if grep -q "String metadata = metadata_obj.toPrettyString()" wf_clone_assets/main.nf; then
        python3 - <<'PY'
from pathlib import Path

p = Path("wf_clone_assets/main.nf")
text = p.read_text()
text = text.replace(
    "String metadata = metadata_obj.toPrettyString()",
    "String metadata_json = metadata_obj.toPrettyString()",
)
text = text.replace(
    "echo '\${metadata}' > metadata.json",
    "echo '\${metadata_json}' > metadata.json",
)
p.write_text(text)
PY
    fi

    # Patch Flye overlap heuristic for short plasmids:
    # upstream rule only lowers min-overlap for <=3kb constructs, which can
    # fail for ~5-10kb constructs with read lengths near construct size.
    flye_module="wf_clone_assets/modules/local/flye_assembly.nf"
    if [[ -f "\${flye_module}" ]] && grep -q "def min_overlap = meta.approx_size.toInteger() <= 3000 ? '--min-overlap 1000' : ''" "\${flye_module}"; then
        python3 - <<'PY'
from pathlib import Path

p = Path("wf_clone_assets/modules/local/flye_assembly.nf")
text = p.read_text()
old = "def min_overlap = meta.approx_size.toInteger() <= 3000 ? '--min-overlap 1000' : ''"
new = (
    "int approx_size_bp = meta.approx_size.toInteger()\\n"
    "        int min_overlap_bp = approx_size_bp <= 10000 ? Math.max(1000, (int)(approx_size_bp * 0.25)) : 0\\n"
    "        def min_overlap = min_overlap_bp > 0 ? '--min-overlap ' + min_overlap_bp : ''"
)
if old in text:
    text = text.replace(old, new)
    p.write_text(text)
PY
    fi

    full_ref_arg=()
    if [[ -n "${referencePath}" ]]; then
        cp "${referencePath}" wf_clone_reference.fasta
        full_ref_arg=(--full_reference "wf_clone_reference.fasta")
    fi
    override_model_arg=()
    if [[ -n "${wfCloneOverrideBasecallerCfg}" ]]; then
        override_model_arg=(--override_basecaller_cfg "${wfCloneOverrideBasecallerCfg}")
    fi

    run_wf_clone() {
        local tool="\$1"
        local log_file="wf_clone_\${tool}.log"
        nextflow -log "\${log_file}" run wf_clone_assets \\
            -profile "${wfCloneProfile}" \\
            -w "wf_clone_work_\${tool}" \\
            --bam "${bam}" \\
            --sample "${sampleName}" \\
            --out_dir wf_clone_out \\
            --approx_size ${approxSize} \\
            --assm_coverage ${assmCoverage} \\
            --min_quality ${minQuality} \\
            --trim_length ${trimLength} \\
            --assembly_tool "\${tool}" \\
            ${largeConstruct} \\
            "\${override_model_arg[@]}" \\
            "\${full_ref_arg[@]}"
    }

    wf_clone_rc=0
    chosen_tool="${assemblyTool}"

    set +e
    run_wf_clone "\${chosen_tool}"
    wf_clone_rc=\$?
    set -e

    # Flye can fail on near-genome-length reads where overlap heuristics over-shoot.
    # Retry once with CANU to improve robustness for plasmid workflows.
    if [[ \${wf_clone_rc} -ne 0 && "\${chosen_tool}" == "flye" ]]; then
        echo "Flye assembly failed (exit=\${wf_clone_rc}); retrying wf-clone-validation with CANU." >&2
        rm -rf wf_clone_out
        mkdir -p wf_clone_out
        chosen_tool="canu"
        set +e
        run_wf_clone "\${chosen_tool}"
        wf_clone_rc=\$?
        set -e
    fi

    # Consolidate nested wf logs into stable output path for UI/API consumers.
    if [[ -f "wf_clone_\${chosen_tool}.log" ]]; then
        cp "wf_clone_\${chosen_tool}.log" wf_clone.log
    elif [[ -f "wf_clone_flye.log" ]]; then
        cp "wf_clone_flye.log" wf_clone.log
    else
        : > wf_clone.log
    fi

    if [[ \${wf_clone_rc} -ne 0 ]]; then
        echo "wf-clone-validation failed after retries (final_tool=\${chosen_tool}, exit=\${wf_clone_rc})." >&2
        exit \${wf_clone_rc}
    fi
    """
}
