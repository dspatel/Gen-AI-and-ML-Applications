#pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

import torch
import time

print("Torch version:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())

if not torch.cuda.is_available():
    raise RuntimeError("CUDA is NOT available")

print("GPU:", torch.cuda.get_device_name(0))

a = torch.randn(4096, 4096, device="cuda")
b = torch.randn(4096, 4096, device="cuda")

# Warm-up
_ = a @ b
torch.cuda.synchronize()

t0 = time.time()
c = a @ b
torch.cuda.synchronize()

print("Matmul time (s):", round(time.time() - t0, 4))
print("Result mean:", c.mean().item())
