# syntax=docker/dockerfile:1
# ═══════════════════════════════════════════════════════════════════
# CP2 — Production image
#
#   Stage 1 `builder`: tạo virtualenv và cài dependency runtime vào đó.
#   Stage 2 `runtime`: chỉ copy virtualenv + source, chạy bằng user thường.
#
# Build:  docker build -t day12-agent:prod .
# ═══════════════════════════════════════════════════════════════════

# ── Stage 1: builder ───────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Chỉ copy file dependency trước → sửa code không làm mất cache layer pip.
# requirements.txt tham chiếu requirements-prod.txt; image chỉ cài phần prod.
COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

# ── Stage 2: runtime ───────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    FORWARDED_ALLOW_IPS="*"

# User hệ thống không có shell đăng nhập, không có home, uid cố định
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

# slim không có curl — dùng chính Python để gọi /health
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=2)"]

# `exec` để uvicorn thay thế shell và trở thành PID 1 → nhận SIGTERM trực tiếp.
# Không có exec thì sh nhận SIGTERM, không chuyển tiếp, và graceful shutdown chết.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT}\" --proxy-headers --no-access-log --timeout-graceful-shutdown 20"]
