"""
Centralized GPU metadata and hardware limits.

Discovers GPUs at startup via nvidia-smi.  Policy values that cannot be
read from hardware (eco targets, capability flags) are kept as overridable
dicts so they can be tuned without touching nvidia-smi output.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Dict, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# POLICY OVERRIDES  –  values that are NOT discoverable from hardware
# Edit these if you want to pin specific eco targets or capability flags
# for GPUs that would otherwise get auto-computed defaults.
# ═══════════════════════════════════════════════════════════════════════════════

# gpu_index -> eco watt override.  If absent, eco = int(default * 0.75).
_ECO_OVERRIDES: Dict[int, int] = {}

# gpu_index -> capability flag overrides.
# supports_heavy:    True if the GPU has enough VRAM for AF2/RF3/RFDiffusion
# supports_protenix: True if the GPU can run Protenix workloads
_CAPABILITY_OVERRIDES: Dict[int, Dict[str, bool]] = {}

# VRAM threshold (MiB) above which a GPU is considered "heavy-capable"
_HEAVY_VRAM_THRESHOLD_MB = 20480


# ═══════════════════════════════════════════════════════════════════════════════
# RUNTIME DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════════

def discover_gpus() -> Dict[int, Dict[str, Any]]:
    """
    Query nvidia-smi at startup and build a GPU metadata dict.

    Returns a dict keyed by GPU index with per-GPU metadata:
        name, vram_mb, supports_heavy, supports_protenix,
        power: {min, default, max, eco}
    """
    query = (
        "index,name,"
        "power.min_limit,power.default_limit,power.max_limit,"
        "memory.total"
    )
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--query-gpu={query}",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        logger.warning("[GPU-META] nvidia-smi not found; GPU metadata will be empty")
        return {}
    except Exception as exc:
        logger.warning("[GPU-META] nvidia-smi query failed: %s", exc)
        return {}

    if result.returncode != 0:
        logger.warning(
            "[GPU-META] nvidia-smi returned exit code %d: %s",
            result.returncode,
            result.stderr.strip(),
        )
        return {}

    metadata: Dict[int, Dict[str, Any]] = {}

    for line in result.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue

        try:
            idx = int(parts[0])
            name = parts[1].replace("NVIDIA GeForce ", "").replace("NVIDIA ", "").strip()
            power_min = int(round(float(parts[2])))
            power_default = int(round(float(parts[3])))
            power_max = int(round(float(parts[4])))
            vram_mb = int(round(float(parts[5])))
        except (TypeError, ValueError) as exc:
            logger.warning("[GPU-META] Failed to parse line %r: %s", line, exc)
            continue

        # Eco: use override if present, otherwise 75% of default, clamped
        eco = _ECO_OVERRIDES.get(idx, int(power_default * 0.75))
        eco = max(power_min, min(power_max, eco))

        # Capability flags: auto-detect from VRAM, allow overrides
        overrides = _CAPABILITY_OVERRIDES.get(idx, {})
        supports_heavy = overrides.get("supports_heavy", vram_mb >= _HEAVY_VRAM_THRESHOLD_MB)
        supports_protenix = overrides.get("supports_protenix", True)

        metadata[idx] = {
            "name": name,
            "vram_mb": vram_mb,
            "supports_heavy": supports_heavy,
            "supports_protenix": supports_protenix,
            "power": {
                "min": power_min,
                "default": power_default,
                "max": power_max,
                "eco": eco,
            },
        }

    if metadata:
        logger.info(
            "[GPU-META] Discovered %d GPU(s): %s",
            len(metadata),
            ", ".join(f"GPU {i}: {m['name']} ({m['vram_mb']} MiB)" for i, m in sorted(metadata.items())),
        )
    else:
        logger.warning("[GPU-META] No GPUs discovered from nvidia-smi")

    return metadata


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL EXPORTS  –  same shape as before, built dynamically
# ═══════════════════════════════════════════════════════════════════════════════

GPU_METADATA: Dict[int, Dict[str, Any]] = discover_gpus()

GPU_CAPABILITIES: Dict[int, Dict[str, Any]] = {
    idx: {
        "name": meta["name"],
        "vram_mb": meta["vram_mb"],
        "supports_heavy": meta["supports_heavy"],
        "supports_protenix": meta.get("supports_protenix", True),
    }
    for idx, meta in GPU_METADATA.items()
}

HARDWARE_LIMITS: Dict[int, Dict[str, Any]] = {
    idx: {
        "min": meta["power"]["min"],
        "default": meta["power"]["default"],
        "max": meta["power"]["max"],
        "eco": meta["power"]["eco"],
        "name": meta["name"],
    }
    for idx, meta in GPU_METADATA.items()
}
