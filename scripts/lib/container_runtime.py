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
