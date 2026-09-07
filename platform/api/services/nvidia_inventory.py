"""Read-only PCI evidence for the native NVIDIA status path."""
from pathlib import Path
import re


def nvidia_gpu_present(pci_root: Path = Path('/sys/bus/pci/devices'), dev_root: Path = Path('/dev')) -> bool | None:
    """False requires a complete nonempty inventory. None preserves driver probing."""
    try:
        devices = list(pci_root.iterdir())
        if not devices:
            return None
        unknown = False
        for device in devices:
            try:
                vendor = int((device / 'vendor').read_text().strip(), 16)
                device_class = int((device / 'class').read_text().strip(), 16)
            except (OSError, ValueError):
                unknown = True
                continue
            # Display/3D and processing accelerators. NVIDIA chipset functions
            # alone do not establish that an NVIDIA GPU is installed.
            if vendor == 0x10DE and device_class >> 16 in (0x03, 0x12):
                return True
        if unknown:
            return None
        # Virtual/container device exposure can differ from PCI visibility.
        if any(re.fullmatch(r'nvidia\d+', device.name) for device in dev_root.iterdir()):
            return None
        return False
    except OSError:
        return None
