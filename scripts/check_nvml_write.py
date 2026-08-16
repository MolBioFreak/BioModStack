import argparse
import os
import sys

import pynvml


def _default_gpu_index() -> int:
    raw_value = os.environ.get("BMS_NVML_TEST_GPU", "0")
    try:
        return max(0, int(raw_value))
    except ValueError:
        return 0


def test_nvml_write(idx: int | None = None):
    if idx is None:
        idx = _default_gpu_index()
    try:
        pynvml.nvmlInit()
        print("NVML Initialized")

        handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
        name = pynvml.nvmlDeviceGetName(handle)
        print(f"Target: GPU {idx} ({name})")

        # Get current limit
        current_limit = pynvml.nvmlDeviceGetPowerManagementLimit(handle)
        print(f"Current Limit: {current_limit} mW")

        # Try to set it to the same value (safe test)
        # or slightly different if needed, but same value usually triggers the perm check too
        print(f"Attempting to set limit to {current_limit} mW...")
        pynvml.nvmlDeviceSetPowerManagementLimit(handle, current_limit)
        print("SUCCESS: Set power limit worked!")
        return 0

    except pynvml.NVMLError as e:
        print(f"FAILED: NVML Error: {e}")
        return 1
    except Exception as e:
        print(f"FAILED: Exception: {e}")
        return 1
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check whether NVML power-limit writes are permitted")
    parser.add_argument(
        "--gpu-id",
        type=int,
        default=_default_gpu_index(),
        help="GPU index to test (default: BMS_NVML_TEST_GPU or 0)",
    )
    args = parser.parse_args()
    sys.exit(test_nvml_write(args.gpu_id))
