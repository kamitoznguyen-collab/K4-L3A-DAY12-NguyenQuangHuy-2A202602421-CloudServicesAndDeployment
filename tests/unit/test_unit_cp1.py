"""Test bổ sung cho CP1 — config, structured logging, /health, middleware.

Bộ test chấm điểm (tests/test_cp1.py) kiểm tra yêu cầu tối thiểu; file này
kiểm tra các case biên mà một service public phải xử lý đúng.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

CONFIG_ENV_VARS = (
    "AGENT_API_KEY",
    "PORT",
    "REDIS_URL",
    "RATE_LIMIT_PER_MINUTE",
    "MONTHLY_BUDGET_USD",
    "LOG_LEVEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Xóa mọi biến cấu hình để test không phụ thuộc máy đang chạy."""
    for name in CONFIG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────
class TestSettingsValidation:
    def test_tu_choi_khoa_mau_trong_env_example(self, clean_env):
        from app.config import EXAMPLE_API_KEY_PLACEHOLDER, Settings

        clean_env.setenv("AGENT_API_KEY", EXAMPLE_API_KEY_PLACEHOLDER)
        with pytest.raises(ValidationError, match="giá trị mẫu"):
            Settings(_env_file=None)

    def test_khoa_mau_trong_env_example_khop_voi_hang_so(self, repo_root):
        """Nếu ai đó đổi .env.example mà quên đổi hằng số, validator mất tác dụng."""
        from app.config import EXAMPLE_API_KEY_PLACEHOLDER

        example = (repo_root / ".env.example").read_text(encoding="utf-8")
        assert f"AGENT_API_KEY={EXAMPLE_API_KEY_PLACEHOLDER}" in example

    @pytest.mark.parametrize("value", ["", "   "])
    def test_tu_choi_khoa_rong(self, clean_env, value):
        from app.config import Settings

        clean_env.setenv("AGENT_API_KEY", value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_cat_khoang_trang_thua_cua_khoa(self, clean_env):
        """Dán khóa vào dashboard hay dính dấu cách/xuống dòng ở cuối."""
        from app.config import Settings

        clean_env.setenv("AGENT_API_KEY", "  khoa-that \n")
        assert Settings(_env_file=None).agent_api_key == "khoa-that"

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("PORT", "0"),
            ("PORT", "70000"),
            ("PORT", "khong-phai-so"),
            ("RATE_LIMIT_PER_MINUTE", "0"),
            ("MONTHLY_BUDGET_USD", "0"),
            ("MONTHLY_BUDGET_USD", "-1"),
            ("LOG_LEVEL", "VERBOSE"),
        ],
    )
    def test_tu_choi_gia_tri_vo_ly(self, clean_env, name, value):
        """Cấu hình sai phải chết lúc khởi động, không phải lúc có traffic."""
        from app.config import Settings

        clean_env.setenv("AGENT_API_KEY", "k")
        clean_env.setenv(name, value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_log_level_duoc_chuan_hoa(self, clean_env):
        from app.config import Settings

        clean_env.setenv("AGENT_API_KEY", "k")
        clean_env.setenv("LOG_LEVEL", " debug ")
        assert Settings(_env_file=None).log_level == "DEBUG"

    def test_ten_bien_khong_phan_biet_hoa_thuong(self, clean_env):
        from app.config import Settings

        clean_env.setenv("agent_api_key", "khoa-chu-thuong")
        assert Settings(_env_file=None).agent_api_key == "khoa-chu-thuong"


class TestSettingsSources:
    def test_doc_duoc_tu_file_env(self, clean_env, tmp_path):
        from app.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text(
            "AGENT_API_KEY=tu-file\nRATE_LIMIT_PER_MINUTE=7\n", encoding="utf-8"
        )
        settings = Settings(_env_file=env_file)
        assert settings.agent_api_key == "tu-file"
        assert settings.rate_limit_per_minute == 7

    def test_bien_moi_truong_uu_tien_hon_file_env(self, clean_env, tmp_path):
        """Trên cloud, giá trị set trong dashboard phải thắng file .env lỡ bị copy vào."""
        from app.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text("AGENT_API_KEY=tu-file\n", encoding="utf-8")
        clean_env.setenv("AGENT_API_KEY", "tu-dashboard")
        assert Settings(_env_file=env_file).agent_api_key == "tu-dashboard"

    def test_bo_qua_bien_la(self, clean_env, tmp_path):
        """.env có LOCAL_FALLBACK, DEPLOY_API_KEY... không được làm app crash."""
        from app.config import Settings

        env_file = tmp_path / ".env"
        env_file.write_text(
            "AGENT_API_KEY=k\nLOCAL_FALLBACK=false\nDEPLOY_API_KEY=x\n", encoding="utf-8"
        )
        assert Settings(_env_file=env_file).agent_api_key == "k"

    def test_get_settings_duoc_cache(self):
        from app.config import get_settings

        assert get_settings() is get_settings()


# ─────────────────────────────────────────────────────────────
# Structured logging
# ─────────────────────────────────────────────────────────────
class TestLogEvent:
    def test_field_khong_ghi_de_khoa_he_thong(self):
        from app.logging_utils import log_event

        parsed = json.loads(log_event("e", timestamp="gia-mao"))
        assert parsed["timestamp"] != "gia-mao"

    def test_ba_khoa_he_thong_dung_dau(self):
        from app.logging_utils import log_event

        keys = list(json.loads(log_event("e", a=1, b=2)))
        assert keys[:3] == ["event", "level", "timestamp"]

    def test_timestamp_la_utc(self):
        from app.logging_utils import log_event

        stamp = json.loads(log_event("e"))["timestamp"]
        assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0

    def test_gia_tri_khong_phai_json_van_ghi_duoc(self):
        """Log mà crash request thì còn tệ hơn không log."""
        from app.logging_utils import log_event

        when = datetime(2026, 1, 2, tzinfo=timezone.utc)
        parsed = json.loads(log_event("e", when=when, err=ValueError("hong")))
        assert parsed["when"].startswith("2026-01-02")
        assert parsed["err"] == "hong"

    def test_giu_nguyen_tieng_viet(self):
        from app.logging_utils import log_event

        assert "Xin chào" in log_event("e", question="Xin chào")

    def test_xuong_dong_trong_gia_tri_khong_lam_vo_log(self, capsys):
        from app.logging_utils import log_event

        log_event("e", text="dong 1\ndong 2")
        out = capsys.readouterr().out
        assert len(out.strip().splitlines()) == 1
        assert json.loads(out)["text"] == "dong 1\ndong 2"


# ─────────────────────────────────────────────────────────────
# /health và middleware
# ─────────────────────────────────────────────────────────────
class TestHealth:
    def test_tra_ve_ten_va_phien_ban(self, client):
        from app.main import SERVICE_NAME, SERVICE_VERSION

        body = client.get("/health").json()
        assert body == {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

    def test_van_200_khi_redis_chet(self, client_factory):
        class StoreChet:
            def ping(self):
                raise ConnectionError("Redis chết")

        assert client_factory(store=StoreChet()).get("/health").status_code == 200

    def test_chi_nhan_get(self, client):
        assert client.post("/health").status_code == 405


class TestRequestMiddleware:
    def test_sinh_request_id(self, client):
        request_id = client.get("/health").headers.get("X-Request-ID")
        assert request_id and len(request_id) == 32

    def test_giu_request_id_tu_proxy(self, client):
        response = client.get("/health", headers={"X-Request-ID": "tu-nginx-123"})
        assert response.headers["X-Request-ID"] == "tu-nginx-123"

    def test_cat_request_id_qua_dai(self, client):
        response = client.get("/health", headers={"X-Request-ID": "a" * 500})
        assert len(response.headers["X-Request-ID"]) == 64

    def test_co_security_header(self, client):
        headers = client.get("/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"

    def test_ghi_access_log_json(self, client, capsys):
        client.get("/health", headers={"X-Request-ID": "req-log-test"})
        records = [r for r in json_lines(capsys.readouterr().out) if r["event"] == "http_request"]
        assert records, "mỗi request phải có một dòng access log"
        record = records[-1]
        assert record["request_id"] == "req-log-test"
        assert record["method"] == "GET"
        assert record["path"] == "/health"
        assert record["status"] == 200
        assert record["duration_ms"] >= 0

    def test_access_log_khong_lo_api_key(self, client, capsys):
        client.post(
            "/ask",
            json={"question": "x"},
            headers={"X-API-Key": "khoa-bi-mat-khong-duoc-log"},
        )
        assert "khoa-bi-mat-khong-duoc-log" not in capsys.readouterr().out

    def test_loi_khong_luong_truoc_thanh_500_json(self, client_factory, capsys):
        """Exception lọt ra ngoài → 500 có request_id, log mức error, không lộ traceback."""
        from app import main as main_module

        def store_hong():
            raise RuntimeError("chi tiet noi bo")

        client = client_factory()
        main_module.app.dependency_overrides[main_module.get_store] = store_hong
        response = client.get("/ready", headers={"X-Request-ID": "req-500"})

        assert response.status_code == 500
        assert response.json() == {"detail": "internal server error", "request_id": "req-500"}
        assert "chi tiet noi bo" not in response.text
        errors = [r for r in json_lines(capsys.readouterr().out) if r["event"] == "unhandled_error"]
        assert errors and errors[-1]["level"] == "error"
