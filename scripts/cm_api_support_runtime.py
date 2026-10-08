"""Read-only startup selection for the CM support interpreter, not admission.

No selection is created here. Model registries/attestations remain the scientific
execution authority. A missing optional image on Development needs no portable
interpreter; an explicitly selected but broken image is never treated as absent.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from biomodstack_runtime_profile import resolve_runtime_paths
from lib.runtime_image_lifecycle import object_path
from select_workflow_adapter_image import select_probe_image
from lib.shared_runtime_images import verify_image


def support_image(project_root: Path) -> Path | None:
    container_dir = Path(str(resolve_runtime_paths(project_root=project_root)["container_dir"]))
    root = Path(os.environ.get("BMS_RUNTIME_IMAGE_STORE", "").strip() or container_dir / ".image-store")
    path = select_probe_image(containers=container_dir)
    if path != container_dir / "protenix.sif":
        return path  # shared selector already verified retained identity/bytes

    # Preserve installed legacy support-host behavior, without registering or
    # approving either image. Frustra-only installations do not need Protenix.
    frustra = os.environ.get("BMS_FRUSTRAMPNN_SIF", "").strip()
    if frustra:
        path = Path(frustra)
        # Reuse the immutable object constraints. This is only a support import
        # probe; the Frustra registry still authenticates its pinned digest,
        # executable and checkpoint at scientific admission/execution.
        digest = path.parent.name
        if path != object_path(root, digest):
            raise ValueError("configured Frustra image is not a canonical shared object")
        verify_image(path, digest)
        return path
    for name in ("protenix.sif", "frustrampnn.sif"):
        path = container_dir / name
        if path.exists() or path.is_symlink():
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"legacy support image must be a regular file: {path}")
            return path
    return None


def portable_runtime(source_python: Path) -> Path:
    """Reject system interpreter prefixes before any recursive copy occurs."""
    runtime = source_python.resolve(strict=True).parent.parent
    if runtime in {Path(p) for p in ("/", "/usr", "/usr/local", "/opt", "/home", "/bin", "/lib", "/lib64")}:
        raise ValueError("CM support runtime requires a standalone managed Python, not a system prefix")
    if not (runtime / "bin").is_dir() or not list((runtime / "lib").glob("python3.*/os.py")):
        raise ValueError("CM support runtime requires a self-contained Python distribution")
    return runtime


if __name__ == "__main__":
    try:
        if sys.argv[1] == "image":
            selected = support_image(Path(sys.argv[2]))
            print(selected or "")
        elif sys.argv[1] == "python-runtime":
            print(portable_runtime(Path(sys.argv[2])))
        else:
            raise ValueError("unknown support-runtime operation")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        label = "workflow adapter Protenix probe image is unavailable" if sys.argv[1] == "image" else "CM support runtime is blocked"
        print(f"{label}: {exc}", file=sys.stderr)
        sys.exit(78)
