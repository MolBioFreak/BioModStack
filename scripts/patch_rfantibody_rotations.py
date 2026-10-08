"""
Monkey-patch for RFantibody's get_next_frames to handle degenerate rotation matrices.

Known issue: RosettaCommons/RFantibody#84
Some target proteins produce degenerate coordinate frames (N/Ca/C collinear or
overlapping) for certain residues during diffusion. This causes rigid_from_3_points
to return all-zero rotation matrices, which scipy.spatial.transform.Rotation.from_matrix
rejects with "Non-positive determinant (left-handed or null coordinate frame)".

IMPORTANT: scipy.spatial.transform.Rotation is a Cython extension type and CANNOT
be monkey-patched directly. Instead, this module patches the calling function
rfantibody.rfdiffusion.inference.utils.get_next_frames, which IS a regular Python
function and can be replaced.

Fix: Before passing rotation matrices to scipy, replace degenerate (det ≈ 0) 
matrices with identity. Affected residues get identity rotation transitions 
(stay at current orientation), which is the safest fallback.

Usage: Import this module AFTER rfantibody modules are importable (PYTHONPATH set).
       Call apply_patch() to replace get_next_frames.
"""

import logging
import numpy as np
from scipy.spatial.transform import Rotation as scipy_R

logger = logging.getLogger("rfantibody_patch")


def _sanitize_rotation_matrices(R, label=""):
    """Replace degenerate rotation matrices with identity.
    
    A valid rotation matrix has det = +1.
    We flag any matrix with |det| < 0.5 (covers zero, negative, near-degenerate).
    """
    dets = np.linalg.det(R)
    bad = np.abs(dets) < 0.5
    n_bad = int(np.sum(bad))
    
    if n_bad > 0:
        indices = np.where(bad)[0]
        logger.warning(
            f"[RFA-PATCH] {n_bad}/{len(R)} degenerate rotation matrices in {label}. "
            f"Replacing with identity. Indices: {indices[:10].tolist()}"
        )
        print(
            f"[RFA-PATCH] {n_bad}/{len(R)} degenerate rotation matrices in {label}. "
            f"Replacing with identity. Indices: {indices[:10].tolist()}"
        )
        R = R.copy()
        R[bad] = np.eye(3)
    
    return R


def patched_get_next_frames(xt, px0, t, diffuser, so3_type, diffusion_mask,
                            noise_scale=1., rotation_scaling=None):
    """Drop-in replacement for get_next_frames with degenerate rotation handling.
    
    This is identical to the original function except for the _sanitize calls
    before scipy_R.from_matrix.
    """
    import torch
    from rfantibody.rfdiffusion.util import rigid_from_3_points

    N_0  = px0[None,:,0,:]
    Ca_0 = px0[None,:,1,:]
    C_0  = px0[None,:,2,:]

    R_0, Ca_0 = rigid_from_3_points(N_0, Ca_0, C_0)

    N_t  = xt[None, :, 0, :]
    Ca_t = xt[None, :, 1, :]
    C_t  = xt[None, :, 2, :]

    R_t, Ca_t = rigid_from_3_points(N_t, Ca_t, C_t)

    # Convert to numpy and SANITIZE before scipy
    R_0_np = R_0.squeeze().numpy()
    R_t_np = R_t.squeeze().numpy()
    
    R_0_np = _sanitize_rotation_matrices(R_0_np, "R_0(predicted)")
    R_t_np = _sanitize_rotation_matrices(R_t_np, "R_t(current)")
    
    # Now safe to pass through scipy (normalizes to proper rotation matrices)
    R_0_clean = scipy_R.from_matrix(R_0_np).as_matrix()
    R_t_clean = scipy_R.from_matrix(R_t_np).as_matrix()

    L = R_t_clean.shape[0]
    all_rot_transitions = np.broadcast_to(np.identity(3), (L, 3, 3)).copy()

    if so3_type == "igso3":
        all_rot_transitions[~diffusion_mask] = diffuser.so3_diffuser.reverse_sample_vectorized(
            R_t_clean[~diffusion_mask], R_0_clean[~diffusion_mask], t,
            noise_level=noise_scale, mask=None, return_perturb=True,
            rotation_scaling=rotation_scaling
        )
    elif so3_type == "slerp":
        from rfantibody.rfdiffusion.inference.utils import slerp_update_vectorized
        all_rot_transitions[~diffusion_mask] = slerp_update_vectorized(
            R_t_clean[~diffusion_mask], R_0_clean[~diffusion_mask], t,
            mask=diffusion_mask[~diffusion_mask]
        )
    else:
        assert False, "so3 diffusion type %s not implemented" % so3_type

    all_rot_transitions = all_rot_transitions[:,None,:,:]

    # Apply the interpolated rotation matrices to the coordinates
    next_crds = np.einsum(
        'lrij,laj->lrai', 
        all_rot_transitions, 
        xt[:,:3,:] - Ca_t.squeeze()[:,None,...].numpy()
    ) + Ca_t.squeeze()[:,None,None,...].numpy()

    return next_crds.squeeze(1)


def apply_patch():
    """Apply the monkey-patch to rfantibody.rfdiffusion.inference.utils.get_next_frames.
    
    This patches the FUNCTION (which is a regular Python object), not the scipy
    Cython class (which is immutable).
    """
    try:
        import rfantibody.rfdiffusion.inference.utils as rfa_utils
        
        # Verify the function exists and is patchable
        original = getattr(rfa_utils, 'get_next_frames', None)
        if original is None:
            print("[RFA-PATCH] ERROR: get_next_frames not found in rfantibody.rfdiffusion.inference.utils")
            return False
        
        # Save original and replace
        rfa_utils._original_get_next_frames = original
        rfa_utils.get_next_frames = patched_get_next_frames
        
        # Verify the patch took
        if rfa_utils.get_next_frames is patched_get_next_frames:
            print("[RFA-PATCH] Successfully patched get_next_frames")
            print("[RFA-PATCH] Ref: https://github.com/RosettaCommons/RFantibody/issues/84")
            return True
        else:
            print("[RFA-PATCH] ERROR: Patch did not take effect")
            return False
            
    except ImportError as e:
        print(f"[RFA-PATCH] ERROR: Cannot import rfantibody modules: {e}")
        print("[RFA-PATCH] Make sure PYTHONPATH includes rfantibody src/ and include/")
        return False
    except Exception as e:
        print(f"[RFA-PATCH] ERROR: Unexpected failure: {e}")
        import traceback
        traceback.print_exc()
        return False
