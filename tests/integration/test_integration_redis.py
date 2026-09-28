"""Test tích hợp với Redis THẬT (không phải fakeredis).

Chạy khi có biến REDIS_TEST_URL, ví dụ:

    docker compose up -d redis
    REDIS_TEST_URL=redis://127.0.0.1:6379/15 pytest tests/integration -v

(Dùng 127.0.0.1 thay vì localhost: trên Windows localhost thử IPv6 trước và
mất trọn connect timeout vì Redis chỉ publish trên IPv4.)

Mỗi test FLUSHDB database đó — dùng một DB riêng (mặc định /15), đừng trỏ vào
DB đang chứa dữ liệu thật. CI chạy file này với Redis service container.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from fastapi import HTTPException

REDIS_TEST_URL = os.getenv("REDIS_TEST_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_TEST_URL, reason="chưa đặt REDIS_TEST_URL"),
]


@pytest.fixture
def redis_client():
    from app.store import get_redis_client

    client = get_redis_client(REDIS_TEST_URL)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


def new_connection():
    """Một client + connection pool riêng = một container riêng."""
    from app.store import get_redis_client

    return get_redis_client(REDIS_TEST_URL)


def run_concurrently(make_target, count: int) -> list:
    """`make_target(i)` trả về một hàm không tham số; tất cả được gọi cùng lúc."""
    barrier = threading.Barrier(count)
    results: list = []
    lock = threading.Lock()

    def worker(index):
        try:
            target = make_target(index)  # dựng client TRƯỚC, để mọi thread bắn cùng lúc
        except Exception:
            barrier.abort()  # setup lỗi → giải phóng các thread khác thay vì treo mãi
            raise
        barrier.wait(timeout=30)
        try:
            outcome = target()
        except Exception as err:  # noqa: BLE001
            outcome = err
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


class TestStoreRedisThat:
    def test_ping(self, redis_client):
        from app.store import ConversationStore

        assert ConversationStore(redis_client).ping() is True

    def test_redis_khong_ton_tai_thi_ping_false_nhanh(self):
        """Không có timeout thì /ready treo theo Redis và probe của platform timeout."""
        from app.store import ConversationStore, get_redis_client

        started = time.monotonic()
        assert ConversationStore(get_redis_client("redis://10.255.255.1:6379/0")).ping() is False
        assert time.monotonic() - started < 5

    def test_luu_cat_bot_va_ttl(self, redis_client):
        from app.store import HISTORY_MAX_MESSAGES, ConversationStore

        store = ConversationStore(redis_client)
        for i in range(HISTORY_MAX_MESSAGES + 3):
            store.append("u1", "user", f"tin {i} — tiếng Việt")
        history = store.get_history("u1")
        assert len(history) == HISTORY_MAX_MESSAGES
        assert history[-1]["content"] == f"tin {HISTORY_MAX_MESSAGES + 2} — tiếng Việt"
        assert redis_client.ttl(ConversationStore._key("u1")) > 0

    def test_hai_container_cung_thay_mot_lich_su(self, redis_client):
        from app.store import ConversationStore

        ConversationStore(new_connection()).append("u1", "user", "từ container A")
        assert ConversationStore(new_connection()).get_history("u1")[0]["content"] == "từ container A"


class TestRateLimiterRedisThat:
    def test_nhieu_container_dong_thoi_khong_vuot_han_muc(self, redis_client):
        from app.rate_limiter import RateLimiter

        def make_target(_):
            limiter = RateLimiter(new_connection(), limit_per_minute=10)
            return lambda: limiter.check("u1")

        results = run_concurrently(make_target, count=40)
        assert len(results) == 40
        assert sum(r is None for r in results) == 10
        assert all(r.status_code == 429 for r in results if isinstance(r, HTTPException))
        assert RateLimiter(redis_client, 10).hit_count("u1") == 10

    def test_cua_so_truot(self, redis_client):
        from app.rate_limiter import RateLimiter

        limiter = RateLimiter(redis_client, limit_per_minute=2)
        limiter.check("u1", now=1000.0)
        limiter.check("u1", now=1001.0)
        with pytest.raises(HTTPException):
            limiter.check("u1", now=1002.0)
        limiter.check("u1", now=1061.0)


class TestCostGuardRedisThat:
    def test_nhieu_container_ghi_dong_thoi(self, redis_client):
        from app.cost_guard import CostGuard

        def make_target(_):
            guard = CostGuard(new_connection(), 100.0)
            return lambda: guard.record("u1", 0.01)

        run_concurrently(make_target, count=50)
        assert CostGuard(redis_client, 100.0).spent("u1") == pytest.approx(0.5)

    def test_vuot_ngan_sach(self, redis_client):
        from app.cost_guard import CostGuard

        guard = CostGuard(redis_client, 1.0)
        guard.record("u1", 0.9)
        with pytest.raises(HTTPException) as err:
            guard.check("u1", estimated_cost=0.2)
        assert err.value.status_code == 402


class TestApiRedisThat:
    @pytest.fixture
    def client(self, redis_client):
        from fastapi.testclient import TestClient

        from app import main as main_module
        from app.cost_guard import CostGuard
        from app.rate_limiter import RateLimiter
        from app.store import ConversationStore

        overrides = main_module.app.dependency_overrides
        overrides[main_module.get_store] = lambda: ConversationStore(redis_client)
        overrides[main_module.get_rate_limiter] = lambda: RateLimiter(redis_client, 5)
        overrides[main_module.get_cost_guard] = lambda: CostGuard(redis_client, 10.0)
        yield TestClient(main_module.app, raise_server_exceptions=False)
        overrides.clear()

    def test_luong_day_du(self, client, auth_headers):
        assert client.get("/ready").json() == {"status": "ready", "redis": True}

        lengths = [
            client.post("/ask", json={"question": f"câu {i}"}, headers=auth_headers).json()[
                "history_length"
            ]
            for i in range(3)
        ]
        assert lengths == [0, 2, 4]

        usage = client.get("/usage", headers=auth_headers).json()
        assert usage["requests_last_minute"] == 3
        assert usage["spent_usd"] > 0

        codes = [
            client.post("/ask", json={"question": "x"}, headers=auth_headers).status_code
            for _ in range(4)
        ]
        assert codes == [200, 200, 429, 429]
