/** Pinned Dorado 1.3.1 P4 data plane: preflight -> exact model -> optional demux. */
def doradoShellQuote(value) {
    return "'" + value.toString().replace("'", "'\"'\"'") + "'"
}

process DoradoPreflight {
    cache false
    label 'local_cpu'
    publishDir "${params.out_dir}/basecall", mode: 'copy'
    tag 'dorado-preflight'

    input:
    path pod5_dir

    output:
    path "dorado_preflight.json", emit: manifest

    script:
    def python = doradoShellQuote(params.pod5_python ?: 'python3')
    def scriptPath = doradoShellQuote("${params.code_root}/scripts/dorado_p4_preflight.py")
    def lockPath = doradoShellQuote(params.dorado_lock_manifest ?: "${params.code_root}/config/ngs/dorado_v1.3.1.lock.json")
    def expectedLockSha256 = (params.dorado_lock_sha256 ?: '').toString().trim().toLowerCase()
    if (expectedLockSha256 && !(expectedLockSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('dorado_lock_sha256 must be exactly 64 hexadecimal characters')
    }
    def modelRoot = doradoShellQuote(params.dorado_model_root ?: "${params.weights_root}/dorado/1.3.1")
    def runtimeSif = doradoShellQuote(params.dorado_runtime_sif ?: "${params.container_dir}/dorado.sif")
    def molecule = doradoShellQuote(params.ont_molecule_type ?: 'dna')
    def quality = doradoShellQuote(params.dorado_quality_mode ?: 'sup')
    def mode = doradoShellQuote(params.dorado_basecall_mode ?: 'simplex')
    def modified = doradoShellQuote(params.modified_bases ?: 'none')
    def device = doradoShellQuote(params.dorado_device ?: 'cuda:0')
    def minQscore = doradoShellQuote(params.min_qscore != null ? params.min_qscore : 10)
    def optional = []
    if (params.dorado_batch_size != null && params.dorado_batch_size.toString().trim()) optional += "--batch-size ${doradoShellQuote(params.dorado_batch_size)}"
    if (params.duplex_pairs) optional += "--pairs ${doradoShellQuote(params.duplex_pairs)}"
    if (params.barcode_kit) optional += "--barcode-kit ${doradoShellQuote(params.barcode_kit)}"
    if (params.sample_sheet) optional += "--sample-sheet ${doradoShellQuote(params.sample_sheet)}"
    """
    set -euo pipefail
    lock_source=${lockPath}
    [[ -f "\${lock_source}" && ! -L "\${lock_source}" ]] || { echo 'Dorado lock must be a regular non-symlink file' >&2; exit 1; }
    cp --reflink=auto "\${lock_source}" dorado_lock.json
    lock_snapshot_sha256="\$(sha256sum dorado_lock.json | cut -d' ' -f1)"
    if [[ -n '${expectedLockSha256}' && "\${lock_snapshot_sha256}" != '${expectedLockSha256}' ]]; then
      echo 'accepted Dorado lock identity changed before preflight' >&2; exit 1
    fi
    ${python} ${scriptPath} \
      --lock "\$PWD/dorado_lock.json" \
      --pod5-root "\$(realpath ${doradoShellQuote(pod5_dir)})" \
      --molecule ${molecule} \
      --quality ${quality} \
      --mode ${mode} \
      --model-root ${modelRoot} \
      --runtime-sif ${runtimeSif} \
      --modified-bases ${modified} \
      --device ${device} \
      --min-qscore ${minQscore} \
      ${optional.join(' ')} \
      --output dorado_preflight.json
    [[ "\$(sha256sum dorado_lock.json | cut -d' ' -f1)" == "\${lock_snapshot_sha256}" ]] || { echo 'Dorado lock snapshot changed during preflight' >&2; exit 1; }
    test -s dorado_preflight.json
    """
}

process DoradoBasecall {
    label 'dorado_gpu'
    label 'gpu'
    publishDir "${params.out_dir}/basecall", mode: 'copy'
    tag 'dorado-basecall'

    input:
    path pod5_dir
    path preflight_json

    output:
    path "calls.bam", emit: bam
    path "basecall.log", emit: log
    path "dorado_preflight.json", emit: preflight
    path "dorado_runtime_provenance.json", emit: provenance
    path "sequencing_summary.tsv", emit: summary, optional: true

    script:
    def summaryRequested = params.emit_summary != false
    def movesRequested = params.emit_moves == true
    def trimAdapters = params.trim_adapters != false
    def expectedLockSha256 = (params.dorado_lock_sha256 ?: '').toString().trim().toLowerCase()
    if (expectedLockSha256 && !(expectedLockSha256 ==~ /[0-9a-f]{64}/)) {
        throw new IllegalArgumentException('dorado_lock_sha256 must be exactly 64 hexadecimal characters')
    }
    """
    set -euo pipefail
    test -s ${doradoShellQuote(preflight_json)}
    [[ "\$(jq -r '.schema' dorado_preflight.json)" == 'biomodstack.dorado_preflight.v1' ]]
    [[ "\$(jq -r '.runtime.assets.verified' dorado_preflight.json)" == 'true' ]]
    if [[ -n '${expectedLockSha256}' && "\$(jq -r '.lock.sha256' dorado_preflight.json)" != '${expectedLockSha256}' ]]; then
      echo 'preflight lock identity does not match the accepted job contract' >&2; exit 1
    fi

    model_id="\$(jq -r '.selection.model_id' dorado_preflight.json)"
    molecule="\$(jq -r '.selection.molecule' dorado_preflight.json)"
    mode="\$(jq -r '.selection.mode' dorado_preflight.json)"
    batch_size="\$(jq -r '.execution_policy.batch_size' dorado_preflight.json)"
    device="\$(jq -r '.execution_policy.device' dorado_preflight.json)"
    min_qscore="\$(jq -r '.execution_policy.min_qscore' dorado_preflight.json)"
    min_gpu_total="\$(jq -r '.execution_policy.min_gpu_total_mib' dorado_preflight.json)"
    min_gpu_free="\$(jq -r '.execution_policy.min_gpu_free_mib' dorado_preflight.json)"
    pair_relative="\$(jq -r '.pairs.relative_path // empty' dorado_preflight.json)"
    barcode_kit="\$(jq -r '.barcoding.kit // empty' dorado_preflight.json)"
    sample_relative="\$(jq -r '.barcoding.sample_sheet.relative_path // empty' dorado_preflight.json)"
    mod_model_id="\$(jq -r '.selection.modified_bases_model_id // empty' dorado_preflight.json)"
    [[ "\${model_id}" =~ ^[A-Za-z0-9_.@-]+\$ ]]
    [[ "\${batch_size}" =~ ^[0-9]+\$ ]]
    [[ "\${min_qscore}" =~ ^[0-9]+\$ ]]
    [[ "\${device}" =~ ^cuda:(all|[0-9]+(,[0-9]+)*)\$ ]]
    [[ "\${mode}" == simplex || "\${mode}" == duplex ]]
    [[ "\${molecule}" == dna || "\${molecule}" == rna ]]
    trim_adapters=${trimAdapters}
    if [[ "\${molecule}" == rna && "\${trim_adapters}" == false ]]; then
      echo 'Dorado RNA always trims adapters; trim_adapters=false is unsupported' >&2; exit 1
    fi
    if [[ "\${mode}" == duplex && "\${trim_adapters}" == false ]]; then
      echo 'locked Dorado duplex lacks an adapter-trim control; trim_adapters=false is unsupported' >&2; exit 1
    fi
    [[ "\${min_gpu_total}" =~ ^[0-9]+\$ && "\${min_gpu_free}" =~ ^[0-9]+\$ ]]

    verify_model_tree() {
      local model_dir="\$1" expected_sha="\$2" expected_files="\$3" expected_bytes="\$4"
      local inventory observed_sha observed_files=0 observed_bytes=0 file rel size file_sha
      [[ -d "\${model_dir}" && ! -L "\${model_dir}" ]]
      if find "\${model_dir}" -type l -print -quit | grep -q .; then echo "model tree contains a symlink: \${model_dir}" >&2; exit 1; fi
      inventory="\$(mktemp)"
      while IFS= read -r -d '' file; do
        rel="\${file#\${model_dir}/}"; size="\$(stat -c %s "\${file}")"; file_sha="\$(sha256sum "\${file}" | cut -d' ' -f1)"
        printf '%s\\0%s\\0%s\\n' "\${rel}" "\${size}" "\${file_sha}" >> "\${inventory}"
        observed_files=\$((observed_files + 1)); observed_bytes=\$((observed_bytes + size))
      done < <(find "\${model_dir}" -type f -print0 | sort -z)
      observed_sha="\$(sha256sum "\${inventory}" | cut -d' ' -f1)"; rm -f "\${inventory}"
      [[ "\${observed_sha}" == "\${expected_sha}" && "\${observed_files}" == "\${expected_files}" && "\${observed_bytes}" == "\${expected_bytes}" ]] || {
        echo "model identity changed after preflight: \${model_dir}; sha=\${observed_sha}/\${expected_sha} files=\${observed_files}/\${expected_files} bytes=\${observed_bytes}/\${expected_bytes}" >&2; exit 1;
      }
    }
    snapshot_model() {
      local model_name="\$1" json_key="\$2"
      local source="/weights/dorado/1.3.1/\${model_name}" target="sealed_models/\${model_name}"
      local expected_sha expected_files expected_bytes
      expected_sha="\$(jq -r "\${json_key}.aggregate_sha256" dorado_preflight.json)"
      expected_files="\$(jq -r "\${json_key}.files" dorado_preflight.json)"
      expected_bytes="\$(jq -r "\${json_key}.bytes" dorado_preflight.json)"
      verify_model_tree "\${source}" "\${expected_sha}" "\${expected_files}" "\${expected_bytes}"
      mkdir -p "\${target}"; cp -a --reflink=auto "\${source}/." "\${target}/"
      verify_model_tree "\${target}" "\${expected_sha}" "\${expected_files}" "\${expected_bytes}"
    }

    runtime_expected="\$(jq -r '.runtime.assets.runtime_sif.sha256' dorado_preflight.json)"
    runtime_observed="\$(sha256sum /runtime/dorado.sif | cut -d' ' -f1)"
    [[ "\${runtime_observed}" == "\${runtime_expected}" ]] || { echo 'Dorado runtime changed after preflight' >&2; exit 1; }

    pod5_source=${doradoShellQuote(pod5_dir)}
    mkdir -p sealed_pod5 sealed_models
    while IFS=\$'\t' read -r relative expected_sha expected_bytes; do
      [[ "\${relative}" != /* && "\${relative}" != *'..'* ]]
      source_file="\${pod5_source}/\${relative}"; target_file="sealed_pod5/\${relative}"
      [[ -f "\${source_file}" && ! -L "\${source_file}" ]]
      [[ "\$(sha256sum "\${source_file}" | cut -d' ' -f1)" == "\${expected_sha}" && "\$(stat -c %s "\${source_file}")" == "\${expected_bytes}" ]] || { echo "POD5 input changed after preflight: \${relative}" >&2; exit 1; }
      mkdir -p "\$(dirname "\${target_file}")"; cp --reflink=auto --preserve=mode,timestamps "\${source_file}" "\${target_file}"
      [[ "\$(sha256sum "\${target_file}" | cut -d' ' -f1)" == "\${expected_sha}" ]] || { echo "POD5 snapshot identity mismatch: \${relative}" >&2; exit 1; }
    done < <(jq -r '.inputs.files[] | [.relative_path,.sha256,.bytes] | @tsv' dorado_preflight.json)

    if [[ -n "\${pair_relative}" ]]; then
      pair_sha="\$(jq -r '.pairs.sha256' dorado_preflight.json)"; pair_source="\${pod5_source}/\${pair_relative}"; pair_target="sealed_pod5/\${pair_relative}"
      [[ -f "\${pair_source}" && ! -L "\${pair_source}" && "\$(sha256sum "\${pair_source}" | cut -d' ' -f1)" == "\${pair_sha}" ]] || { echo 'duplex pair file changed after preflight' >&2; exit 1; }
      mkdir -p "\$(dirname "\${pair_target}")"; cp --reflink=auto "\${pair_source}" "\${pair_target}"
      [[ "\$(sha256sum "\${pair_target}" | cut -d' ' -f1)" == "\${pair_sha}" ]] || { echo 'duplex pair snapshot identity mismatch' >&2; exit 1; }
    fi
    if [[ -n "\${sample_relative}" ]]; then
      sample_sha="\$(jq -r '.barcoding.sample_sheet.sha256' dorado_preflight.json)"; sample_source="\${pod5_source}/\${sample_relative}"; sample_target="sealed_pod5/\${sample_relative}"
      [[ -f "\${sample_source}" && ! -L "\${sample_source}" && "\$(sha256sum "\${sample_source}" | cut -d' ' -f1)" == "\${sample_sha}" ]] || { echo 'sample sheet changed after preflight' >&2; exit 1; }
      mkdir -p "\$(dirname "\${sample_target}")"; cp --reflink=auto "\${sample_source}" "\${sample_target}"
      [[ "\$(sha256sum "\${sample_target}" | cut -d' ' -f1)" == "\${sample_sha}" ]] || { echo 'sample-sheet snapshot identity mismatch' >&2; exit 1; }
    fi
    snapshot_model "\${model_id}" '.runtime.assets.models.base'
    if [[ -n "\${mod_model_id}" ]]; then snapshot_model "\${mod_model_id}" '.runtime.assets.models.modified_bases'; fi
    if [[ "\${mode}" == duplex ]]; then stereo_model_id="\$(jq -r '.selection.stereo_model_id' dorado_preflight.json)"; snapshot_model "\${stereo_model_id}" '.runtime.assets.models.stereo'; fi

    visible_gpus="\${CUDA_VISIBLE_DEVICES:-0}"
    IFS=',' read -r -a gpu_ids <<<"\${visible_gpus}"
    device_indices="\${device#cuda:}"
    if [[ "\${device_indices}" != all ]]; then
      declare -A selected_logical=()
      IFS=',' read -r -a logical_ids <<<"\${device_indices}"
      for logical_id in "\${logical_ids[@]}"; do
        if (( logical_id < 0 || logical_id >= \${#gpu_ids[@]} )) || [[ -n "\${selected_logical[\${logical_id}]:-}" ]]; then
          echo "Dorado device selection \${device} is invalid for \${#gpu_ids[@]} visible GPU(s)" >&2
          exit 1
        fi
        selected_logical["\${logical_id}"]=1
      done
    fi
    for gpu_id in "\${gpu_ids[@]}"; do
      gpu_memory="\$(nvidia-smi --id="\${gpu_id}" --query-gpu=memory.total,memory.free --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
      gpu_total="\${gpu_memory%%,*}"; gpu_free="\${gpu_memory##*,}"
      if (( gpu_total < min_gpu_total || gpu_free < min_gpu_free )); then
        echo "Dorado GPU admission denied for \${gpu_id}: total=\${gpu_total}MiB free=\${gpu_free}MiB required_total=\${min_gpu_total}MiB required_free=\${min_gpu_free}MiB" >&2
        exit 1
      fi
    done

    pod5_root="\$PWD/sealed_pod5"
    base_model="\$PWD/sealed_models/\${model_id}"
    [[ -d "\${base_model}" ]]
    command=(dorado)
    if [[ "\${mode}" == duplex ]]; then command+=(duplex); else command+=(basecaller); fi
    command+=("\${base_model}" "\${pod5_root}" --recursive --models-directory "\$PWD/sealed_models" --batchsize "\${batch_size}" --device "\${device}")
    if [[ "\${mode}" == simplex ]]; then command+=(--min-qscore "\${min_qscore}"); fi
    if [[ -n "\${pair_relative}" ]]; then command+=(--pairs "\${pod5_root}/\${pair_relative}"); fi
    if [[ -n "\${barcode_kit}" ]]; then command+=(--kit-name "\${barcode_kit}"); fi
    if [[ -n "\${sample_relative}" ]]; then command+=(--sample-sheet "\${pod5_root}/\${sample_relative}"); fi
    if [[ -n "\${mod_model_id}" ]]; then command+=(--modified-bases-models "\$PWD/sealed_models/\${mod_model_id}"); fi
    if [[ "\${molecule}" == dna && "\${mode}" == simplex && "\${trim_adapters}" == false ]]; then
      bash ${doradoShellQuote(params.code_root + '/scripts/dorado_supports_option.sh')} dorado basecaller --no-trim || { echo 'locked Dorado runtime lacks --no-trim' >&2; exit 1; }
      command+=(--no-trim)
    fi
    if [[ '${summaryRequested}' == 'true' && "\${mode}" == simplex ]] && bash ${doradoShellQuote("${params.code_root}/scripts/dorado_supports_option.sh")} dorado basecaller --emit-summary; then command+=(--emit-summary); fi
    emit_moves='${movesRequested}'
    if [[ "\${emit_moves}" == true ]]; then
      [[ "\${mode}" == simplex ]] || { echo 'move-tag emission is not qualified for duplex' >&2; exit 1; }
      bash ${doradoShellQuote(params.code_root + '/scripts/dorado_supports_option.sh')} dorado basecaller --emit-moves || { echo 'locked Dorado runtime lacks --emit-moves' >&2; exit 1; }
      command+=(--emit-moves)
    fi

    : > basecall.log
    "\${command[@]}" > calls.bam 2>> basecall.log
    samtools quickcheck -u -v calls.bam
    if [[ -f sequencing_summary.txt ]]; then mv sequencing_summary.txt sequencing_summary.tsv; fi
    calls_sha="\$(sha256sum calls.bam | cut -d' ' -f1)"
    preflight_sha="\$(sha256sum dorado_preflight.json | cut -d' ' -f1)"
    read_count="\$(samtools view -c calls.bam)"
    read_inventory_sha256="\$(samtools view calls.bam | cut -f1 | LC_ALL=C sort | sha256sum | cut -d' ' -f1)"
    mv_tag_count="\$(samtools view calls.bam | awk 'BEGIN{n=0} {for(i=12;i<=NF;i++) if(\$i ~ /^mv:B:/) {n++; break}} END{print n}')"
    ts_tag_count="\$(samtools view calls.bam | awk 'BEGIN{n=0} {for(i=12;i<=NF;i++) if(\$i ~ /^ts:i:/) {n++; break}} END{print n}')"
    ns_tag_count="\$(samtools view calls.bam | awk 'BEGIN{n=0} {for(i=12;i<=NF;i++) if(\$i ~ /^ns:i:/) {n++; break}} END{print n}')"
    if [[ "\${emit_moves}" == true ]] && (( mv_tag_count != read_count || ts_tag_count != read_count || ns_tag_count != read_count )); then
      echo "Dorado move-tag contract incomplete: reads=\${read_count} mv=\${mv_tag_count} ts=\${ts_tag_count} ns=\${ns_tag_count}" >&2; exit 1
    fi
    duplex_dx1=0
    if [[ "\${mode}" == duplex ]]; then
      duplex_dx1="\$(samtools view calls.bam | awk 'BEGIN{n=0} {for(i=12;i<=NF;i++) if(\$i=="dx:i:1") {n++; break}} END{print n}')"
      (( read_count > 0 && duplex_dx1 == read_count )) || { echo "Dorado duplex output lacks authoritative dx:i:1 calls" >&2; exit 1; }
    fi
    jq -n --arg schema 'biomodstack.dorado_runtime_provenance.v1' --arg mode "\${mode}" --arg model_id "\${model_id}" --arg calls_sha256 "\${calls_sha}" --arg preflight_sha256 "\${preflight_sha}" --arg runtime_sha256 "\${runtime_observed}" --arg read_inventory_sha256 "\${read_inventory_sha256}" --argjson emit_moves "\${emit_moves}" --argjson read_count "\${read_count}" --argjson mv_tag_count "\${mv_tag_count}" --argjson ts_tag_count "\${ts_tag_count}" --argjson ns_tag_count "\${ns_tag_count}" --argjson duplex_dx1 "\${duplex_dx1}" '{schema:\$schema,mode:\$mode,model_id:\$model_id,preflight_sha256:\$preflight_sha256,runtime_sha256:\$runtime_sha256,emit_moves:\$emit_moves,calls_bam:{sha256:\$calls_sha256,read_count:\$read_count,read_inventory_sha256:\$read_inventory_sha256,move_tags:{mv:\$mv_tag_count,ts:\$ts_tag_count,ns:\$ns_tag_count},duplex_dx1:\$duplex_dx1},network:"denied_by_namespace",model_download:"denied_by_namespace_and_sealed_models"}' > dorado_runtime_provenance.json
    """
}

process DoradoDemux {
    label 'dorado_cpu'
    publishDir "${params.out_dir}/demux", mode: 'copy'
    tag 'dorado-demux'

    input:
    path bam
    path preflight_json

    output:
    path "demux", emit: directory
    path "demux_manifest.json", emit: manifest
    path "per_barcode_units.json", emit: units
    path "demux.log", emit: log

    script:
    """
    set -euo pipefail
    [[ "\$(jq -r '.schema' ${doradoShellQuote(preflight_json)})" == 'biomodstack.dorado_preflight.v1' ]]
    [[ "\$(jq -r '.selection.mode' ${doradoShellQuote(preflight_json)})" == 'simplex' ]]
    [[ -n "\$(jq -r '.barcoding.kit // empty' ${doradoShellQuote(preflight_json)})" ]]
    mkdir -p demux
    dorado demux --no-classify --output-dir demux ${doradoShellQuote(bam)} > demux.log 2>&1
    shopt -s globstar nullglob
    declare -A alias_to_barcode=()
    declare -A barcode_to_alias=()
    while IFS=\$'\t' read -r alias canonical_barcode; do
      if [[ -n "\${alias}" && -n "\${canonical_barcode}" ]]; then
        alias_to_barcode["\${alias}"]="\${canonical_barcode}"
        barcode_to_alias["\${canonical_barcode}"]="\${alias}"
      fi
    done < <(jq -r '.barcoding.sample_sheet.assignments[]? | [.alias,.barcode] | @tsv' ${doradoShellQuote(preflight_json)})
    is_canonical_label() {
      local value="\$1"
      [[ "\${value}" == 'unclassified' || "\${value}" =~ ^barcode(0[1-9]|[1-8][0-9]|9[0-6])\$ ]]
    }
    canonical_label() {
      local candidate="\$1" filename stem mapped
      filename="\${candidate##*/}"
      stem="\${filename%.bam}"
      for segment in "\${filename}" "\${stem}"; do
        if is_canonical_label "\${segment}"; then printf '%s\n' "\${segment}"; return 0; fi
        if is_canonical_label "\${stem}"; then printf '%s\n' "\${stem}"; return 0; fi
        mapped="\${alias_to_barcode[\${segment}]:-}"
        if is_canonical_label "\${mapped}"; then printf '%s\n' "\${mapped}"; return 0; fi
      done
      echo "CRITICAL_FAILURE: UNKNOWN_DEMUX_LABEL candidate=\${candidate}" >&2
      return 1
    }
    source_bams=(demux/**/*.bam)
    if (( \${#source_bams[@]} == 0 )); then echo 'Dorado demux emitted no BAM units' >&2; exit 1; fi
    labels=()
    for source_bam in "\${source_bams[@]}"; do
      [[ -L "\${source_bam}" ]] && { echo 'demux symlink forbidden' >&2; exit 1; }
      samtools quickcheck -u -v "\${source_bam}"
      if ! source_label="\$(canonical_label "\${source_bam}")"; then
        echo "CRITICAL_FAILURE: UNKNOWN_DEMUX_LABEL" >&2
        exit 86
      fi
      labels+=("\${source_label}")
    done
    mapfile -t labels < <(printf '%s\n' "\${labels[@]}" | sort -u)
    source_calls_sha="\$(sha256sum ${doradoShellQuote(bam)} | cut -d' ' -f1)"
    source_read_count="\$(samtools view -c ${doradoShellQuote(bam)})"
    preflight_sha="\$(sha256sum ${doradoShellQuote(preflight_json)} | cut -d' ' -f1)"
    mkdir -p demux/units demux/manifests
    units='[]'; total=0
    for label in "\${labels[@]}"; do
      label_bams=()
      for source_bam in "\${source_bams[@]}"; do
        if ! source_label="\$(canonical_label "\${source_bam}")"; then
          echo "CRITICAL_FAILURE: UNKNOWN_DEMUX_LABEL" >&2
          exit 86
        fi
        [[ "\${source_label}" == "\${label}" ]] && label_bams+=("\${source_bam}")
      done
      unit_bam="demux/units/\${label}.bam"
      if (( \${#label_bams[@]} == 1 )); then cp "\${label_bams[0]}" "\${unit_bam}"; else samtools merge -u -f "\${unit_bam}" "\${label_bams[@]}"; fi
      samtools quickcheck -u -v "\${unit_bam}"
      reads="\$(samtools view -c "\${unit_bam}")"; sha="\$(sha256sum "\${unit_bam}" | cut -d' ' -f1)"
      sample_alias="\${barcode_to_alias[\${label}]:-}"
      unit_manifest="demux/manifests/\${label}.json"
      jq -n --arg schema 'biomodstack.dorado_barcode_unit.v1' --arg unit_id "\${label}" --arg sample_alias "\${sample_alias}" --arg bam_path "\${unit_bam}" --arg bam_sha256 "\${sha}" --arg source_calls_sha256 "\${source_calls_sha}" --arg preflight_sha256 "\${preflight_sha}" --argjson read_count "\${reads}" '{schema:\$schema,unit_id:\$unit_id,sample_alias:(if \$sample_alias == "" then null else \$sample_alias end),bam_path:\$bam_path,bam_sha256:\$bam_sha256,read_count:\$read_count,source_calls_sha256:\$source_calls_sha256,preflight_sha256:\$preflight_sha256}' > "\${unit_manifest}"
      unit_manifest_sha="\$(sha256sum "\${unit_manifest}" | cut -d' ' -f1)"
      item="\$(jq -n --arg unit_id "\${label}" --arg sample_alias "\${sample_alias}" --arg bam_path "\${unit_bam}" --arg bam_sha256 "\${sha}" --arg unit_manifest_path "\${unit_manifest}" --arg unit_manifest_sha256 "\${unit_manifest_sha}" --arg source_calls_sha256 "\${source_calls_sha}" --arg preflight_sha256 "\${preflight_sha}" --argjson read_count "\${reads}" '{unit_id:\$unit_id,sample_alias:(if \$sample_alias == "" then null else \$sample_alias end),bam_path:\$bam_path,bam_sha256:\$bam_sha256,unit_manifest_path:\$unit_manifest_path,unit_manifest_sha256:\$unit_manifest_sha256,read_count:\$read_count,source_calls_sha256:\$source_calls_sha256,preflight_sha256:\$preflight_sha256,resubmission_params:{bam_path:\$bam_path,barcode_unit:\$unit_id,sample_alias:(if \$sample_alias == "" then null else \$sample_alias end)}}')"
      units="\$(jq --argjson item "\${item}" '. + [\$item]' <<<"\${units}")"; total=\$((total + reads))
    done
    (( total == source_read_count )) || { echo "demux read-count parity failed: source=\${source_read_count} units=\${total}" >&2; exit 1; }
    jq -n --arg schema 'biomodstack.dorado_demux.v1' --arg source 'dorado_basecaller_inline' --arg preflight_sha256 "\${preflight_sha}" --arg source_calls_sha256 "\${source_calls_sha}" --argjson source_read_count "\${source_read_count}" --argjson total_reads "\${total}" --argjson units "\${units}" '{schema:\$schema,barcode_classification_source:\$source,preflight_sha256:\$preflight_sha256,source_calls:{sha256:\$source_calls_sha256,read_count:\$source_read_count},total_reads:\$total_reads,units:\$units}' > demux_manifest.json
    jq -n --arg schema 'biomodstack.dorado_barcode_units.v1' --argjson units "\${units}" '{schema:\$schema,units:\$units}' > per_barcode_units.json
    """
}
