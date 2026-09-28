#!/usr/bin/env python3
"""Smoke test một bản deploy đang chạy — dùng cho local, CI và URL công khai.

    python scripts/smoke_test.py http://localhost:8000
    python scripts/smoke_test.py https://xxx.trycloudflare.com

API key đọc từ biến môi trường SMOKE_API_KEY, rồi DEPLOY_API_KEY, rồi
AGENT_API_KEY (file .env được nạp nếu có). Không có key thì chỉ chạy các
bước không cần xác thực.

Mỗi lần chạy dùng một user id ngẫu nhiên, nên chạy lại liên tục không bị dính
rate limit của lần trước. Thoát với mã 1 nếu có bước nào hỏng.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def load_api_key() -> str | None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass
    for name in ("SMOKE_API_KEY", "DEPLOY_API_KEY", "AGENT_API_KEY"):
        if value := os.getenv(name, "").strip():
            return value
    return None


class Smoke:
    def __init__(self, base_url: str, api_key: str | None, timeout: float) -> None:
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.http = httpx.Client(timeout=timeout, follow_redirects=False)
        self.user_id = f"smoke-{uuid.uuid4().hex[:10]}"
        self.failures: list[str] = []
        self.instances: set[str] = set()

    def headers(self) -> dict:
        return {"X-API-Key": self.api_key or "", "X-User-Id": self.user_id}

    def step(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(name)

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self.http.request(method, f"{self.base}{path}", **kwargs)
        if served_by := response.headers.get("X-Served-By"):
            self.instances.add(served_by)
        return response

    def wait_until_up(self, seconds: float) -> None:
        """Free tier / container vừa khởi động có thể cần vài giây."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                if self.request("GET", "/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                return
            time.sleep(2)

    def run(self) -> int:
        print(f"Smoke test {self.base}  (user: {self.user_id})")

        health = self.request("GET", "/health")
        self.step(
            "GET /health → 200 ok",
            health.status_code == 200 and health.json().get("status") == "ok",
            f"{health.status_code}",
        )

        ready = self.request("GET", "/ready")
        self.step("GET /ready → 200 (Redis nối được)", ready.status_code == 200, ready.text[:80])

        if self.base.startswith("https://"):
            self.step("HTTPS", True)

        anon = self.request("POST", "/ask", json={"question": "Hello"})
        self.step("POST /ask không key → 401", anon.status_code == 401, f"{anon.status_code}")

        wrong = self.request(
            "POST",
            "/ask",
            json={"question": "Hello"},
            headers={"X-API-Key": "sai-" + uuid.uuid4().hex},
        )
        self.step("POST /ask sai key → 401", wrong.status_code == 401, f"{wrong.status_code}")

        if not self.api_key:
            print("  [SKIP] các bước cần API key (chưa đặt SMOKE_API_KEY / DEPLOY_API_KEY)")
            return self.finish()

        lengths = []
        for i in range(3):
            response = self.request(
                "POST", "/ask", json={"question": f"Smoke câu {i}"}, headers=self.headers()
            )
            if response.status_code != 200:
                self.step(
                    f"POST /ask #{i} → 200", False, f"{response.status_code} {response.text[:120]}"
                )
                return self.finish()
            lengths.append(response.json()["history_length"])
        self.step("POST /ask có key → 200", True)
        self.step(
            "Lịch sử liền mạch qua các request (stateless)", lengths == [0, 2, 4], f"{lengths}"
        )

        usage = self.request("GET", "/usage", headers=self.headers()).json()
        self.step(
            "GET /usage ghi nhận chi phí",
            usage.get("spent_usd", 0) > 0,
            f"spent={usage.get('spent_usd')} / budget={usage.get('budget_usd')}",
        )

        # Đã dùng 3 request ở trên → gửi thêm `limit` request thì đúng 3 cái cuối bị 429
        limit = int(usage.get("rate_limit_per_minute", 10))
        codes = [
            self.request(
                "POST", "/ask", json={"question": "rate"}, headers=self.headers()
            ).status_code
            for _ in range(limit)
        ]
        expected = [200] * (limit - len(lengths)) + [429] * len(lengths)
        self.step(f"Rate limit {limit}/phút → 429", codes == expected, " ".join(map(str, codes)))

        cleared = self.request("DELETE", "/history", headers=self.headers())
        self.step("DELETE /history dọn dữ liệu smoke", cleared.status_code == 200)
        return self.finish()

    def finish(self) -> int:
        if self.instances:
            print(
                f"  Replica đã phục vụ: {len(self.instances)} ({', '.join(sorted(self.instances))})"
            )
        if self.failures:
            print(f"KẾT QUẢ: {len(self.failures)} bước hỏng: {', '.join(self.failures)}")
            return 1
        print("KẾT QUẢ: tất cả đều ổn")
        return 0


def main() -> int:
    # Console Windows mặc định cp1252 — không in được tiếng Việt
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_url")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--wait", type=float, default=60.0, help="đợi /health tối đa N giây")
    args = parser.parse_args()

    smoke = Smoke(args.base_url, load_api_key(), args.timeout)
    smoke.wait_until_up(args.wait)
    try:
        return smoke.run()
    except httpx.HTTPError as err:
        print(f"KẾT QUẢ: không gọi được {args.base_url}: {type(err).__name__}: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
