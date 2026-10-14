#!/usr/bin/env python3
"""
Wrapper for RFantibody inference that applies rotation matrix patches before running.

This wrapper:
1. Sets up sys.path so the patch module is importable
2. Imports rfantibody modules (making them available for patching)  
3. Applies the get_next_frames patch (see patch_rfantibody_rotations.py)
4. exec's the real inference script in this process (preserving patches)

The patch fixes RosettaCommons/RFantibody#84: degenerate rotation matrices
from certain target proteins crash scipy.Rotation.from_matrix.
"""
import os
import sys


def seed_resume_output(source, destination, *, prefix, num_designs, immutable=False):
    """No-clobber seed; raw task output never supplies shared writable inodes.

    `immutable` is for a publication owner, not a cautious-mode assertion. The
    native attempted range still uses the historical PDB *count*, including
    sparse names. All possible collisions in that range remain private.
    """
    import errno
    import fcntl
    import shutil
    from pathlib import Path
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return
    destination.mkdir(parents=True, exist_ok=True)
    incoming = list(source.rglob('*'))
    # Destination wins, exactly as cp -an; count the resulting top-level PDBs.
    names = {p.name for p in destination.glob(prefix + '_*.pdb') if p.is_file()}
    names.update(p.name for p in source.glob(prefix + '_*.pdb')
                 if p.is_file() and not (destination / p.name).exists())
    start = len(names)
    collisions = set()
    for index in range(start, num_designs):
        base = f'{prefix}_{index}'
        collisions.update((base + '.pdb', base + '.trb',
                           f'traj/{base}_Xt-1_traj.pdb', f'traj/{base}_pX0_traj.pdb'))
    for path in incoming:
        relative = path.relative_to(source)
        target = destination / relative
        if target.exists() or target.is_symlink():
            continue
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Only completed published structure/evidence leaves qualify. Unknown
        # cache/log/control files and orphan TRBs are execution-private.
        name = path.name
        base = name.removesuffix('_Xt-1_traj.pdb').removesuffix('_pX0_traj.pdb') if relative.parts[0] == 'traj' else path.stem
        complete = (source / (base + '.pdb')).is_file()
        share = (immutable and complete and path.suffix in ('.pdb', '.trb')
                 and relative.as_posix() not in collisions and not path.is_symlink())
        if share:
            try:
                os.link(path, target)
                continue
            except OSError as exc:
                if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK):
                    raise
        with path.open('rb') as reader, target.open('xb') as writer:
            try:
                fcntl.ioctl(writer.fileno(), 0x40049409, reader.fileno())
            except OSError:
                shutil.copyfileobj(reader, writer)


if '--bms-seed-output' in sys.argv:
    # Existing module supplies a retained mutable task directory, not a
    # published artifact. Never opt it into immutable hardlinks.
    _, source, destination, prefix, count = sys.argv[1:]
    seed_resume_output(source, destination, prefix=prefix, num_designs=int(count))
    sys.exit(0)

# Role-only export leaves all inference/rotation behavior exactly as the native
# entrypoint. Unverified debug overlays run without inventing biological roles.
if '--bms-role-export' in sys.argv:
    import runpy
    from antibody_fampnn_provenance import install_native_export
    sys.argv.remove('--bms-role-export')
    try:
        install_native_export()
    except ValueError as exc:
        print(f'[RFA-WRAPPER] Role provenance unavailable: {exc}', file=sys.stderr)
    runpy.run_path('/opt/RFantibody/scripts/rfdiffusion_inference.py', run_name='__main__')
    sys.exit(0)

# Step 1: Ensure this script's directory is on the path for the patch module
scripts_dir = os.path.dirname(os.path.abspath(__file__))
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# Step 2: Import rfantibody modules so they're available for patching
# (PYTHONPATH in the container already includes rfantibody src/ and include/)
try:
    import rfantibody.rfdiffusion.inference.utils  # noqa: F401 — needed for patch target
except ImportError as e:
    print(f"[RFA-WRAPPER] WARNING: Could not import rfantibody: {e}")
    print("[RFA-WRAPPER] Proceeding without patch — original inference script will run")

# Step 3: Apply the rotation matrix patch
from patch_rfantibody_rotations import apply_patch
patch_ok = apply_patch()

if not patch_ok:
    print("[RFA-WRAPPER] WARNING: Patch did not apply — running unpatched")

# Step 4: exec the real inference script in THIS process
# This preserves our patches (unlike subprocess/os.execvp which start fresh)
inference_script = "/opt/RFantibody/scripts/rfdiffusion_inference.py"
sys.argv[0] = inference_script

print(f"[RFA-WRAPPER] Running {inference_script} with {len(sys.argv)-1} args")
sys.stdout.flush()

with open(inference_script) as f:
    code = compile(f.read(), inference_script, 'exec')

exec(code, {
    '__name__': '__main__',
    '__file__': inference_script,
    '__builtins__': __builtins__,
})
