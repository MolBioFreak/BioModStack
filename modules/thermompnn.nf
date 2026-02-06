process THERMOMPNN {
    tag "${meta.id}"
    label 'process_gpu'
    container "${params.container_dir}/stability_tools.sif"
    containerOptions "--nv"

    input:
    tuple val(meta), path(pdb)

    output:
    tuple val(meta), path("*_stability.csv"), emit: stability
    path "thermompnn.log"

    script:
    """
    cat > run_thermompnn.py << 'PYEOF'
import subprocess
import sys
import os
import glob
import shutil
from pathlib import Path

pdb_path = sys.argv[1]
out_file = sys.argv[2]

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

try:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/opt/ThermoMPNN/analysis")
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    
    # Find output CSV and rename to our convention
    csvs = glob.glob("*.csv") + glob.glob("/opt/ThermoMPNN/analysis/*.csv")
    for csv in csvs:
        if "inference" in csv.lower() or "thermo" in csv.lower():
            shutil.copy(csv, out_file)
            print(f"Output saved to {out_file}")
            break
    else:
        # Create empty file if no output found
        with open(out_file, 'w') as f:
            f.write("sequence_id,ddG_pred\\n")
            f.write(f"{os.path.basename(pdb_path)},N/A\\n")
        print("Warning: No inference output found, created placeholder")
        
except subprocess.CalledProcessError as e:
    print(f"ThermoMPNN failed: {e}")
    # Create placeholder on failure so pipeline can continue
    with open(out_file, 'w') as f:
        f.write("sequence_id,ddG_pred\\n")
        f.write(f"{os.path.basename(pdb_path)},ERROR\\n")

PYEOF

    python3 run_thermompnn.py "${pdb}" "${meta.id}_stability.csv" > thermompnn.log 2>&1
    """
}
