"""Tiện ích dùng chung cho các test cần Docker thật."""

from __future__ import annotations

import subprocess

import pytest

IMAGE_TAG = "day12-agent:unit-test"


def docker_available() -> bool:
    """Docker daemon có đang chạy không? Không có thì các test Docker tự bỏ qua."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


needs_docker = pytest.mark.skipif(not docker_available(), reason="Docker chưa chạy")
