#!/usr/bin/env python3
import sys
import os
import argparse
import shutil
from pathlib import Path
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

def main():
    parser = argparse.ArgumentParser(description="Wrapper to run RF3 structure prediction")
    parser.add_argument('--input-dir', required=True, help="Directory containing input PDBs")
    parser.add_argument('--output-dir', required=True, help="Directory for outputs")
    parser.add_argument('--extra-config', nargs='*', default=[], help="Extra Hydra overrides")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find inputs - match generic pdb names
    inputs = list(input_dir.glob("*.pdb"))
    if not inputs:
        print(f"No PDB inputs found in {input_dir}")
        sys.exit(1)
    
    # Convert paths to absolute strings
    inputs_str = [str(p.absolute()) for p in inputs]
    
    print(f"Found {len(inputs)} input PDBs")
    
    # Correct path for installed package config
    config_path = "/usr/local/lib/python3.12/dist-packages/rf3/configs"
    
    # Initial overrides
    overrides = [
        f"out_dir={str(output_dir.absolute())}",
        "inference_engine=rf3",
        "ckpt_path=/foundry/checkpoints/rf3_foundry_01_24_latest_remapped.ckpt" 
    ] + args.extra_config

    print(f"Loading config from {config_path}")
    print(f"Ids: {[p.name for p in inputs]}")

    try:
        with initialize_config_dir(config_dir=config_path, version_base="1.3"):
            cfg = compose(config_name="inference", overrides=overrides)
            
            # Manually inject inputs list to avoid string formatting issues in overrides
            # OmegaConf supports list assignment
            # inference_engine config merges to global root, so inputs is at top level
            cfg.inputs = inputs_str
            
            # Set PROJECT_ROOT to the expected path for RF3
            # RF3's inference.py expects: os.environ["PROJECT_ROOT"]/models/rf3/configs
            # Container should have this set up via FOUNDRY_PROJECT_ROOT or similar
            project_root = os.environ.get("PROJECT_ROOT") or os.environ.get("FOUNDRY_PROJECT_ROOT")
            
            if not project_root:
                # Use a writable default for staging RF3 configs
                project_root = "/tmp/rf3_project"
                os.environ["PROJECT_ROOT"] = project_root
            
            # Ensure the expected directory structure exists
            # RF3 looks for PROJECT_ROOT/models/rf3/configs
            rf3_models_path = Path(project_root) / "models" / "rf3"
            rf3_models_path.mkdir(parents=True, exist_ok=True)
            
            # Symlink configs if not already present (fallback to copy)
            configs_symlink = rf3_models_path / "configs"
            if not configs_symlink.exists():
                real_configs = Path(config_path)
                if real_configs.exists():
                    try:
                        os.symlink(real_configs, configs_symlink)
                    except Exception:
                        try:
                            shutil.copytree(real_configs, configs_symlink)
                        except Exception as copy_err:
                            print(f"Warning: Unable to stage RF3 configs at {configs_symlink}: {copy_err}")
            
            # Mock rootutils to prevent it from interfering
            try:
                import rootutils
                rootutils.setup_root = lambda *args, **kwargs: None
            except ImportError:
                pass

            # Run inference
            from rf3.inference import run_inference
            run_inference(cfg)
            
    except Exception as e:
        print(f"Error running RF3: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
