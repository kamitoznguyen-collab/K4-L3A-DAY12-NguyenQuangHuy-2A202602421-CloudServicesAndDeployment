"""Test bổ sung cho CP2 — Dockerfile, compose, nginx.

Phần static chạy ở mọi nơi. Phần mark `docker` dùng Docker thật và tự bỏ qua
nếu máy không có Docker daemon.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest
import yaml

from docker_helpers import needs_docker

NGINX_IMAGE_RE = re.compile(r"image:\s*(nginx:\S+)")


@pytest.fixture(scope="module")
def dockerfile(repo_root) -> str:
    return (repo_root / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose(repo_root) -> dict:
    return yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))


def _stage(dockerfile: str, name: str) -> str:
    """Nội dung một stage, từ dòng FROM ... AS name tới FROM kế tiếp."""
    match = re.search(
        rf"^FROM\s+\S+\s+AS\s+{name}\s*$(.*?)(?=^FROM\s|\Z)",
        dockerfile,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    assert match, f"không tìm thấy stage {name!r}"
    return match.group(1)


class TestDockerfile:
    def test_cmd_dung_exec_de_uvicorn_la_pid_1(self, dockerfile):
        """`sh -c "uvicorn ..."` không có exec → sh là PID 1, nuốt mất SIGTERM."""
        cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD")][-1]
        assert "exec uvicorn" in cmd

    def test_doc_cong_tu_bien_port(self, dockerfile):
        cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD")][-1]
        assert "${PORT}" in cmd

    def test_runtime_khong_cai_them_gi(self, dockerfile):
        """Stage runtime chỉ copy kết quả; pip/apt chạy ở đây là mất ý nghĩa multi-stage."""
        runtime = _stage(dockerfile, "runtime")
        assert "pip install" not in runtime
        assert "apt-get install" not in runtime
        assert "COPY --from=builder" in runtime

    def test_runtime_khong_copy_ca_repo(self, dockerfile):
        runtime = _stage(dockerfile, "runtime")
        assert not re.search(r"^COPY\s+(--\S+\s+)*\.\s", runtime, re.MULTILINE)

    def test_log_khong_bi_buffer(self, dockerfile):
        """Không có PYTHONUNBUFFERED, log nằm trong buffer và mất khi container chết."""
        assert "PYTHONUNBUFFERED=1" in dockerfile


class TestDependencies:
    def test_image_khong_cai_thu_vien_test(self, repo_root):
        prod = (repo_root / "requirements-prod.txt").read_text(encoding="utf-8").lower()
        for lib in ("pytest", "fakeredis", "httpx", "ruff"):
            assert lib not in prod, f"{lib} là thư viện test, không vào image production"

    def test_requirements_bao_gom_ban_prod(self, repo_root):
        """Grader cài requirements.txt — nó phải kéo theo đủ runtime."""
        text = (repo_root / "requirements.txt").read_text(encoding="utf-8")
        assert "-r requirements-prod.txt" in text


class TestDockerignore:
    @pytest.mark.parametrize("entry", [".env", ".env.*", "*.pem", "*.key", "tests"])
    def test_loai_tru(self, repo_root, entry):
        lines = (repo_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        assert entry in lines


class TestCompose:
    def test_agent_khong_publish_cong(self, compose):
        """Publish 8000 ở agent thì không scale được (trùng cổng); nginx là cửa vào."""
        assert "ports" not in compose["services"]["agent"]

    def test_redis_chi_mo_cho_localhost(self, compose):
        for mapping in compose["services"]["redis"].get("ports", []):
            assert str(mapping).startswith("127.0.0.1:"), "Redis không có mật khẩu — đừng mở ra LAN"

    def test_chay_nhieu_ban_agent(self, compose):
        assert compose["services"]["agent"]["deploy"]["replicas"] >= 2

    def test_cho_du_thoi_gian_graceful_shutdown(self, compose, dockerfile):
        """stop_grace_period phải dài hơn thời gian uvicorn đợi request dở."""
        grace = int(compose["services"]["agent"]["stop_grace_period"].rstrip("s"))
        uvicorn_timeout = int(re.search(r"--timeout-graceful-shutdown (\d+)", dockerfile).group(1))
        assert grace > uvicorn_timeout

    def test_tunnel_chi_bat_khi_chon_profile_public(self, compose):
        """`docker compose up` thường không được tự động mở service ra Internet."""
        assert compose["services"]["tunnel"]["profiles"] == ["public"]

    def test_tunnel_image_duoc_ghim_phien_ban(self, compose):
        image = compose["services"]["tunnel"]["image"]
        assert ":" in image and not image.endswith(":latest")

    def test_nginx_la_cua_vao_duy_nhat(self, compose):
        assert compose["services"]["nginx"]["ports"] == ["${HOST_PORT:-8000}:80"]


@pytest.mark.docker
@needs_docker
class TestDockerThat:
    def test_compose_hop_le(self, repo_root):
        env = {**os.environ, "AGENT_API_KEY": "compose-config-test"}
        result = subprocess.run(
            ["docker", "compose", "--profile", "public", "config", "-q"],
            cwd=repo_root, capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 0, result.stderr

    def test_nginx_config_hop_le(self, repo_root):
        text = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
        nginx_image = NGINX_IMAGE_RE.search(text).group(1)
        conf = repo_root / "nginx" / "nginx.conf"
        result = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{conf}:/etc/nginx/nginx.conf:ro",
             nginx_image, "nginx", "-t"],
            capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stderr

    def test_chay_bang_user_thuong(self, docker_image):
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "id", docker_image],
            capture_output=True, text=True, timeout=60,
        )
        assert result.stdout.startswith("uid=10001(app)")

    def test_image_khong_chua_env_va_test(self, docker_image):
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "ls", docker_image, "-A", "/app"],
            capture_output=True, text=True, timeout=60,
        )
        assert sorted(result.stdout.split()) == ["app", "utils"]

    def test_thieu_api_key_thi_container_chet_ngay(self, docker_image):
        """Fail fast: không có AGENT_API_KEY → thoát với mã lỗi, không âm thầm chạy."""
        result = subprocess.run(
            ["docker", "run", "--rm", docker_image],
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "agent_api_key" in output.lower()
        assert "Application startup failed" in output
