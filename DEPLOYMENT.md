# Thông Tin Deploy — Checkpoint 5

> `pytest tests/test_cp5.py` đọc file này để tìm địa chỉ service và gọi thử.
>
> **Chỉ ghi TÊN biến môi trường, không bao giờ ghi giá trị API key.**

## Thông Tin Học Viên

| Mục | Nội dung |
|-----|----------|
| Họ và tên | Nguyễn Quang Huy |
| Mã học viên | 2A202602421 |
| Repo | https://github.com/kamitoznguyen-collab/K4-L3A-DAY12-NguyenQuangHuy-2A202602421-Cloud-Service-And-Deployment |

## Service

| Mục | Nội dung |
|-----|----------|
| Public URL | https://metallica-handbook-effect-forward.trycloudflare.com |
| Platform | Cloudflare Tunnel (Quick Tunnel) → Docker Compose tự host |
| Ngày deploy | 2026-09-28 |

Mở Public URL bằng trình duyệt là vào giao diện chat; API vẫn cần `X-API-Key`.
Tài liệu API: `<Public URL>/docs`.

## Kiến Trúc

```
Internet ──HTTPS──► Cloudflare edge ──► cloudflared ──► nginx ──► agent × 2 ──► redis
                    (TLS, DDoS)         (container,      (LB, rate   (FastAPI,    (history,
                                         outbound only)   limit/IP)   stateless)   quota, cost)
```

- **Không mở cổng nào ra Internet.** `cloudflared` chủ động kết nối ra Cloudflare
  edge; traffic đi ngược vào theo kết nối đó. Không cần IP tĩnh, không cần port
  forwarding, HTTPS do Cloudflare cấp.
- **Hai replica agent** sau nginx; state nằm trong Redis nên request liên tiếp
  của một user có thể rơi vào hai container khác nhau mà lịch sử vẫn liền mạch
  (header `X-Served-By` cho biết replica nào trả lời).
- **Ba lớp bảo vệ chi phí:** nginx giới hạn 10 req/s theo IP → app giới hạn
  N req/phút theo user (sliding window, Redis) → cost guard chặn theo ngân sách tháng.

### Vì sao chọn Cloudflare Tunnel thay vì Railway / Render

Stack chạy bằng đúng `docker-compose.yml` dùng ở CP2–CP4, nên môi trường public
giống hệt môi trường đã test (cùng image, cùng nginx, cùng Redis). Không cần thẻ
thanh toán, không bị "ngủ đông" như free tier. Đổi lại, service chỉ sống khi máy
chủ đang bật, và Quick Tunnel cấp URL mới mỗi lần container `tunnel` khởi động
lại (chạy `python scripts/public_url.py --write` để cập nhật file này).

`railway.toml` và `render.yaml` vẫn được giữ nguyên làm phương án dự phòng — cả
hai build từ cùng `Dockerfile`.

## Biến Môi Trường

Giá trị thật nằm trong file `.env` trên máy chủ (đã `.gitignore`), docker compose
nội suy vào container qua `${...}`. Không giá trị nào nằm trong repo.

| Biến | Đã set | Nguồn / ghi chú |
|------|--------|-----------------|
| `PORT` | ✅ | compose đặt `8000` bên trong container agent |
| `AGENT_API_KEY` | ✅ | `.env` trên máy chủ, sinh bằng `secrets.token_urlsafe(32)` |
| `REDIS_URL` | ✅ | `redis://redis:6379/0` — service Redis trong compose (AOF bật) |
| `RATE_LIMIT_PER_MINUTE` | ✅ | 10 |
| `MONTHLY_BUDGET_USD` | ✅ | 10.0 |
| `LOG_LEVEL` | ✅ | INFO |
| `HOST_PORT` | ✅ | cổng nginx trên máy chủ (8080 vì 8000 đang bị chương trình khác dùng) |

## Vận Hành

```bash
docker compose --profile public up -d --build   # dựng toàn bộ stack + tunnel
python scripts/public_url.py --write            # lấy URL công khai, ghi vào file này
python scripts/smoke_test.py <Public URL>       # kiểm tra end-to-end bản đang chạy
docker compose logs -f agent                    # log JSON của app
docker compose up -d --build agent              # deploy bản mới (SIGTERM → tắt êm)
```

## Lệnh Kiểm Tra

Thay `<URL>` bằng Public URL ở trên:

```bash
# 1. Liveness — mong đợi 200 {"status":"ok"}
curl -i <URL>/health

# 2. Readiness — mong đợi 200 {"status":"ready"} (đã nối được Redis)
curl -i <URL>/ready

# 3. Không có API key — mong đợi 401
curl -i -X POST <URL>/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Hello"}'

# 4. Có API key — mong đợi 200 kèm câu trả lời
curl -i -X POST <URL>/ask \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "X-User-Id: sv-test" \
  -d '{"question":"Deploy là gì?"}'

# 5. Rate limit — gọi 15 lần, những lần cuối phải trả 429
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "%{http_code} " -X POST <URL>/ask \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $AGENT_API_KEY" \
    -H "X-User-Id: sv-test" \
    -d '{"question":"test"}'
done; echo
```

## Kết Quả Chạy Thật

Chạy ngày 2026-09-28 vào Public URL ở trên (chỉ giữ status line, vài header và body):

```
# 1. GET /health
HTTP/1.1 200 OK
Content-Type: application/json
x-served-by: 98d97ec633fd
{"status":"ok","service":"day12-agent","version":"1.0.0"}

# 2. GET /ready
HTTP/1.1 200 OK
Content-Type: application/json
{"status":"ready","redis":true}

# 3. POST /ask không có API key
HTTP/1.1 401 Unauthorized
WWW-Authenticate: ApiKey
{"detail":"invalid or missing API key"}

# 5. Rate limit — 15 lần liên tiếp cùng user sv-test (10/phút)
200 200 200 200 200 200 200 200 200 200 429 429 429 429 429

# 4. POST /ask có API key (chạy sau khi cửa sổ 60 giây của lệnh 5 đã trôi qua;
#    history_length = 20 vì lịch sử đã chạm HISTORY_MAX_MESSAGES)
HTTP/1.1 200 OK
x-served-by: d8d1070e224b
{"answer":"Câu hỏi hay. Deploy là gì thường được giải quyết bằng cách chuẩn hóa môi trường chạy: cùng một image chạy giống nhau ở laptop và trên cloud. (Mình đang nhớ 20 lượt trao đổi trước đó.)","user_id":"sv-test","history_length":20,"cost_usd":9.285e-05,"tokens":{"in":439,"out":45}}
```

## Ảnh Chụp Màn Hình

- `screenshots/health.png` — trình duyệt mở `<Public URL>/health`
- `screenshots/dashboard.png` — trạng thái stack (`docker compose ps`) và tunnel đang chạy
