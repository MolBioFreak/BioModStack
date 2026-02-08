"""
Centralized GPU metadata and hardware limits.
"""

from __future__ import annotations

from typing import Dict, Any

GPU_METADATA: Dict[int, Dict[str, Any]] = {
    0: {
        "name": "RTX 5090",
        "vram_mb": 32607,
        "supports_heavy": True,
        "supports_protenix": True,
        "power": {"min": 400, "default": 575, "max": 600, "eco": 500},
    },
    1: {
        "name": "RTX 5060 Ti",
        "vram_mb": 16311,
        "supports_heavy": False,
        "supports_protenix": True,
        "power": {"min": 150, "default": 180, "max": 200, "eco": 165},
    },
    2: {
        "name": "RTX 3090",
        "vram_mb": 24576,
        "supports_heavy": True,
        "supports_protenix": True,
        "power": {"min": 100, "default": 370, "max": 380, "eco": 300},
    },
    3: {
        "name": "RTX 3090",
        "vram_mb": 24576,
        "supports_heavy": True,
        "supports_protenix": True,
        "power": {"min": 100, "default": 390, "max": 480, "eco": 300},
    },
}


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
