"""Select the existing managed image reference without adding execution gates."""
import os

IMAGE = 'msa_engines.sif'


def select_runtime():
    explicit = os.environ.get('BMS_FAMILY_MSA_SIF', '').strip()
    if explicit:
        return explicit, {'selection': 'explicit'}
    try:
        from paths import get_container_dir
        from services.remote_execution.images import image_reference
        path, digest = image_reference(IMAGE, get_container_dir())
        if path.is_file():
            return str(path), {'selection': 'managed' if digest else 'conventional',
                               'release_sha256': digest}
        return '', {'selection': 'host', 'image_unavailable': str(path)}
    except (ValueError, OSError, KeyError) as exc:
        # Resolution is evidence, not an extra admission condition. Preserve
        # the existing host execution path when managed discovery fails.
        return '', {'selection': 'host', 'image_resolution_error': str(exc)}
