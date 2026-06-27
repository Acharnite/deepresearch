"""Hardware detection utilities with graceful tiered degradation.

Tiers:
  Tier 1 (required): platform.system(), platform.machine(), os.cpu_count()
  Tier 2 (recommended): psutil virtual_memory (graceful ImportError)
  Tier 3 (enhanced): nvidia-smi, rocm-smi subprocess calls
  Tier 4 (optional): torch.cuda.is_available()
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def get_hardware_info() -> dict[str, Any]:
    """Detect system hardware information with graceful degradation at every tier."""
    info: dict[str, Any] = {}

    # ── Tier 1: Platform info (stdlib, always available) ──
    info["platform"] = _platform.system()
    info["platform_version"] = _platform.version()
    info["machine"] = _platform.machine()
    info["processor"] = _platform.processor()
    info["cpu_count"] = os.cpu_count()

    # ── Tier 2: Memory info via psutil ──
    info["memory"] = _get_memory_info()

    # ── Tier 3: GPU detection ──
    info["gpus"] = _detect_gpus()

    # ── Tier 4: PyTorch CUDA check ──
    info["cuda_available"] = _check_torch_cuda()

    return info


def _get_memory_info() -> dict[str, Any] | None:
    """Return memory info via psutil, or None if psutil is not installed."""
    try:
        import psutil

        mem = psutil.virtual_memory()
        return {
            "total": mem.total,
            "available": mem.available,
            "percent_used": mem.percent,
        }
    except ImportError:
        logger.debug("psutil not installed; memory info unavailable")
        return None


def _detect_gpus() -> list[dict[str, Any]]:
    """Detect GPUs via nvidia-smi and rocm-smi. Returns a list of GPU dicts."""
    gpus: list[dict[str, Any]] = []

    # NVIDIA GPUs
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        mem_total = 0
                        try:
                            mem_total = int(parts[1])
                        except (ValueError, IndexError):
                            pass
                        gpus.append(
                            {
                                "name": parts[0],
                                "memory_total_mb": mem_total,
                                "driver_version": parts[2],
                                "backend": "nvidia",
                            }
                        )
        except Exception as e:
            logger.debug("nvidia-smi failed: %s", e)

    # AMD ROCm GPUs
    if shutil.which("rocm-smi"):
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if ":" in line and "==" not in line:
                        parts = line.split(":", 1)
                        name = parts[1].strip()
                        if name:
                            gpus.append({"name": name, "backend": "rocm"})
        except Exception as e:
            logger.debug("rocm-smi failed: %s", e)

    return gpus


def _check_torch_cuda() -> bool | None:
    """Check if torch CUDA is available. Returns None if torch not installed."""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        logger.debug("torch not installed; CUDA check skipped")
        return None
