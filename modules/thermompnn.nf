process THERMOMPNN {
    tag "${meta.id}"
    label 'process_gpu'
    container 'apptainer/stability_tools.sif'

    input:
    tuple val(meta), path(pdb)

    output:
    tuple val(meta), path("*_stability.csv"), emit: stability
    path "thermompnn.log"

    script:
    """
    # ThermoMPNN inference
    # We attempt to use the repository scripts directly.
    
    export PYTHONPATH="/opt/ThermoMPNN:\$PYTHONPATH"
    
    cat <<EOF > run_stability.py
    import sys
    import os
    import glob
    import subprocess
    
    pdb_path = "${pdb}"
    out_file = "${meta.id}_stability.csv"
    
    # Check where ThermoMPNN is
    base_dir = "/opt/ThermoMPNN"
    if not os.path.exists(base_dir):
        print("Error: ThermoMPNN not found at /opt/ThermoMPNN")
        sys.exit(1)
        
    # Look for inference script
    # Common names in this repo: PredictStability.py, predict.py
    script_candidates = ["PredictStability.py", "predict.py", "custom_inference.py"]
    script_to_run = None
    
    for s in script_candidates:
        p = os.path.join(base_dir, s)
        if os.path.exists(p):
            script_to_run = p
            break
            
    if script_to_run:
        print(f"Running {script_to_run}")
        cmd = [sys.executable, script_to_run, "--pdb_path", pdb_path, "--out_file", out_file]
        # Some versions might take --pdb or similar, but let's assume standard args or fail
        # If standard args fail, we might need a specific wrapper.
        
        # For Kuhlman-Lab ThermoMPNN specifically:
        # It typically takes a --pdb argument and produces output.
        # Use simple call first.
        subprocess.check_call(cmd)
        
    else:
        print("No standard inference script found. Attempting to use library if possible.")
        # Fallback: Create a dummy CSV if we truly fail, to prevent pipeline crash (mvp)
        # Or better, fail.
        print("Error: Could not find inference script.")
        sys.exit(1)
        
EOF
    
    python3 run_stability.py > thermompnn.log 2>&1
    """
}
