"""Read-only image selection shared by remote inventory and typed transport.

Names are workflow compatibility names, not another dependency registry. Approval
comes from retained installation releases (or Frustra's central pinned identity),
never from hashing a caller-selected file. Unconfigured legacy installations keep
their conventional image; an explicit or registered selection never falls back.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import sys

_SCRIPTS = Path(__file__).resolve().parents[4] / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.runtime_image_lifecycle import load_state, object_path, ensure_lease
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
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.sif', name):
        raise ValueError('Invalid semantic runtime image name')
    conventional = container_root / name
    flag, selector = IMAGE_SELECTORS.get(name, (None, 'BMS_RUNTIME_IMAGE_' + image_environment_key(name).removeprefix('BMS_SELECTED_IMAGE_')))
    params = params or {}
    explicit = params.get(flag) if flag else None
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


def image_environment_key(name: str) -> str:
    return 'BMS_SELECTED_IMAGE_' + re.sub(r'[^A-Za-z0-9]', '_', name).upper()


def bind_local_image_references(images: dict[str, Path], container_root: Path, *, owner: str) -> dict:
    """Pin selected canonical objects before exposing their execution environment.

    Caller owns selected-plan resolution, admission identity and durable receipt.
    Conventional legacy paths are reported, not advertised as migrated/leased.
    No lease is released on process death: retries/recovery retain their owner.
    """
    root = Path(os.environ.get('BMS_RUNTIME_IMAGE_STORE', '').strip() or container_root / '.image-store')
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('Runtime image store must be absolute without traversal')
    digests, environment, legacy = set(), {}, []
    for name, value in sorted(images.items()):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.sif', name):
            raise ValueError('Invalid semantic runtime image name')
        path = Path(value)  # Never resolve aliases before no-follow verification.
        if path.is_relative_to(root):
            digest = path.parent.name
            if path != object_path(root, digest):
                raise ValueError('Selected image is not a canonical object')
            digests.add(digest)
            environment[image_environment_key(name)] = str(path)
            if name in IMAGE_SELECTORS:
                environment[IMAGE_SELECTORS[name][1]] = str(path)
            elif name == 'dorado.sif':
                # Dorado retains its native lock/strict-SIF approval authority;
                # this is execution binding, not installation release selection.
                environment['BMS_NGS_RUNTIME_SIF'] = str(path)
        else:
            legacy.append(name)
    token, identities = ensure_lease(root, digests, owner=owner) if digests else (None, {})
    if identities:
        environment['BMS_RUNTIME_IMAGE_STORE'] = str(root)
    return dict(store_root=str(root), owner=owner, lease_token=token,
                identities=identities, environment=environment, legacy_images=legacy)
