import os
import sys
import site
import glob

def patch_haiku():
    """
    Patches the installed dm-haiku package to fix compatibility with JAX 0.4.26+.
    Specifically, it fixes the 'jax.extend.core.JaxprEqn' AttributeError by 
    redirecting it to 'jax.core.JaxprEqn'.
    """
    # Find the site-packages directory
    site_packages = site.getsitepackages()[0]
    print(f"[INFO] Searching for Haiku in: {site_packages}")

    # Locate the problematic file: haiku/_src/jaxpr_info.py
    haiku_dir = os.path.join(site_packages, "haiku")
    target_file = os.path.join(haiku_dir, "_src", "jaxpr_info.py")

    if not os.path.exists(target_file):
        print(f"[ERROR] File not found: {target_file}")
        # Try finding it via glob in case of different structure
        matches = glob.glob(os.path.join(site_packages, "haiku", "**", "jaxpr_info.py"), recursive=True)
        if matches:
            target_file = matches[0]
            print(f"[INFO] Found file at: {target_file}")
        else:
            print("[FATAL] Could not locate jaxpr_info.py in Haiku installation.")
            sys.exit(1)

    print(f"[INFO] Patching file: {target_file}")

    with open(target_file, "r") as f:
        content = f.read()

    # The problematic line usually looks like:
    # ComputeFlopsFn = Callable[[jax_core.JaxprEqn, Expression], int]
    # where jax_core is imported from jax.extend.core or jax.core

    # We will replace the import or usage.
    # A robust fix is to ensure jax_core.JaxprEqn resolves correctly.
    
    # Check if the file imports jax.extend.core
    if "from jax.extend import core as jax_core" in content:
        print("[INFO] Found 'jax.extend.core' import. Attempting to patch usage.")
        # This is tricky because we don't want to break other things.
        # But we know JaxprEqn is missing from jax.extend.core in this version.
        # So we can try to change the import to jax.core
        
        # However, let's look for the specific usage causing the crash.
        # The error is: AttributeError: module 'jax.extend.core' has no attribute 'JaxprEqn'
        
        # We can try to monkeypatch it at the top of the file? No, that's runtime.
        # We need to change the source code.
        
        # Strategy: Change "jax_core.JaxprEqn" to "jax.core.JaxprEqn" and ensure "import jax" is present.
        
        new_content = content.replace("jax_core.JaxprEqn", "jax.core.JaxprEqn")
        
        if new_content != content:
            # Ensure 'import jax' is available if we introduced 'jax.core'
            if "import jax" not in new_content:
                new_content = "import jax\n" + new_content
            
            with open(target_file, "w") as f:
                f.write(new_content)
            print("[SUCCESS] Patched 'jax_core.JaxprEqn' to 'jax.core.JaxprEqn'")
        else:
            print("[WARNING] Could not find 'jax_core.JaxprEqn' string to replace.")
            
    else:
        # Fallback: Try to replace the import itself if it's the source of the module alias
        # If it says "from jax.extend import core as jax_core", change to "from jax import core as jax_core"
        # This assumes jax.core has everything jax.extend.core had (which might be true for JaxprEqn)
        
        if "from jax.extend import core as jax_core" in content:
             new_content = content.replace("from jax.extend import core as jax_core", "from jax import core as jax_core")
             with open(target_file, "w") as f:
                f.write(new_content)
             print("[SUCCESS] Changed import to 'from jax import core as jax_core'")
        else:
             print("[WARNING] Could not find import pattern to patch.")

if __name__ == "__main__":
    patch_haiku()
