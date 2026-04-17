"""Environment detection: OS, network, disk, Docker."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class EnvironmentInfo:
    os_type: str           # "native_linux" | "wsl" | "macos" | "unknown"
    network_zone: str      # "external" | "internal"
    available_disk_gb: float
    total_ram_gb: float
    docker_available: bool
    recommended_tier: str  # "local" | "lite" | "verified" | "full" | "multi" (auto-pick never returns "local")

    def summary(self) -> str:
        return (
            f"OS: {self.os_type} | Network: {self.network_zone} | "
            f"Disk: {self.available_disk_gb:.1f}GB | RAM: {self.total_ram_gb:.1f}GB | "
            f"Docker: {'Yes' if self.docker_available else 'No'} | "
            f"Tier: {self.recommended_tier}"
        )


def _detect_os() -> str:
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        try:
            with open("/proc/version", "r") as f:
                if "microsoft" in f.read().lower():
                    return "wsl"
        except FileNotFoundError:
            pass
        return "native_linux"
    return "unknown"


def _detect_network(timeout: int = 5) -> str:
    try:
        subprocess.run(
            ["curl", "-s", "--connect-timeout", str(timeout),
             "https://api.anthropic.com"],
            capture_output=True, timeout=timeout + 2,
        )
        return "external"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "internal"


def _get_free_disk_gb(path: str = ".") -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)


def _get_ram_gb() -> float:
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 ** 2)
    except FileNotFoundError:
        pass
    # Fallback
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        return 0.0


def _check_docker() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def detect_environment(path: str = ".") -> EnvironmentInfo:
    os_type = _detect_os()
    network = _detect_network()
    disk = _get_free_disk_gb(path)
    ram = _get_ram_gb()
    docker = _check_docker()

    if disk >= 120:
        tier = "full"
    elif disk >= 30:
        tier = "verified"
    else:
        tier = "lite"

    return EnvironmentInfo(
        os_type=os_type,
        network_zone=network,
        available_disk_gb=disk,
        total_ram_gb=ram,
        docker_available=docker,
        recommended_tier=tier,
    )


if __name__ == "__main__":
    env = detect_environment()
    print(env.summary())
