"""
GPU Utilities — CUDA setup, memory management, dtype selection.
================================================================
Centralises all PyTorch/CUDA interactions so other modules never
import torch just to check availability.

Functions
---------
setup_gpu()          Enable TF32 + high-precision matmul on Ampere+ GPUs.
detect_compute_dtype() Return the best floating-point dtype for the device.
release_cuda()       Aggressively free GPU memory (call after model unload).
gpu_info()           Return a dict with current GPU memory statistics.
"""

import gc
import logging
import os
import platform
from typing import Optional

try:
    import torch
except ImportError:
    torch = None

try:
    import psutil
except ImportError:
    psutil = None

logger = logging.getLogger("pipeline")


def setup_gpu() -> None:
    """
    Configure CUDA for optimal throughput on Ampere+ GPUs (A100, L4, etc.).

    What it does:
    - Enables TF32 math for both matmul and cuDNN kernels
      (free ~3× throughput on Ampere with negligible accuracy loss)
    - Sets float32 matmul precision to "high" for the same effect via the
      newer PyTorch API (≥ 2.0)

    Safe to call on CPU-only machines — returns immediately if no GPU found.
    """
    if torch is None or not torch.cuda.is_available():
        return

    # TF32 is disabled by default in PyTorch; enabling it speeds up dense
    # matrix ops that dominate transformer inference.
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    try:
        # PyTorch ≥ 2.0 unified API — equivalent to the allow_tf32 flags above
        torch.set_float32_matmul_precision("high")
    except AttributeError:
        pass  # older PyTorch versions don't have this API

    props = torch.cuda.get_device_properties(0)
    logger.info(
        "GPU ready: %s | %.1f GB VRAM",
        torch.cuda.get_device_name(0),
        props.total_memory / 1024 ** 3,
    )


def detect_compute_dtype() -> Optional[object]:
    """
    Choose the best floating-point dtype for the current GPU.

    - bfloat16  → preferred on Ampere+ (sm_80+): full dynamic range, no
                  loss of magnitude vs fp32, hardware-native on A100/L4
    - float16   → fallback for older GPUs (Turing, Volta, Pascal): narrower
                  range but still ~2× faster than fp32
    - None      → no GPU; caller should use float32 (CPU default)

    Returns a torch dtype object or None.
    """
    if torch is None or not torch.cuda.is_available():
        return None

    major, _ = torch.cuda.get_device_capability(0)
    # Ampere = compute capability 8.x (sm_80+)
    return torch.bfloat16 if major >= 8 else torch.float16


def release_cuda() -> None:
    """
    Free GPU memory aggressively.

    Call this after deleting a model to reclaim VRAM before the next
    stage of the pipeline (e.g., after LLM inference, before merge phase).

    Steps:
    1. Python garbage collector — clears reference-counted tensors
    2. CUDA empty_cache — releases cached-but-unused allocator blocks
    3. ipc_collect — cleans up cross-process shared memory (optional)
    """
    gc.collect()

    if torch is None or not torch.cuda.is_available():
        return

    torch.cuda.empty_cache()

    try:
        torch.cuda.ipc_collect()
    except Exception:
        pass  # not all CUDA versions support ipc_collect


def system_info() -> dict:
    """
    Return a full hardware snapshot: GPU, CPU, RAM, and pipeline ETA estimates.

    ETA model (empirically calibrated on L4 / T4 / CPU):
      GPU Ampere+  (≥ 24 GB): ~20 s/pdf
      GPU other    (< 24 GB): ~40 s/pdf
      CPU only               : ~180 s/pdf
    These are baseline estimates; actual time scales with page count.

    Keys
    ----
    gpu           : dict from gpu_info()
    cpu_cores     : int  — logical cores
    cpu_model     : str
    ram_total_gb  : float
    ram_free_gb   : float
    ram_used_pct  : float
    platform      : str  — e.g. "Linux-5.15 x86_64"
    eta_per_pdf_s : int  — estimated seconds per PDF
    compute_tier  : str  — "gpu_fast" | "gpu_std" | "cpu"
    """
    gpu = gpu_info()

    cpu_cores = os.cpu_count() or 1
    cpu_model = "Unknown CPU"
    try:
        if psutil:
            # Try to get brand string from /proc/cpuinfo (Linux) or WMIC (Windows)
            import subprocess
            if platform.system() == "Linux":
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name"):
                            cpu_model = line.split(":", 1)[1].strip()
                            break
            elif platform.system() == "Windows":
                r = subprocess.check_output(
                    "wmic cpu get name", shell=True, text=True, timeout=3
                )
                lines = [l.strip() for l in r.splitlines() if l.strip() and l.strip() != "Name"]
                if lines:
                    cpu_model = lines[0]
    except Exception:
        pass

    ram_total_gb = ram_free_gb = ram_used_pct = 0.0
    if psutil:
        vm = psutil.virtual_memory()
        ram_total_gb  = round(vm.total  / 1024 ** 3, 1)
        ram_free_gb   = round(vm.available / 1024 ** 3, 1)
        ram_used_pct  = round(vm.percent, 1)

    # Determine compute tier and ETA
    if gpu.get("available"):
        if gpu.get("total_gb", 0) >= 20:
            compute_tier  = "gpu_fast"
            eta_per_pdf_s = 20
        else:
            compute_tier  = "gpu_std"
            eta_per_pdf_s = 40
    else:
        compute_tier  = "cpu"
        eta_per_pdf_s = 180

    return {
        "gpu":           gpu,
        "cpu_cores":     cpu_cores,
        "cpu_model":     cpu_model,
        "ram_total_gb":  ram_total_gb,
        "ram_free_gb":   ram_free_gb,
        "ram_used_pct":  ram_used_pct,
        "platform":      f"{platform.system()} {platform.release()} {platform.machine()}",
        "eta_per_pdf_s": eta_per_pdf_s,
        "compute_tier":  compute_tier,
    }


def gpu_info() -> dict:
    """
    Return a snapshot of GPU memory usage as a plain dict.

    Keys
    ----
    available      : bool  — False if no CUDA device found
    device_name    : str
    total_gb       : float — total VRAM in GiB
    free_gb        : float — currently free VRAM
    used_gb        : float — currently used VRAM
    utilization_pct: float — used / total × 100

    Used by the dashboard API endpoint to display live GPU status.
    """
    if torch is None or not torch.cuda.is_available():
        return {"available": False}

    free_bytes, total_bytes = torch.cuda.mem_get_info()
    used_bytes = total_bytes - free_bytes

    return {
        "available": True,
        "device_name": torch.cuda.get_device_name(0),
        "total_gb": round(total_bytes / 1024 ** 3, 2),
        "free_gb":  round(free_bytes  / 1024 ** 3, 2),
        "used_gb":  round(used_bytes  / 1024 ** 3, 2),
        "utilization_pct": round(used_bytes / total_bytes * 100, 1),
    }
