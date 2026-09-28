"""Test bổ sung cho CP4 — store, readiness, lịch sử, graceful shutdown."""

from __future__ import annotations

import json
import signal
import subprocess
import time

import pytest

from docker_helpers import needs_docker


class TestConversationStoreChiTiet:
    def test_giu_nguyen_unicode(self, fake_redis):
        from app.store import ConversationStore

        store = ConversationStore(fake_redis)
        store.append("u1", "user", "Tiếng Việt có dấu 🚀")
        assert store.get_history("u1")[0]["content"] == "Tiếng Việt có dấu 🚀"

    def test_luu_khong_escape_unicode(self, fake_redis):
        """ensure_ascii=False → Redis lưu ít byte hơn và đọc tay dễ hơn."""
        from app.store import ConversationStore

        ConversationStore(fake_redis).append("u1", "user", "chào")
        raw = fake_redis.lrange(ConversationStore._key("u1"), 0, -1)[0]
        assert "chào" in raw

    def test_ttl_duoc_lam_moi_moi_lan_ghi(self, fake_redis):
        from app.store import HISTORY_TTL_SECONDS, ConversationStore

        store = ConversationStore(fake_redis)
        key = ConversationStore._key("u1")
        store.append("u1", "user", "a")
        fake_redis.expire(key, 5)
        store.append("u1", "assistant", "b")
        assert fake_redis.ttl(key) > HISTORY_TTL_SECONDS - 5

    def test_cat_bot_khong_anh_huong_user_khac(self, fake_redis):
        from app.store import HISTORY_MAX_MESSAGES, ConversationStore

        store = ConversationStore(fake_redis)
        store.append("u2", "user", "cua u2")
        for i in range(HISTORY_MAX_MESSAGES + 5):
            store.append("u1", "user", str(i))
        assert len(store.get_history("u1")) == HISTORY_MAX_MESSAGES
        assert len(store.get_history("u2")) == 1

    def test_clear_xoa_het(self, fake_redis):
        from app.store import ConversationStore

        store = ConversationStore(fake_redis)
        store.append("u1", "user", "a")
        store.clear("u1")
        assert store.get_history("u1") == []

    def test_ping_false_khi_redis_tra_false(self):
        from app.store import ConversationStore

        class RedisLa:
            def ping(self):
                return False

        assert ConversationStore(RedisLa()).ping() is False


class TestReadinessChiTiet:
    def test_503_co_body_ro_rang_khi_redis_chet(self, client_factory):
        class StoreChet:
            def ping(self):
                return False

        response = client_factory(store=StoreChet()).get("/ready")
        assert response.json() == {"status": "not ready", "redis": False}

    def test_redis_nem_loi_thi_503_khong_phai_500(self, client_factory):
        from app.store import ConversationStore

        class RedisMatKetNoi:
            def ping(self):
                raise ConnectionError("connection refused")

        response = client_factory(store=ConversationStore(RedisMatKetNoi())).get("/ready")
        assert response.status_code == 503

    def test_ready_khong_can_api_key(self, client_real_store):
        assert client_real_store.get("/ready").json() == {"status": "ready", "redis": True}


class TestHistoryEndpoints:
    def test_can_api_key(self, client_real_store):
        assert client_real_store.get("/history").status_code == 401
        assert client_real_store.delete("/history").status_code == 401

    def test_doc_lich_su_sau_khi_hoi(self, client_real_store, auth_headers):
        client_real_store.post("/ask", json={"question": "Câu một"}, headers=auth_headers)
        body = client_real_store.get("/history", headers=auth_headers).json()
        assert body["user_id"] == "sv-test"
        assert body["count"] == 2
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        assert body["messages"][0]["content"] == "Câu một"

    def test_xoa_lich_su_thi_bat_dau_lai(self, client_real_store, auth_headers):
        client_real_store.post("/ask", json={"question": "a"}, headers=auth_headers)
        assert client_real_store.delete("/history", headers=auth_headers).json()["cleared"]
        after = client_real_store.post("/ask", json={"question": "b"}, headers=auth_headers)
        assert after.json()["history_length"] == 0

    def test_moi_user_chi_thay_lich_su_cua_minh(self, client_real_store, api_key):
        headers_a = {"X-API-Key": api_key, "X-User-Id": "user-a"}
        headers_b = {"X-API-Key": api_key, "X-User-Id": "user-b"}
        client_real_store.post("/ask", json={"question": "cua a"}, headers=headers_a)
        assert client_real_store.get("/history", headers=headers_b).json()["count"] == 0


def provide(value):
    """Dependency trả về đúng `value` (không dùng default arg — FastAPI coi đó là query param)."""
    return lambda: value


class TestStatelessQuaHttp:
    def test_request_xen_ke_giua_hai_instance_van_nho(self, client_factory, fake_redis, auth_headers):
        """Mỗi request đi vào một "container" khác (store object khác), cùng Redis."""
        from app import main as main_module
        from app.store import ConversationStore

        client = client_factory(store=ConversationStore(fake_redis))
        lengths = []
        for i in range(4):
            instance = ConversationStore(fake_redis)  # container mới mỗi request
            main_module.app.dependency_overrides[main_module.get_store] = provide(instance)
            response = client.post("/ask", json={"question": f"cau {i}"}, headers=auth_headers)
            lengths.append(response.json()["history_length"])
        assert lengths == [0, 2, 4, 6]


class TestLifecycleChiTiet:
    @pytest.fixture
    def restore_signals(self):
        saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        yield
        for sig, handler in saved.items():
            signal.signal(sig, handler)

    def test_install_hai_lan_khong_de_quy(self, restore_signals):
        from app.lifecycle import Lifecycle

        calls = []
        signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
        life = Lifecycle()
        life.install()
        life.install()
        life.request_shutdown(signal.SIGTERM, None)  # không được RecursionError
        assert calls == [signal.SIGTERM]

    def test_handler_cu_la_sig_ign_thi_bo_qua(self, restore_signals):
        """SIG_IGN/SIG_DFL là hằng số, không gọi được — không được crash."""
        from app.lifecycle import Lifecycle

        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        life = Lifecycle()
        life.install()
        life.request_shutdown(signal.SIGTERM, None)
        assert life.shutting_down is True

    def test_sigint_goi_dung_handler_cu_cua_no(self, restore_signals):
        from app.lifecycle import Lifecycle

        seen = []
        signal.signal(signal.SIGTERM, lambda signum, frame: seen.append("term"))
        signal.signal(signal.SIGINT, lambda signum, frame: seen.append("int"))
        life = Lifecycle()
        life.install()
        life.request_shutdown(signal.SIGINT, None)
        assert seen == ["int"]


@pytest.mark.docker
@needs_docker
class TestGracefulShutdownThat:
    """SIGTERM thật vào container thật: phải tắt êm, không đợi tới SIGKILL."""

    def _wait_healthy(self, container: str, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],
                capture_output=True, text=True,
            ).stdout.strip()
            if status == "healthy":
                return
            time.sleep(1)
        pytest.fail(f"container {container} không healthy sau {timeout}s")

    def test_sigterm_thi_thoat_ma_0_nhanh(self, docker_image):
        container = subprocess.run(
            ["docker", "run", "-d", "-e", "AGENT_API_KEY=graceful-test",
             "--health-interval=1s", docker_image],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        try:
            self._wait_healthy(container)
            started = time.monotonic()
            subprocess.run(["docker", "stop", "-t", "15", container], check=True, capture_output=True)
            elapsed = time.monotonic() - started

            exit_code = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.ExitCode}}", container],
                capture_output=True, text=True,
            ).stdout.strip()
            logs = subprocess.run(
                ["docker", "logs", container], capture_output=True, text=True
            )
            events = [
                json.loads(line)["event"]
                for line in (logs.stdout + logs.stderr).splitlines()
                if line.startswith("{")
            ]

            assert exit_code == "0", (
                f"exit code {exit_code} — 137 nghĩa là bị SIGKILL: SIGTERM không tới "
                "được uvicorn (thiếu exec?) hoặc handler cũ không được gọi lại"
            )
            assert elapsed < 10, f"mất {elapsed:.1f}s để tắt — gần như đã đợi tới SIGKILL"
            assert "service_stopped" in events, "lifespan shutdown không chạy"
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)


class TestLifespanVaProvider:
    @pytest.fixture
    def fresh_providers(self):
        from app import main as main_module
        from app.config import get_settings

        caches = (get_settings, main_module.get_redis, main_module.get_store,
                  main_module.get_rate_limiter, main_module.get_cost_guard)
        for cached in caches:
            cached.cache_clear()
        yield main_module
        for cached in caches:
            cached.cache_clear()

    @pytest.fixture
    def install_calls(self, monkeypatch, fresh_providers):
        """TestClient chạy lifespan ở thread phụ, nơi signal.signal() bị cấm
        (uvicorn thật chạy ở main thread) → thay install() bằng spy."""
        calls = []
        monkeypatch.setattr(fresh_providers.lifecycle, "install", lambda: calls.append(1))
        return calls

    def test_lifespan_log_khoi_dong_va_dung(self, fresh_providers, install_calls, capsys):
        from fastapi.testclient import TestClient

        with TestClient(fresh_providers.app) as client:
            assert client.get("/health").status_code == 200
        assert install_calls == [1], "lifespan phải đăng ký signal handler lúc khởi động"
        events = [
            json.loads(line)["event"]
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("{")
        ]
        assert events[0] == "service_started"
        assert events[-1] == "service_stopped"

    def test_lifespan_thieu_khoa_thi_khong_khoi_dong(
        self, fresh_providers, install_calls, monkeypatch, tmp_path
    ):
        """Fail fast xảy ra lúc startup, không đợi request đầu tiên."""
        from fastapi.testclient import TestClient
        from pydantic import ValidationError

        monkeypatch.delenv("AGENT_API_KEY")
        monkeypatch.chdir(tmp_path)  # thư mục không có .env
        with pytest.raises(ValidationError), TestClient(fresh_providers.app):
            pass
        assert install_calls == [], "cấu hình hỏng thì không được đi tiếp"

    def test_ba_provider_dung_chung_mot_redis(self, fresh_providers):
        from app.config import get_settings

        main_module = fresh_providers
        redis_client = main_module.get_redis()
        assert main_module.get_store().client is redis_client
        assert main_module.get_rate_limiter().client is redis_client
        assert main_module.get_cost_guard().client is redis_client
        assert main_module.get_rate_limiter().limit == get_settings().rate_limit_per_minute
        assert main_module.get_cost_guard().budget == get_settings().monthly_budget_usd
