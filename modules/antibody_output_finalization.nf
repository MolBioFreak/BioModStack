nextflow.enable.dsl = 2

process FinalizeSequentialValidationOutputs {
    label 'process_low'

    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.pdb"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.json"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.cif"
    publishDir "${params.out_dir}/pdb_files", mode: 'copy', pattern: "validated_designs/*.npz"
    publishDir "${params.out_dir}/pdb_files/aligned_error", mode: 'copy', pattern: "validated_designs/aligned_error/*.json", saveAs: { filename -> filename.split('/')[-1] }
    publishDir "${params.out_dir}", mode: 'copy', pattern: "aggregation_report.json"

    input:
    path artifact_manifest_file

    output:
    path "validated_designs/*.pdb", emit: pdbs, optional: true
    path "validated_designs/*.json", emit: scores, optional: true
    path "validated_designs/*.npz", emit: aligned_error, optional: true
    path "validated_designs/aligned_error/*.json", emit: aligned_error_json, optional: true
    path "aggregation_report.json", emit: report

    script:
    """
    #!/bin/bash
    set -euo pipefail

    export JOB_ID="${params.job_id ?: 'unknown'}"
    export BATCH_NAME="${params.batch_name ?: params.job_name ?: 'sequential_validation'}"
    export OUTPUT_PATH="${params.out_dir}/pdb_files"

    mkdir -p validated_designs

    python3 <<'PY'
import json
import os
import shutil
from pathlib import Path

manifest = json.loads(Path("${artifact_manifest_file}").read_text(encoding="utf-8"))
validated_dir = Path("validated_designs")
validated_dir.mkdir(parents=True, exist_ok=True)
aligned_error_dir = validated_dir / "aligned_error"
aligned_error_dir.mkdir(parents=True, exist_ok=True)

total_pdbs = 0
report_files: list[str] = []

def choose_dest_name(base_dir: Path, filename: str) -> Path:
    candidate = base_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while True:
        candidate = base_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1

for key in ("pdbs", "cifs", "scores", "aligned_error"):
    for raw_path in manifest.get(key) or []:
        source_path = Path(str(raw_path)).expanduser()
        if not source_path.exists():
            print(f"[FinalizeSequentialValidationOutputs] WARNING: missing {key} artifact: {source_path}")
            continue
        dest_root = aligned_error_dir if key == "aligned_error" and source_path.suffix.lower() == ".json" else validated_dir
        dest_path = choose_dest_name(dest_root, source_path.name)
        shutil.copy2(source_path, dest_path)
        if source_path.suffix.lower() == ".pdb":
            total_pdbs += 1
            if len(report_files) < 50:
                report_files.append(str(dest_path))

Path("report_files.txt").write_text("\\n".join(report_files), encoding="utf-8")
Path("aggregation_report.json").write_text(
    json.dumps(
        {
            "parent_job_id": os.environ["JOB_ID"],
            "batch_name": os.environ["BATCH_NAME"],
            "children_processed": 1,
            "total_validated_designs": total_pdbs,
            "output_path": os.environ["OUTPUT_PATH"],
            "status": "complete",
        },
        indent=2,
    ),
    encoding="utf-8",
)
print(f"[FinalizeSequentialValidationOutputs] Copied {total_pdbs} validated PDB(s) from explicit manifest")
PY

    TOTAL_PDBS=\$(python3 - <<'PY'
import json
from pathlib import Path
report = json.loads(Path("aggregation_report.json").read_text(encoding="utf-8"))
print(int(report.get("total_validated_designs") or 0))
PY
    )

    if [ "\$TOTAL_PDBS" -gt 0 ]; then
        mkdir -p "${params.out_dir}/pdb_files/validated_designs"
        while IFS= read -r -d '' staged_artifact; do
            rel_path="\${staged_artifact#validated_designs/}"
            dest_dir="${params.out_dir}/pdb_files/validated_designs/\$(dirname "\$rel_path")"
            mkdir -p "\$dest_dir"
            cp -f "\$staged_artifact" "\$dest_dir/"
        done < <(find validated_designs -type f -print0)
        cp -f aggregation_report.json "${params.out_dir}/aggregation_report.json"
        echo "Triggering result ingestion for parent job..."
        python3 ${params.code_root}/scripts/result_ingester.py \\
            --job_id "${params.job_id ?: 'unknown'}" \\
            --results_dir "${params.out_dir}" \\
            --api_url "${params.api_url}" \\
            2>&1 | tee ingest.log || echo "Warning: Ingestion had issues (non-fatal)"

        if [ -s report_files.txt ]; then
            mapfile -t report_files < report_files.txt
            python3 ${params.code_root}/scripts/stage_reporter.py \\
                "${params.job_id ?: 'unknown'}" \\
                "structure_validation" \\
                "complete" \\
                "\${report_files[@]}" \\
                || echo "Warning: Failed to report sequential structure_validation completion"
        fi
    fi

    echo "Sequential validation closeout complete: \$TOTAL_PDBS designs ready for analytics"
    """
}

process FinalizeTerminalAntibodyOutputs {
    label 'process_low'

    publishDir "${params.out_dir}", mode: 'copy', pattern: "terminal_closeout_report.json"

    input:
    path terminal_pdb_list_file

    output:
    path "terminal_closeout_report.json", emit: report

    script:
    """
    #!/bin/bash
    set -euo pipefail

    if [ ! -e terminal_pdbs.list ] || [ ! "${terminal_pdb_list_file}" -ef terminal_pdbs.list ]; then
        cp "${terminal_pdb_list_file}" terminal_pdbs.list
    fi

    TOTAL_PDBS=\$(grep -c . terminal_pdbs.list || true)

    cat > terminal_closeout_report.json << EOF
{
    "job_id": "${params.job_id ?: 'unknown'}",
    "job_name": "${params.job_name ?: 'antibody_batch'}",
    "total_terminal_designs": \$TOTAL_PDBS,
    "output_path": "${params.out_dir}",
    "status": "complete"
}
EOF

    if [ \$TOTAL_PDBS -gt 0 ]; then
        echo "Triggering terminal result ingestion for parent job..."
        python3 ${params.code_root}/scripts/result_ingester.py \\
            --job_id "${params.job_id ?: 'unknown'}" \\
            --results_dir "${params.out_dir}" \\
            --api_url "${params.api_url}" \\
            2>&1 | tee ingest.log || echo "Warning: Terminal ingestion had issues (non-fatal)"
    fi

    echo "Terminal antibody closeout complete: \$TOTAL_PDBS designs ready for analytics"
    """
}
