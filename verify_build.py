import torch
import dgl
import sys

print(f"Python Version: {sys.version}")
print(f"PyTorch Version: {torch.__version__}")
print(f"CUDA Available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA Version: {torch.version.cuda}")
    print(f"Device Name: {torch.cuda.get_device_name(0)}")
    print(f"Device Capability: {torch.cuda.get_device_capability(0)}")

print(f"DGL Version: {dgl.__version__}")
try:
    import e3nn
    print(f"e3nn Version: {e3nn.__version__}")
except ImportError:
    print("e3nn not found")

print("Verification Complete")
