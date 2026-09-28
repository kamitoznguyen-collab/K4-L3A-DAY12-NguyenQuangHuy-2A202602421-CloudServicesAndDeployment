"""Test bổ sung cho CP3 — auth, rate limit, cost guard, /ask, /usage."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi import HTTPException


def run_concurrently(target, count: int) -> list:
    """Chạy `target()` trên `count` thread cùng lúc, trả về list kết quả/exception."""
    barrier = threading.Barrier(count)
    results: list = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            outcome = target()
        except Exception as err:  # noqa: BLE001 — muốn thu cả exception
            outcome = err
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# ─────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────
class TestVerifyApiKey:
    def test_khoa_phan_biet_hoa_thuong(self, api_key):
        from app.auth import verify_api_key

        with pytest.raises(HTTPException) as err:
            verify_api_key(x_api_key=api_key.upper(), x_user_id=None)
        assert err.value.status_code == 401

    def test_khoa_dung_nhung_thua_ky_tu_thi_401(self, api_key):
        from app.auth import verify_api_key

        for bad in (api_key + " ", api_key[:-1], ""):
            with pytest.raises(HTTPException):
                verify_api_key(x_api_key=bad, x_user_id=None)

    def test_khoa_co_ky_tu_ngoai_ascii_thi_401_khong_phai_500(self):
        """compare_digest(str, str) ném TypeError với ký tự ngoài ASCII."""
        from app.auth import verify_api_key

        with pytest.raises(HTTPException) as err:
            verify_api_key(x_api_key="khóa-tiếng-việt", x_user_id=None)
        assert err.value.status_code == 401

    def test_401_co_header_www_authenticate(self, client):
        response = client.post("/ask", json={"question": "x"})
        assert response.headers.get("WWW-Authenticate") == "ApiKey"

    @pytest.mark.parametrize(
        "user_id",
        ["a" * 65, "co dau cach", "chen:namespace", "user/../x", "tiếng-việt", "*"],
    )
    def test_user_id_sai_dinh_dang_thi_400(self, api_key, user_id):
        """user_id nằm trong tên Redis key — `a:b` có thể giẫm lên key của người khác."""
        from app.auth import verify_api_key

        with pytest.raises(HTTPException) as err:
            verify_api_key(x_api_key=api_key, x_user_id=user_id)
        assert err.value.status_code == 400

    @pytest.mark.parametrize("user_id", ["sv-01", "a", "huy.nguyen@lab", "A_b-9", "x" * 64])
    def test_user_id_hop_le(self, api_key, user_id):
        from app.auth import verify_api_key

        assert verify_api_key(x_api_key=api_key, x_user_id=user_id) == user_id

    def test_kiem_tra_khoa_truoc_user_id(self):
        """Không có khóa thì 401, không tiết lộ gì về quy tắc user_id."""
        from app.auth import verify_api_key

        with pytest.raises(HTTPException) as err:
            verify_api_key(x_api_key=None, x_user_id="sai dinh dang")
        assert err.value.status_code == 401

    def test_kiem_tra_khoa_truoc_ca_body(self, client):
        response = client.post("/ask", json={"question": ""})
        assert response.status_code == 401


# ─────────────────────────────────────────────────────────────
# Rate limiter
# ─────────────────────────────────────────────────────────────
class TestRateLimiterChiTiet:
    def test_request_bi_tu_choi_khong_chiem_quota(self, fake_redis):
        from app.rate_limiter import RateLimiter

        limiter = RateLimiter(fake_redis, limit_per_minute=2)
        limiter.check("u1", now=1000.0)
        limiter.check("u1", now=1001.0)
        for i in range(5):
            with pytest.raises(HTTPException):
                limiter.check("u1", now=1030.0 + i)
        assert limiter.hit_count("u1", now=1035.0) == 2
        # 1060.5: request lúc 1000 đã rời cửa sổ → có chỗ cho đúng 1 request
        limiter.check("u1", now=1060.5)

    def test_entry_dung_60_giay_thi_het_han(self, fake_redis):
        from app.rate_limiter import WINDOW_SECONDS, RateLimiter

        limiter = RateLimiter(fake_redis, limit_per_minute=1)
        limiter.check("u1", now=1000.0)
        limiter.check("u1", now=1000.0 + WINDOW_SECONDS)

    def test_cung_timestamp_van_dem_du(self, fake_redis):
        """Member trùng nhau trong ZSET sẽ ghi đè → đếm thiếu → lọt quota."""
        from app.rate_limiter import RateLimiter

        limiter = RateLimiter(fake_redis, limit_per_minute=5)
        for _ in range(3):
            limiter.check("u1", now=1000.0)
        assert limiter.hit_count("u1", now=1000.0) == 3

    def test_key_co_ttl(self, fake_redis):
        from app.rate_limiter import WINDOW_SECONDS, RateLimiter

        RateLimiter(fake_redis, limit_per_minute=5).check("u1")
        assert 0 < fake_redis.ttl(RateLimiter._key("u1")) <= WINDOW_SECONDS

    def test_429_co_retry_after(self, client_factory, auth_headers):
        client = client_factory(rate_limit=1)
        client.post("/ask", json={"question": "x"}, headers=auth_headers)
        blocked = client.post("/ask", json={"question": "x"}, headers=auth_headers)
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"

    def test_dong_thoi_khong_vuot_han_muc(self, fake_redis):
        """Mô phỏng nhiều container cùng ghi một Redis: không ai lọt quá hạn mức."""
        from app.rate_limiter import RateLimiter

        limiter = RateLimiter(fake_redis, limit_per_minute=10)
        results = run_concurrently(lambda: limiter.check("u1"), count=40)
        allowed = [r for r in results if r is None]
        rejected = [r for r in results if isinstance(r, HTTPException)]
        assert len(allowed) == 10
        assert len(rejected) == 30
        assert limiter.hit_count("u1") == 10


# ─────────────────────────────────────────────────────────────
# Cost guard
# ─────────────────────────────────────────────────────────────
class TestCostGuardChiTiet:
    def test_moi_thang_mot_ngan_sach(self, fake_redis):
        from app.cost_guard import CostGuard

        guard = CostGuard(fake_redis, 1.0)
        guard.record("u1", 5.0, month="2026-01")
        assert guard.spent("u1", month="2026-02") == 0.0
        guard.check("u1", estimated_cost=0.5, month="2026-02")

    def test_dung_bang_ngan_sach_van_cho_qua(self, fake_redis):
        from app.cost_guard import CostGuard

        guard = CostGuard(fake_redis, 1.0)
        guard.record("u1", 0.5)
        guard.check("u1", estimated_cost=0.5)

    def test_remaining_khong_am(self, fake_redis):
        from app.cost_guard import CostGuard

        guard = CostGuard(fake_redis, 1.0)
        guard.record("u1", 3.0)
        assert guard.remaining("u1") == 0.0

    def test_record_tra_ve_tong_moi(self, fake_redis):
        from app.cost_guard import CostGuard

        guard = CostGuard(fake_redis, 1.0)
        assert guard.record("u1", 0.25) == pytest.approx(0.25)
        assert guard.record("u1", 0.5) == pytest.approx(0.75)

    def test_key_co_ttl(self, fake_redis):
        from app.cost_guard import KEY_TTL_SECONDS, CostGuard

        CostGuard(fake_redis, 1.0).record("u1", 0.1)
        assert 0 < fake_redis.ttl(CostGuard._key("u1")) <= KEY_TTL_SECONDS

    def test_ghi_dong_thoi_khong_mat_cap_nhat(self, fake_redis):
        from app.cost_guard import CostGuard

        guard = CostGuard(fake_redis, 100.0)
        run_concurrently(lambda: guard.record("u1", 0.01), count=50)
        assert guard.spent("u1") == pytest.approx(0.5)


# ─────────────────────────────────────────────────────────────
# /ask qua HTTP
# ─────────────────────────────────────────────────────────────
@pytest.fixture
def llm_calls(monkeypatch):
    """Đếm số lần /ask thật sự gọi LLM."""
    from app import main as main_module

    calls: list[str] = []
    real_ask_llm = main_module.ask_llm

    def spy(question, history=None):
        calls.append(question)
        return real_ask_llm(question, history)

    monkeypatch.setattr(main_module, "ask_llm", spy)
    return calls


class TestAskFlow:
    def test_bi_chan_429_thi_khong_goi_llm(self, client_factory, auth_headers, llm_calls):
        client = client_factory(rate_limit=1)
        client.post("/ask", json={"question": "mot"}, headers=auth_headers)
        client.post("/ask", json={"question": "hai"}, headers=auth_headers)
        assert llm_calls == ["mot"]

    def test_bi_chan_402_thi_khong_goi_llm_khong_ghi_lich_su(
        self, client_factory, fake_redis, auth_headers, llm_calls
    ):
        from app.cost_guard import CostGuard
        from app.store import ConversationStore

        store = ConversationStore(fake_redis)
        client = client_factory(store=store, budget=1.0)
        fake_redis.set(CostGuard._key("sv-test"), "1.5")

        response = client.post("/ask", json={"question": "x"}, headers=auth_headers)
        assert response.status_code == 402
        assert llm_calls == []
        assert store.get_history("sv-test") == []

    def test_chi_phi_ghi_nhan_bang_chi_phi_tra_ve(self, client, fake_redis, auth_headers):
        from app.cost_guard import CostGuard

        body = client.post("/ask", json={"question": "Redis là gì?"}, headers=auth_headers).json()
        assert CostGuard(fake_redis, 10.0).spent("sv-test") == pytest.approx(body["cost_usd"])

    def test_tokens_dung_dinh_dang(self, client, auth_headers):
        body = client.post("/ask", json={"question": "Hi"}, headers=auth_headers).json()
        assert set(body["tokens"]) == {"in", "out"}
        assert body["tokens"]["in"] >= 1 and body["tokens"]["out"] >= 1

    def test_log_ask_completed_khong_chua_noi_dung_cau_hoi(self, client, auth_headers, capsys):
        """Log đi vào hệ thống bên thứ ba — nội dung chat của user không nằm ở đó."""
        client.post("/ask", json={"question": "bi-mat-rieng-tu"}, headers=auth_headers)
        out = capsys.readouterr().out
        records = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
        completed = [r for r in records if r["event"] == "ask_completed"]
        assert completed and completed[-1]["user_id"] == "sv-test"
        assert completed[-1]["cost_usd"] > 0
        assert "bi-mat-rieng-tu" not in out

    @pytest.mark.parametrize(
        "payload",
        [{}, {"question": None}, {"question": "x" * 2001}, {"cau_hoi": "x"}],
    )
    def test_payload_sai_thi_422(self, client, auth_headers, payload):
        assert client.post("/ask", json=payload, headers=auth_headers).status_code == 422

    def test_user_id_sai_qua_http_thi_400(self, client, api_key):
        response = client.post(
            "/ask", json={"question": "x"}, headers={"X-API-Key": api_key, "X-User-Id": "a:b"}
        )
        assert response.status_code == 400


class TestUsage:
    def test_can_api_key(self, client):
        assert client.get("/usage").status_code == 401

    def test_phan_anh_chi_tieu_va_so_request(self, client_factory, auth_headers):
        client = client_factory(rate_limit=5, budget=2.0)
        spent = sum(
            client.post("/ask", json={"question": f"cau {i}"}, headers=auth_headers).json()["cost_usd"]
            for i in range(3)
        )
        body = client.get("/usage", headers=auth_headers).json()
        assert body["user_id"] == "sv-test"
        assert body["requests_last_minute"] == 3
        assert body["rate_limit_per_minute"] == 5
        assert body["budget_usd"] == 2.0
        assert body["spent_usd"] == pytest.approx(spent)
        assert body["remaining_usd"] == pytest.approx(2.0 - spent)
        assert len(body["month"]) == 7

    def test_xem_usage_khong_ton_quota(self, client_factory, auth_headers):
        client = client_factory(rate_limit=1)
        for _ in range(5):
            assert client.get("/usage", headers=auth_headers).status_code == 200
        assert client.post("/ask", json={"question": "x"}, headers=auth_headers).status_code == 200
