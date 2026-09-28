"""Fixture riêng cho test bổ sung trong tests/unit/."""

from __future__ import annotations

import subprocess

import pytest

from docker_helpers import IMAGE_TAG


@pytest.fixture(scope="session")
def docker_image(repo_root) -> str:
    """Build image production một lần cho cả phiên test (layer cache làm lần sau rất nhanh)."""
    result = subprocess.run(
        ["docker", "build", "-q", "-t", IMAGE_TAG, "."],
        cwd=repo_root, capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return IMAGE_TAG
