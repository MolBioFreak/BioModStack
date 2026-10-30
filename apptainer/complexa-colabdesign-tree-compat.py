"""Restore the jax.tree_* aliases that this ColabDesign fork still calls.

jax 0.4.31 removed the top-level aliases (tree_map, tree_flatten, tree_unflatten,
tree_leaves). The pinned upstream checkout must stay byte-clean for the image
packaging check, so the definition copies community_models/colabdesign to a
build-owned path and runs this patch against that copy before installing it.
"""
import sys
from pathlib import Path

MARKER = "import os,jax\n"
SHIM = (
    "import os,jax\n"
    "# jax >= 0.4.31 removed the top-level tree_* aliases this fork still calls.\n"
    "from jax import tree_util as _bms_tree_util\n"
    "for _bms_name in (\"tree_map\", \"tree_flatten\", \"tree_unflatten\", \"tree_leaves\"):\n"
    "    if not hasattr(jax, _bms_name):\n"
    "        setattr(jax, _bms_name, getattr(_bms_tree_util, _bms_name))\n"
)


def main() -> None:
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if MARKER not in text:
        raise SystemExit(f"colabdesign __init__ lacks the expected import: {path}")
    if "_bms_tree_util" in text:
        return
    path.write_text(text.replace(MARKER, SHIM, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
