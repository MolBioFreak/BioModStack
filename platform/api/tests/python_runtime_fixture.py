"""Copy the real CPython closure used by relocation tests, not its OS prefix.

A system interpreter can have /usr as sys.base_prefix. Copying that directory
includes unrelated tools, package caches and devices. These Linux import-only
fixtures need the interpreter, complete stdlib/extensions and libpython only;
OS shared dependencies remain supplied by the same test host as before.
"""
from pathlib import Path
import shutil
import sys
import sysconfig


def copy_python_base(destination: Path) -> Path:
    version = f'{sys.version_info.major}.{sys.version_info.minor}'
    destination.mkdir(parents=True, exist_ok=False)
    binary = destination / 'bin' / f'python{version}'
    binary.parent.mkdir()
    shutil.copy2(Path(getattr(sys, '_base_executable', sys.executable)).resolve(), binary)

    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    installed_stdlib = destination / 'lib' / f'python{version}'
    ignored = shutil.ignore_patterns('__pycache__', 'site-packages', 'dist-packages')
    shutil.copytree(stdlib, installed_stdlib, symlinks=False, ignore=ignored)
    extension_dir = sysconfig.get_config_var('DESTSHARED')
    if extension_dir:
        extension_dir = Path(extension_dir).resolve()
        if not extension_dir.is_relative_to(stdlib):
            shutil.copytree(extension_dir, installed_stdlib / 'lib-dynload',
                            symlinks=False, ignore=ignored, dirs_exist_ok=True)

    if sysconfig.get_config_var('Py_ENABLE_SHARED'):
        library_dir = Path(sysconfig.get_config_var('LIBDIR'))
        names = {sysconfig.get_config_var(key) for key in ('LDLIBRARY', 'INSTSONAME')}
        for name in sorted(name for name in names if name):
            # Copy the soname as a physical member, not a link to host storage.
            shutil.copy2((library_dir / name).resolve(strict=True), destination / 'lib' / name)
    return binary
