"""Agent service — điểm ráp nối của cả lab (CP1, CP3, CP4).

Luồng một request tới /ask:

    client ──► verify_api_key ──► rate_limiter ──► cost_guard
                                                       │
                              store.get_history ◄──────┘
                                       │
                                    ask_llm
                                       │
                              store.append × 2 ──► cost_guard.record ──► log_event
"""

from __future__ import annotations

import socket
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from utils.mock_llm import ask_llm

from .auth import verify_api_key
from .config import get_settings
from .cost_guard import CostGuard
from .lifecycle import lifecycle
from .logging_utils import log_event
from .rate_limiter import RateLimiter
from .store import ConversationStore, get_redis_client

SERVICE_NAME = "day12-agent"
SERVICE_VERSION = "1.0.0"


# ─────────────────────────────────────────────────────────────
# Providers — CHO SẴN
# Tách ra thành hàm để test có thể thay bằng Redis giả qua
# app.dependency_overrides, và để kết nối Redis chỉ tạo khi thật sự cần.
# ─────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def get_redis():
    """Một connection pool dùng chung cho store, limiter và cost guard."""
    return get_redis_client()


@lru_cache(maxsize=1)
def get_store() -> ConversationStore:
    return ConversationStore(get_redis())


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_redis(), get_settings().rate_limit_per_minute)


@lru_cache(maxsize=1)
def get_cost_guard() -> CostGuard:
    return CostGuard(get_redis(), get_settings().monthly_budget_usd)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Chạy lúc app khởi động và lúc tắt."""
    # Đọc cấu hình NGAY lúc khởi động: thiếu AGENT_API_KEY thì container chết
    # tại đây (fail fast), không phải đợi tới request đầu tiên mới 500.
    settings = get_settings()
    lifecycle.install()
    log_event(
        "service_started",
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        monthly_budget_usd=settings.monthly_budget_usd,
    )
    yield
    log_event("service_stopped", service=SERVICE_NAME)


app = FastAPI(title="Day 12 Production Agent", version=SERVICE_VERSION, lifespan=lifespan)

REQUEST_ID_HEADER = "X-Request-ID"
# Container nào đã xử lý request — nhìn header này là thấy load balancer đang
# chia request qua các replica (và lịch sử vẫn liền mạch nhờ Redis).
INSTANCE_ID = socket.gethostname()
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
# Trang chat chỉ được tải script/style từ chính origin này: kể cả khi có lỗi
# XSS, trình duyệt cũng không chạy script lạ và không gửi dữ liệu đi nơi khác.
# (/docs dùng Swagger UI từ CDN nên không gắn CSP này.)
UI_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Gắn request id, security header và ghi một dòng access log JSON.

    Request id lấy từ header client/proxy gửi lên nếu có (để nối log giữa
    nginx và app), ngược lại tự sinh. Log KHÔNG chứa header nào của request —
    trong đó có X-API-Key.
    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    request_id = request_id[:64]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as err:  # noqa: BLE001 — catch-all có chủ đích: log rồi trả 500 gọn
        log_event(
            "unhandled_error",
            level="error",
            request_id=request_id,
            path=request.url.path,
            error=f"{type(err).__name__}: {err}",
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": "internal server error", "request_id": request_id},
        )

    response.headers[REQUEST_ID_HEADER] = request_id
    response.headers["X-Served-By"] = INSTANCE_ID
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers.setdefault("Content-Security-Policy", UI_CONTENT_SECURITY_POLICY)
    log_event(
        "http_request",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return response


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


# ─────────────────────────────────────────────────────────────
# Giao diện web
# ─────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    """Trang chat. Trang này công khai; mọi thao tác vẫn cần API key."""
    return FileResponse(STATIC_DIR / "index.html")


# ─────────────────────────────────────────────────────────────
# Health & readiness
# ─────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness probe — process còn sống không?

    Endpoint này phải **nhẹ**: không gọi Redis, không query DB. Nó chỉ trả
    lời câu hỏi "có cần restart container này không?". Nếu nó phụ thuộc
    Redis, Redis chết một nhịp là cả cụm container bị restart theo.
    """
    if lifecycle.shutting_down:
        return JSONResponse(status_code=503, content={"status": "shutting_down"})
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@app.get("/ready")
def ready(store: ConversationStore = Depends(get_store)):
    """Readiness probe — đã sẵn sàng nhận traffic chưa?

    Khác /health ở chỗ: endpoint này ĐƯỢC PHÉP kiểm tra dependency. Load
    balancer dùng nó để quyết định có đẩy request vào instance này không;
    Redis chết thì instance tạm rời vòng xoay, nhưng KHÔNG bị restart.
    """
    if lifecycle.shutting_down:
        return JSONResponse(status_code=503, content={"status": "shutting_down"})
    if not store.ping():
        return JSONResponse(status_code=503, content={"status": "not ready", "redis": False})
    return {"status": "ready", "redis": True}


# ─────────────────────────────────────────────────────────────
# Endpoint chính
# ─────────────────────────────────────────────────────────────
@app.post("/ask")
def ask(
    payload: AskRequest,
    user_id: str = Depends(verify_api_key),
    store: ConversationStore = Depends(get_store),
    limiter: RateLimiter = Depends(get_rate_limiter),
    guard: CostGuard = Depends(get_cost_guard),
):
    """Hỏi agent một câu.

    Thứ tự: 401 (verify_api_key, trong Depends) → 429 → 402 → gọi LLM → lưu
    lịch sử → ghi chi phí → log. Mọi bước chặn đều nằm TRƯỚC lời gọi LLM, vì
    tiền mất ở bước đó: chặn sau khi đã gọi là vừa trả tiền vừa trả lỗi.
    """
    limiter.check(user_id)
    guard.check(user_id)

    history = store.get_history(user_id)
    result = ask_llm(payload.question, history)

    store.append(user_id, "user", payload.question)
    store.append(user_id, "assistant", result["answer"])
    guard.record(user_id, result["cost_usd"])

    log_event(
        "ask_completed",
        user_id=user_id,
        tokens_in=result["tokens_in"],
        tokens_out=result["tokens_out"],
        cost_usd=result["cost_usd"],
    )
    return {
        "answer": result["answer"],
        "user_id": user_id,
        "history_length": len(history),
        "cost_usd": result["cost_usd"],
        "tokens": {"in": result["tokens_in"], "out": result["tokens_out"]},
    }


@app.get("/history")
def history(
    user_id: str = Depends(verify_api_key),
    store: ConversationStore = Depends(get_store),
):
    """Lịch sử hội thoại của user (tối đa HISTORY_MAX_MESSAGES lượt gần nhất)."""
    messages = store.get_history(user_id)
    return {"user_id": user_id, "messages": messages, "count": len(messages)}


@app.delete("/history")
def clear_history(
    user_id: str = Depends(verify_api_key),
    store: ConversationStore = Depends(get_store),
):
    """Xóa lịch sử hội thoại — bắt đầu cuộc trò chuyện mới."""
    store.clear(user_id)
    log_event("history_cleared", user_id=user_id)
    return {"user_id": user_id, "cleared": True}


@app.get("/usage")
def usage(
    user_id: str = Depends(verify_api_key),
    limiter: RateLimiter = Depends(get_rate_limiter),
    guard: CostGuard = Depends(get_cost_guard),
):
    """Quota còn lại của user: chi tiêu tháng này và số request trong 60 giây.

    Chỉ đọc, không tính vào rate limit — xem quota không được làm tốn quota.
    """
    spent = guard.spent(user_id)
    return {
        "user_id": user_id,
        "month": guard.current_month(),
        "spent_usd": round(spent, 8),
        "budget_usd": guard.budget,
        "remaining_usd": round(guard.remaining(user_id), 8),
        "rate_limit_per_minute": limiter.limit,
        "requests_last_minute": limiter.hit_count(user_id),
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
