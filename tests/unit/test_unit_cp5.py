"""Test bổ sung cho CP5 — giao diện web công khai và script vận hành."""

from __future__ import annotations

import importlib.util
import re
from html.parser import HTMLParser

import pytest


def load_script(repo_root, name: str):
    """Import một file trong scripts/ như module (thư mục này không phải package)."""
    spec = importlib.util.spec_from_file_location(name, repo_root / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict] = []
        self.inline_handlers: list[str] = []
        self._in_script = False
        self.inline_code = ""

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.inline_handlers += [name for name in attributes if name.startswith("on")]
        if tag == "script":
            self.scripts.append(attributes)
            self._in_script = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.inline_code += data.strip()


class TestGiaoDienWeb:
    def test_trang_chu_la_html_khong_can_key(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Day 12 Agent" in response.text

    def test_trang_chu_co_csp_chat(self, client):
        csp = client.get("/").headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp

    def test_html_khong_co_script_inline(self, client):
        """CSP chặn script inline — có script inline nghĩa là trang tự hỏng."""
        parser = ScriptCollector()
        parser.feed(client.get("/").text)
        assert parser.scripts, "trang phải nạp app.js"
        assert all(s.get("src", "").startswith("/static/") for s in parser.scripts)
        assert parser.inline_code == ""
        assert parser.inline_handlers == [], "onclick=... là script inline"

    def test_html_khong_nhung_api_key(self, client, api_key):
        assert api_key not in client.get("/").text

    @pytest.mark.parametrize(
        ("path", "content_type"),
        [("/static/app.js", "javascript"), ("/static/app.css", "text/css")],
    )
    def test_file_tinh(self, client, path, content_type):
        response = client.get(path)
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]

    @pytest.mark.parametrize(
        "path",
        ["/static/..%2fconfig.py", "/static/%2e%2e/config.py", "/static/../../.env"],
    )
    def test_khong_doc_duoc_file_ngoai_static(self, client, path):
        response = client.get(path)
        assert response.status_code == 404
        assert "agent_api_key" not in response.text.lower()

    def test_docs_khong_bi_csp_lam_hong(self, client):
        """Swagger UI tải từ CDN — gắn CSP 'self' vào /docs là trang trắng."""
        assert "Content-Security-Policy" not in client.get("/docs").headers

    def test_trang_chu_khong_nam_trong_openapi(self, client):
        assert "/" not in client.get("/openapi.json").json()["paths"]

    def test_js_khong_render_html_tu_du_lieu(self, repo_root):
        """Câu trả lời/câu hỏi phải gán bằng textContent — innerHTML là XSS."""
        source = (repo_root / "app" / "static" / "app.js").read_text(encoding="utf-8")
        assert not re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", source)

    def test_js_chi_goi_cung_origin(self, repo_root):
        source = (repo_root / "app" / "static" / "app.js").read_text(encoding="utf-8")
        assert not re.search(r"fetch\(\s*[\"'`]https?://", source)

    def test_regex_user_id_js_khop_voi_server(self, repo_root):
        from app.auth import USER_ID_PATTERN

        source = (repo_root / "app" / "static" / "app.js").read_text(encoding="utf-8")
        js_pattern = re.search(r"USER_ID_PATTERN = /(.+)/;", source).group(1)
        assert js_pattern == USER_ID_PATTERN.pattern


class TestSmokeScript:
    @pytest.fixture
    def smoke_module(self, repo_root):
        return load_script(repo_root, "smoke_test")

    def test_chay_xanh_tren_app_that(self, smoke_module, client_real_store, api_key, capsys):
        smoke = smoke_module.Smoke("http://testserver", api_key, timeout=5)
        smoke.http = client_real_store  # TestClient cũng là một httpx.Client
        assert smoke.run() == 0, capsys.readouterr().out
        out = capsys.readouterr().out
        assert "FAIL" not in out
        assert api_key not in out

    def test_bao_do_khi_sai_key(self, smoke_module, client_real_store, capsys):
        smoke = smoke_module.Smoke("http://testserver", "khoa-sai", timeout=5)
        smoke.http = client_real_store
        assert smoke.run() == 1

    def test_khong_co_key_thi_chi_chay_phan_cong_khai(self, smoke_module, client_real_store, capsys):
        smoke = smoke_module.Smoke("http://testserver", None, timeout=5)
        smoke.http = client_real_store
        assert smoke.run() == 0
        assert "SKIP" in capsys.readouterr().out

    def test_uu_tien_smoke_api_key(self, smoke_module, monkeypatch):
        monkeypatch.setenv("SMOKE_API_KEY", "smoke")
        monkeypatch.setenv("DEPLOY_API_KEY", "deploy")
        assert smoke_module.load_api_key() == "smoke"


class TestPublicUrlScript:
    def test_ghi_url_vao_dung_dong(self, repo_root, tmp_path, monkeypatch):
        module = load_script(repo_root, "public_url")
        doc = tmp_path / "DEPLOYMENT.md"
        doc.write_text(
            "| Mục | Nội dung |\n|-----|-----|\n| Public URL | https://cu.example |\n"
            "| Platform | x |\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(module, "DEPLOYMENT_FILE", doc)
        module.write_deployment("https://moi.trycloudflare.com")
        text = doc.read_text(encoding="utf-8")
        assert "| Public URL | https://moi.trycloudflare.com |" in text
        assert "| Platform | x |" in text

    def test_deployment_md_co_dong_public_url(self, repo_root):
        module = load_script(repo_root, "public_url")
        assert module.PUBLIC_URL_ROW.search((repo_root / "DEPLOYMENT.md").read_text(encoding="utf-8"))
