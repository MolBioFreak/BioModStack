#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from biomodstack_services import (  # noqa: E402
    BROWSER_LAUNCH_SURFACE,
    ELECTRON_LAUNCH_SURFACE,
    NONE_LAUNCH_SURFACE,
    SUPPORTED_LAUNCH_SURFACES,
    ServiceManagerError,
    load_launch_preferences,
    runtime_descriptor,
    start_all,
)


def resolve_surface_choice(explicit_surface: str | None, launch_preferences: dict[str, object]) -> str:
    if explicit_surface:
        return explicit_surface
    surface = str(launch_preferences.get("default_surface") or BROWSER_LAUNCH_SURFACE).strip().lower()
    if surface not in SUPPORTED_LAUNCH_SURFACES:
        return BROWSER_LAUNCH_SURFACE
    return surface


def should_open_browser(surface: str, explicit_surface: str | None, launch_preferences: dict[str, object]) -> bool:
    if surface in {NONE_LAUNCH_SURFACE, ELECTRON_LAUNCH_SURFACE}:
        return False
    if explicit_surface == BROWSER_LAUNCH_SURFACE:
        return True
    return bool(launch_preferences.get("auto_open_hosted_web_on_start", True))


def launch_ui(runtime_mode: str | None = None, surface: str | None = None) -> dict[str, object]:
    prefs = load_launch_preferences()
    chosen_surface = resolve_surface_choice(surface, prefs)

    if chosen_surface == ELECTRON_LAUNCH_SURFACE:
        if surface == ELECTRON_LAUNCH_SURFACE:
            raise ServiceManagerError(
                "Electron launch surface requested, but no Electron shell is installed yet. Implement Phase 2 before enabling this path."
            )
        chosen_surface = BROWSER_LAUNCH_SURFACE

    start_all(runtime_mode=runtime_mode)
    descriptor = runtime_descriptor(runtime_mode=runtime_mode)
    prefs = descriptor["launch_preferences"]
    if should_open_browser(chosen_surface, surface, prefs):
        webbrowser.open_new_tab(str(descriptor["browser_url"]))
    return descriptor


def main() -> int:
    parser = argparse.ArgumentParser(description="Start BioModStack and optionally raise a UI surface")
    parser.add_argument("--runtime", choices=["dev", "container"], default=None)
    parser.add_argument("--surface", choices=list(SUPPORTED_LAUNCH_SURFACES))
    args = parser.parse_args()

    try:
        launch_ui(runtime_mode=args.runtime, surface=args.surface)
    except (ServiceManagerError, FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
