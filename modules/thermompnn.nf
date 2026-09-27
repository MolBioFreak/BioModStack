process THERMOMPNN {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/stability_tools.sif"
    containerOptions "--nv"

    input:
    tuple val(meta), path(pdb)

    output:
    tuple val(meta), path("*_stability.csv"), optional: true, emit: stability
    path "thermompnn.log"

    script:
    """
    cat > run_thermompnn.py << 'PYEOF'
import subprocess
import sys
import os
import tempfile
import shutil
from pathlib import Path

pdb_path = sys.argv[1]
out_file = sys.argv[2]
# A retried task must not publish an earlier score as this attempt's result.
Path(out_file).unlink(missing_ok=True)

# ThermoMPNN inference script location
script_path = "/opt/ThermoMPNN/analysis/custom_inference.py"
default_model_path = "/opt/ThermoMPNN/models/thermoMPNN_default.pt"
model_path = default_model_path

if not os.path.exists(script_path):
    print(f"Error: custom_inference.py not found at {script_path}")
    sys.exit(1)

if not os.path.exists(model_path):
    print(f"Warning: Default model not found at {default_model_path}, using BMS weights if available")
    weights_root = (
        os.environ.get("BMS_THERMOMPNN_WEIGHTS")
        or os.environ.get("BMS_WEIGHTS")
        or "${params.weights_root}"
    )
    candidate = Path(weights_root) / "thermompnn" / "thermoMPNN_default.pt"
    if candidate.exists():
        model_path = str(candidate)
        print(f"Using ThermoMPNN weights from {model_path}")
    else:
        print(f"Error: ThermoMPNN model not found at {default_model_path} or {candidate}")
        sys.exit(1)

# Run inference
cmd = [
    sys.executable,
    script_path,
    "--pdb", os.path.abspath(pdb_path),
    "--model_path", model_path,
]

print(f"Running ThermoMPNN: {' '.join(cmd)}")

# Keep native import/config resolution, but never read shared output files.
# A fresh directory also isolates retries from files left by earlier attempts.
with tempfile.TemporaryDirectory(prefix="thermompnn-", dir=".") as attempt:
    cmd.extend(["--out_dir", str(Path(attempt).resolve())])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/opt/ThermoMPNN/analysis")
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f"ThermoMPNN failed with exit status {result.returncode}; scores unavailable")
        sys.exit(1)

    # Match the installed native writer exactly (including its rstrip semantics).
    pdb_id = os.path.basename(pdb_path).rstrip('.pdb')
    native_csv = Path(attempt) / f"ThermoMPNN_inference_{pdb_id}.csv"
    if not native_csv.is_file():
        print("ThermoMPNN produced no inference output; scores unavailable")
        sys.exit(1)
    shutil.copyfile(native_csv, out_file)
    print(f"Output saved to {out_file}")

PYEOF

    python3 run_thermompnn.py "${pdb}" "${meta.id}_stability.csv" > thermompnn.log 2>&1 || echo "Optional ThermoMPNN scores unavailable; see native outcome above" >> thermompnn.log
    """
}
