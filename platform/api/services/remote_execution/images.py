"""Read-only image selection shared by remote inventory and typed transport.

Names are workflow compatibility names, not another dependency registry. Approval
comes from retained installation releases (or Frustra's central pinned identity),
never from hashing a caller-selected file. Unconfigured legacy installations keep
their conventional image; an explicit or registered selection never falls back.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

_SCRIPTS = Path(__file__).resolve().parents[4] / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.runtime_image_lifecycle import load_state, object_path
from lib.shared_runtime_images import verify_image

# Do NOT map experimental confornets.sif to the distinct canonical CM build.
IMAGE_SELECTORS = {
    'protenix.sif': ('protenix_container_path', 'BMS_PROTENIX_CONTAINER_PATH'),
    'confornets-canonical.sif': ('cm_confornets_container_path', 'BMS_CM_CONFORNETS_CONTAINER_PATH'),
    'frustrampnn.sif': ('frustrampnn_container_path', 'BMS_FRUSTRAMPNN_SIF'),
}


def resolve_image(name: str, container_root: Path, params: dict | None = None) -> Path:
    """Typed argument > installation selector > current release > legacy name.

    CAS validation intentionally runs before resolving symlinks. Legacy names are
    compatible only when no managed selection exists; explicitly selected paths
    must be retained approved objects, not arbitrary managed-directory files.
    """
    conventional = container_root / name
    if name not in IMAGE_SELECTORS:
        return conventional
    flag, selector = IMAGE_SELECTORS[name]
    params = params or {}
    explicit = params.get(flag)
    configured = explicit if explicit is not None else os.environ.get(selector, '').strip()
    if explicit is not None and (not isinstance(explicit, str) or not explicit.strip()):
        raise ValueError(f'Invalid explicit image selector: {flag}')
    root = Path(os.environ.get('BMS_RUNTIME_IMAGE_STORE', '').strip() or container_root / '.image-store')
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Runtime image store must be absolute without traversal')
    if name == 'frustrampnn.sif':
        from services.frustrampnn import runtime
        identity = runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
        # Central scientific digest remains authoritative, independent of release
        # membership. The central reader also enforces configured path policy.
        path = Path(runtime.validate_configured_container_path(configured or identity.configured_sif_path))
        with runtime.open_verified_container(path, identity.sif_sha256):
            pass
        return path
    state = load_state(root)
    if (configured == str(conventional) and not os.environ.get(selector, '').strip()
            and not any(selector in release['images'] for release in state['releases'].values())):
        return conventional
    if configured:
        candidates = [release['images'][selector]
                      for release in state['releases'].values()
                      if selector in release['images']
                      and release['images'][selector]['path'] == configured]
        if not candidates:
            raise ValueError(f'Configured image is not a retained managed reference: {selector}')
        image = candidates[0]
    else:
        lane = (os.environ.get('BMS_RUNTIME_IMAGE_LANE')
                or os.environ.get('BMS_WORKFLOW_ADAPTER_LANE') or 'production')
        if lane not in {'development', 'production'}:
            raise ValueError('Invalid runtime image lane')
        release = state['releases'].get(state['current'].get(lane), {})
        image = release.get('images', {}).get(selector)
        if image is None:
            return conventional
    path = object_path(root, image['sha256'])
    if str(path) != image['path']:
        raise ValueError('Managed image path differs from shared store')
    verify_image(path, image['sha256'])
    return path
