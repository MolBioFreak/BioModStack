#!/usr/bin/env python3
"""
DiffDock Gradio UI Wrapper for BioModStack
Runs on host, calls DiffDock container for inference
"""

import gradio as gr
import subprocess
import tempfile
import os
import shutil
from pathlib import Path

# Configuration
CONTAINER_PATH = str(Path(__file__).parent.parent / "containers" / "diffdock.sif")
PROJECT_DIR = str(Path(__file__).parent.parent)

def run_diffdock(
    protein_file,
    ligand_smiles: str,
    num_poses: int = 10,
    inference_steps: int = 20
):
    """Run DiffDock inference using the container"""
    
    if protein_file is None:
        return "❌ Please upload a protein PDB file", None
    
    if not ligand_smiles or ligand_smiles.strip() == "":
        return "❌ Please enter a ligand SMILES string", None
    
    # Create temp working directory
    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy protein file
        protein_path = Path(tmpdir) / "protein.pdb"
        shutil.copy(protein_file, protein_path)
        
        # Create input CSV
        csv_path = Path(tmpdir) / "input.csv"
        with open(csv_path, 'w') as f:
            f.write("complex_name,protein_path,ligand_description,protein_sequence\n")
            f.write(f"docking,{protein_path},{ligand_smiles},\n")
        
        results_dir = Path(tmpdir) / "results"
        
        # Build command
        cmd = [
            "apptainer", "exec",
            "--nv",
            "--env", "CUDA_VISIBLE_DEVICES=2",
            "--env", "CUDA_DEVICE_ORDER=PCI_BUS_ID",
            "--env", "HF_HOME=/cache/huggingface",
            "--env", "TORCH_HOME=/cache/torch",
            f"--pwd={PROJECT_DIR}",
            "--bind", f"/home/dalab/.cache/huggingface:/cache/huggingface",
            "--bind", f"/home/dalab/.cache/torch:/cache/torch",
            CONTAINER_PATH,
            "python3", "/app/DiffDock/inference.py",
            "--config", "/app/DiffDock/default_inference_args.yaml",
            "--protein_ligand_csv", str(csv_path),
            "--out_dir", str(results_dir),
            "--inference_steps", str(inference_steps),
            "--samples_per_complex", str(num_poses),
            "--batch_size", "1"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                return f"❌ DiffDock error:\n{result.stderr}", None
            
            # Find results
            docking_dir = results_dir / "docking"
            if not docking_dir.exists():
                return f"❌ No results generated\n{result.stdout}", None
            
            # Collect SDF files
            sdf_files = sorted(docking_dir.glob("*.sdf"))
            
            # Build results summary
            output_text = f"✅ Generated {len(sdf_files)} poses:\n\n"
            results_data = []
            
            for sdf in sdf_files:
                name = sdf.stem
                if "confidence" in name:
                    parts = name.split("_confidence")
                    rank = parts[0].replace("rank", "")
                    conf = parts[1] if len(parts) > 1 else "N/A"
                    output_text += f"  Rank {rank}: Confidence {conf}\n"
                    
                    # Copy to persistent location
                    dest = Path(PROJECT_DIR) / "test_diffdock" / "latest_results" / sdf.name
                    dest.parent.mkdir(exist_ok=True, parents=True)
                    shutil.copy(sdf, dest)
            
            output_text += f"\n📁 Results saved to: test_diffdock/latest_results/"
            
            # Return best pose for download
            best_pose = docking_dir / "rank1.sdf"
            if best_pose.exists():
                return output_text, str(best_pose)
            elif sdf_files:
                return output_text, str(sdf_files[0])
            else:
                return output_text, None
                
        except subprocess.TimeoutExpired:
            return "❌ DiffDock timed out after 10 minutes", None
        except Exception as e:
            return f"❌ Error: {str(e)}", None


# Create Gradio interface
with gr.Blocks(title="DiffDock - Protein-Ligand Docking") as demo:
    gr.Markdown("""
    # 🧬 DiffDock - AI-Powered Molecular Docking
    
    Upload a protein structure and enter a ligand SMILES to predict binding poses.
    
    **Part of BioModStack | Running on RTX 3090**
    """)
    
    with gr.Row():
        with gr.Column():
            protein_input = gr.File(
                label="Protein PDB File",
                file_types=[".pdb"],
                type="filepath"
            )
            ligand_input = gr.Textbox(
                label="Ligand SMILES",
                placeholder="e.g., CC(=O)Oc1ccccc1C(=O)O (Aspirin)",
                lines=2
            )
            
            with gr.Row():
                num_poses = gr.Slider(
                    minimum=1, maximum=40, value=10, step=1,
                    label="Number of Poses"
                )
                inference_steps = gr.Slider(
                    minimum=5, maximum=40, value=20, step=5,
                    label="Inference Steps"
                )
            
            submit_btn = gr.Button("🚀 Run Docking", variant="primary")
        
        with gr.Column():
            output_text = gr.Textbox(label="Results", lines=10)
            output_file = gr.File(label="Download Best Pose (SDF)")
    
    # Examples
    gr.Examples(
        examples=[
            ["CC(=O)Oc1ccccc1C(=O)O"],  # Aspirin
            ["CC(C)Cc1ccc(C(C)C(=O)O)cc1"],  # Ibuprofen
            ["Cn1cnc2c1c(=O)n(c(=O)n2C)C"],  # Caffeine
        ],
        inputs=[ligand_input],
        label="Example Ligands"
    )
    
    submit_btn.click(
        fn=run_diffdock,
        inputs=[protein_input, ligand_input, num_poses, inference_steps],
        outputs=[output_text, output_file]
    )

if __name__ == "__main__":
    print("🚀 Starting DiffDock UI on http://0.0.0.0:7860")
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
