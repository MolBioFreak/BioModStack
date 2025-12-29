import pynvml
import sys

def test_nvml_write():
    try:
        pynvml.nvmlInit()
        print("NVML Initialized")
        
        # Target GPU 2 (RTX 3090)
        idx = 2
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
        except:
            pass

if __name__ == "__main__":
    sys.exit(test_nvml_write())
