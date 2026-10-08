"""PEP 517 build of hash-authenticated upstream source, with a reviewed patch.

No generated wheel hash is asserted here. uv locks this local source package;
upstream.json independently pins the authentic PyPI sdist. Build tooling is
pinned in pyproject.toml; OS/compiler/libcurl identities belong to target receipts.
"""
from contextlib import contextmanager
import hashlib
from importlib.metadata import version as installed_version
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
VERSION = "0.23.3+bms1"


@contextmanager
def source():
    # --inexact retains extra tools but cannot prevent a project dependency
    # from replacing one. Verify the tools actually executing this build.
    expected_tools = {"Cython": "3.1.3", "setuptools": "80.9.0", "wheel": "0.45.1"}
    build_tools = {name: installed_version(name) for name in expected_tools}
    if build_tools != expected_tools:
        raise RuntimeError(f"pysam build tools differ from authenticated lock: {build_tools}")
    provenance = json.loads((ROOT / "upstream.json").read_text())
    # An offline target can stage exactly the same authenticated sdist.
    staged = os.environ.get("BMS_PYSAM_SDIST")
    if staged:
        data = Path(staged).read_bytes()
    else:
        with urllib.request.urlopen(provenance["url"], timeout=120) as response:
            data = response.read()
    if (len(data) != provenance["size"] or
            hashlib.sha256(data).hexdigest() != provenance["sha256"]):
        raise RuntimeError("pysam upstream source integrity mismatch")
    with tempfile.TemporaryDirectory(prefix="bms-pysam-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                target = Path(tmp) / member.name
                if (not target.resolve().is_relative_to(Path(tmp).resolve())
                        or member.issym() or member.islnk() or member.isdev()):
                    raise RuntimeError("unsafe upstream archive member")
            archive.extractall(tmp)
        root = Path(tmp) / "pysam-0.23.3"
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(ROOT / "no-save-index.patch")],
                       cwd=root, check=True)
        version = root / "pysam/version.py"
        text = version.read_text()
        if text.count('__version__ = "0.23.3"') != 1:
            raise RuntimeError("upstream version marker changed")
        version.write_text(text.replace('__version__ = "0.23.3"', f'__version__ = "{VERSION}"'))
        # Cython must regenerate C from patched pyx/pxd; never compile stale C.
        for pyx in (root / "pysam").glob("*.pyx"):
            pyx.with_suffix(".c").unlink(missing_ok=True)
        setup = root / "setup.py"
        text = setup.read_text()
        old = "            if run_configure(env_options):\n                return env_options\n"
        if text.count(old) != 1:
            raise RuntimeError("upstream configure contract changed")
        setup.write_text(text.replace(old, old + '            raise RuntimeError("required HTSlib configuration failed")\n'))
        build_identity = {
            "binding_api": 1, "upstream_sha256": provenance["sha256"],
            "backend_sha256": hashlib.sha256((ROOT / "bms_build.py").read_bytes()).hexdigest(),
            "patch_sha256": hashlib.sha256((ROOT / "no-save-index.patch").read_bytes()).hexdigest(),
            "build_requirements_sha256": hashlib.sha256((ROOT / "build-requirements.lock").read_bytes()).hexdigest(),
            "required_transport": "builtin-htslib-libcurl", "build_tools": build_tools,
        }
        (root / "pysam/bms_native_build.py").write_text("IDENTITY = " + repr(build_identity) + "\n")
        old_cwd = Path.cwd()
        old_options = os.environ.get("HTSLIB_CONFIGURE_OPTIONS")
        os.environ["HTSLIB_CONFIGURE_OPTIONS"] = "--enable-libcurl"
        try:
            os.chdir(root)
            yield root
        finally:
            os.chdir(old_cwd)
            if old_options is None:
                os.environ.pop("HTSLIB_CONFIGURE_OPTIONS", None)
            else:
                os.environ["HTSLIB_CONFIGURE_OPTIONS"] = old_options


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    directory = str(Path(wheel_directory).resolve())
    with source() as root:
        from setuptools import build_meta
        wheel = build_meta.build_wheel(directory, config_settings)
        config = (root / "htslib/config.h").read_text()
        if "#define HAVE_LIBCURL 1" not in config:
            (Path(directory) / wheel).unlink(missing_ok=True)
            raise RuntimeError("pysam build lacks required HTTP transport")
        return wheel


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    # Static package metadata; resolving never runs configure/compiler/imports.
    dist = Path(metadata_directory) / f"pysam-{VERSION}.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: pysam\nVersion: " + VERSION
        + "\nRequires-Python: >=3.10\n\n")
    return dist.name


def get_requires_for_build_wheel(config_settings=None):
    return []
