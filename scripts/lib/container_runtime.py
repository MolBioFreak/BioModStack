"""Select the explicitly installed scientific launcher, not a provider name.

The remote worker exports its qualified backend in its authenticated execution
context. Native callers and threaded adapters consume the same process contract.
An unset selection preserves their existing Apptainer executable and argv.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil


def container_executable(preferred: Path | str | None = None) -> str | None:
    selected = os.environ.get('BMS_CONTAINER_EXECUTABLE')
    if selected:
        if (os.environ.get('BMS_CONTAINER_BACKEND') != 'udocker'
                or not Path(selected).is_absolute() or '\x00' in selected
                or Path(selected).name != 'bms-container'):
            raise ValueError('invalid selected scientific container launcher')
        return selected
    return os.fspath(preferred) if preferred is not None else shutil.which('apptainer')


def nextflow_container_config(container_runtime=None, image_paths=None, shell_options=('-ue',)):
    """Use Nextflow's native interpreter wrapping through the private CLI bridge.

    The managed bms-nextflow entrypoint supplies the private PATH and explicitly
    disables the builder's PID-namespace option. No script/shebang is rewritten.
    Nested OCI names reuse the validated offline library, never a second cache.
    """
    runtime = container_runtime if container_runtime is not None else {
        'backend': os.environ.get('BMS_CONTAINER_BACKEND', 'apptainer'),
        'executable': os.environ.get('BMS_CONTAINER_EXECUTABLE', ''),
    }
    if runtime.get('backend', 'apptainer') != 'udocker':
        return ''
    executable = runtime.get('executable', '')
    if not executable or not Path(executable).is_absolute() or Path(executable).name != 'bms-container':
        raise ValueError('udocker Nextflow requires an absolute trusted bms-container executable')
    def groovy(value):
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n') + "'"
    lines = [
        'apptainer.enabled = false', 'docker.enabled = false',
        'singularity.enabled = true', 'singularity.autoMounts = true',
        'singularity.ociAutoPull = false',
        # Preserve the inherited task environment, as the previous exec boundary
        # did. Values are expanded by Nextflow at task launch, not serialized here.
        "singularity.envWhitelist = System.getenv().keySet().join(',')",
        "process.shell = [" + ', '.join(groovy(s) for s in ('/bin/bash', *shell_options)) + ']'
    ]
    if image_paths:
        directories = set()
        for uri, name in image_paths.items():
            path = Path(name)
            remote = uri.removeprefix('docker://')
            # Pinned Nextflow SingularityCache.simpleName, for this inventory's
            # Docker URIs. Reject unsupported formats instead of guessing aliases.
            expected = remote.replace(':', '-').replace('/', '-') + '.img'
            if not path.is_absolute() or path.name != expected:
                raise ValueError('offline image mapping differs from pinned Nextflow cache naming')
            directories.add(str(path.parent))
        if len(directories) != 1:
            raise ValueError('offline image inventory requires one existing library')
        library = groovy(directories.pop())
        lines += ['singularity.libraryDir = ' + library, 'singularity.cacheDir = ' + library]
    return '\n'.join([*lines, ''])
