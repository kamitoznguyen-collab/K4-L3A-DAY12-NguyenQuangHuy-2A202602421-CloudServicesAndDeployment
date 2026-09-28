# Phiếu Phản Ánh — K4 Level 3A, Ngày 12

> **Bài làm cá nhân.** Trả lời bằng lời của chính bạn, dựa trên những gì bạn
> quan sát được khi chạy code — không sao chép đáp án của người khác.
>
> Cách trả lời: thay dòng `> *Câu trả lời của bạn*` bằng câu trả lời.
> `grade.py` đếm số câu đã trả lời (15 điểm cho 10 câu).
>
> Họ và tên: Nguyễn Quang Huy  Mã học viên: 2A202602421

---

### Câu 1 — Fail fast (CP1)

Trong `Settings`, `agent_api_key` không có giá trị mặc định nên app chết ngay
khi khởi động nếu thiếu biến môi trường. Hãy mô tả một tình huống cụ thể mà
việc "chết sớm" này cứu bạn, so với việc để mặc định `"changeme"`.

> Khi deploy lên Railway hoặc Render, nếu quên set biến `AGENT_API_KEY` trong dashboard của platform, container sẽ chết ngay lúc khởi động và ta thấy lỗi `ValidationError` trong log: `agent_api_key Field required`. Khi thử thật bằng `docker run --rm day12-agent:prod` (không truyền key), container in `Application startup failed. Exiting.` và thoát với mã 3. Nhờ vậy, ta phát hiện và sửa ngay trong vài phút. Ngược lại, nếu để mặc định `"changeme"`, app vẫn khởi động bình thường, service chạy công khai trên internet với một khóa mà ai cũng đoán được — bất kỳ bot quét nào cũng gọi được `/ask`, tiêu hết ngân sách LLM mà ta không hề hay biết cho đến khi nhận hóa đơn. Fail fast biến lỗi cấu hình thành lỗi hiển thị rõ ràng ngay lúc deploy, thay vì để nó âm thầm gây thiệt hại tài chính.

---

### Câu 2 — Log cho máy đọc (CP1)

Chạy service và gọi `/ask` vài lần. Dán một dòng log JSON bạn thu được, rồi
nêu **hai** việc bạn làm được với dòng log đó mà `print("đã trả lời xong")`
không làm được.

> Dòng log JSON thu được (lấy bằng `docker compose logs agent --no-log-prefix | grep ask_completed`):
> ```json
> {"event": "ask_completed", "level": "info", "timestamp": "2026-09-28T08:53:36.674987+00:00", "user_id": "cp5-test", "tokens_in": 41, "tokens_out": 45, "cost_usd": 3.315e-05}
> ```
>
> Hai việc làm được mà `print("đã trả lời xong")` không làm được:
>
> 1. **Lọc và cảnh báo tự động theo chi phí:** Vì log có trường `cost_usd` ở dạng số, ta có thể dùng công cụ giám sát (Datadog, CloudWatch, hoặc `jq` trên terminal) để lọc ra các request có chi phí cao bất thường, ví dụ `jq 'select(.cost_usd > 0.01)'`, rồi gắn cảnh báo Slack/email khi ngân sách sắp hết. Với `print()` thì phải regex thủ công, dễ sai và không tự động hóa được.
>
> 2. **Thống kê theo user và theo thời gian:** Trường `user_id` và `timestamp` cho phép trả lời câu hỏi "user nào tiêu nhiều tiền nhất tuần này?" hoặc "tỷ lệ lỗi 5 phút qua bao nhiêu phần trăm?" bằng cách aggregate trên các trường JSON. `print()` chỉ ra một chuỗi không có cấu trúc, không thể tách trường để phân tích.

---

### Câu 3 — Kích thước image (CP2)

Build cả hai phiên bản và ghi lại số đo thật:

```bash
docker build -f <Dockerfile-1-stage> -t agent:single .
docker build -t agent:multi .
docker images | grep agent
```

| Bản | Dung lượng |
|-----|-----------|
| 1 stage (bản đầu) | 1.79 GB |
| Multi-stage | 258 MB |

Giải thích: phần dung lượng chênh lệch đó là những gì?

> Phần chênh lệch (~1.5 GB) đến từ ba nguồn:
>
> 1. **Base image:** bản 1 stage dùng `python:3.11` đầy đủ — kèm sẵn gcc, make, header C/C++ và rất nhiều gói Debian chỉ cần khi build. Bản multi-stage dùng `python:3.12-slim`, chỉ có Python và vài thư viện hệ thống tối thiểu. Đây là phần lớn nhất.
> 2. **Thư viện không cần lúc chạy:** bản 1 stage `pip install -r requirements.txt` nên cài cả `pytest`, `fakeredis`, `httpx`, `ruff`... Bản multi-stage chỉ cài `requirements-prod.txt` (fastapi, uvicorn, pydantic, pydantic-settings, redis, python-dotenv) vào virtualenv ở stage `builder`, rồi stage `runtime` chỉ `COPY --from=builder /opt/venv` sang.
> 3. **Build context:** bản 1 stage `COPY . .` kéo theo cả `tests/`, `.git`, tài liệu... (lúc đó `.dockerignore` gần như trống). Bản multi-stage chỉ copy `app/` và `utils/`, và `.dockerignore` loại `.env`, `.git`, `tests`, `.venv`...
>
> Stage `builder` bị bỏ lại sau khi build xong, nên pip và mọi thứ dùng để cài đặt không nằm trong image cuối.
---

### Câu 4 — Thứ tự lệnh trong Dockerfile (CP2)

Sửa một ký tự trong `app/main.py` rồi build lại. Với Dockerfile của bạn, những
layer nào được dùng lại từ cache, layer nào phải chạy lại? Nếu bạn đặt
`COPY . .` lên trước `RUN pip install` thì kết quả khác thế nào?

> Với Dockerfile hiện tại (multi-stage), thứ tự là:
> 1. `COPY requirements.txt requirements-prod.txt ./` → **cache HIT** (file dependency không đổi)
> 2. `RUN pip install -r requirements-prod.txt` → **cache HIT** (layer trước không đổi nên layer này cũng được dùng lại)
> 3. `COPY --chown=app:app app/ ./app/` → **cache MISS** (vì `app/main.py` đã thay đổi → Docker phát hiện context khác)
> 4. `COPY --chown=app:app utils/ ./utils/` → **chạy lại** (mọi layer sau cache miss đều phải chạy lại)
>
> Kết quả: chỉ mất vài giây vì bước `pip install` nặng nhất đã được cache.
>
> Nếu đặt `COPY . .` lên trước `RUN pip install`: sửa bất kỳ file nào (kể cả thêm một dấu cách trong `main.py`) sẽ invalidate layer `COPY . .`, kéo theo `pip install` phải chạy lại từ đầu — mất 30–60 giây tải và cài lại toàn bộ thư viện dù dependency không đổi. Docker cache hoạt động theo nguyên tắc "từ layer đầu tiên thay đổi trở đi, tất cả layer sau đều bị invalidate", nên đặt thứ ít thay đổi (dependency) trước, thứ hay thay đổi (code) sau.

---

### Câu 5 — Vì sao không chạy bằng root (CP2)

Container mặc định chạy bằng root. Mô tả chuỗi sự kiện dẫn từ "một lỗ hổng
trong code Python của bạn" tới "kẻ tấn công có quyền cao trên máy host", và
lệnh `USER` cắt đứt chuỗi đó ở chỗ nào.

> **Chuỗi sự kiện khi chạy root:**
> 1. Code Python có lỗ hổng (ví dụ: endpoint `/ask` cho phép injection, hoặc một thư viện bên thứ ba có CVE cho phép thực thi lệnh tùy ý).
> 2. Kẻ tấn công khai thác lỗ hổng, gửi payload qua request → process Python thực thi lệnh hệ thống.
> 3. Vì process Python đang chạy bằng **root** (UID 0), lệnh thực thi có toàn quyền bên trong container: đọc `/etc/shadow`, cài package, mount filesystem.
> 4. Kẻ tấn công leo thang đặc quyền ra host bằng cách lợi dụng quyền root trong container — ví dụ: escape qua lỗ hổng kernel (container breakout), hoặc mount Docker socket nếu có, hoặc truy cập volume chứa dữ liệu nhạy cảm từ host.
> 5. Kết quả: kẻ tấn công có quyền root trên máy host.
>
> **`USER app` cắt đứt ở bước 3:** Process Python chạy dưới user `app` (UID 10001), không có quyền root. Dù kẻ tấn công thực thi được lệnh trong container, họ chỉ có quyền của user thường — không thể đọc file nhạy cảm của hệ thống, không thể cài phần mềm, không thể mount filesystem, và đặc biệt là cực kỳ khó leo thang đặc quyền ra host. Lớp `USER` biến lỗ hổng cấp cao thành lỗ hổng bị giới hạn trong sandbox.

---

### Câu 6 — Cửa sổ trượt (CP3)

Rate limit của bạn dùng sliding window 60 giây. Nếu thay bằng cách đếm theo
phút đồng hồ (reset lúc giây 00), một người dùng có thể gửi tối đa bao nhiêu
request trong 2 giây liên tiếp khi hạn mức là 10/phút? Giải thích cách đạt được
con số đó.

> Tối đa **20 request trong 2 giây liên tiếp.** Cách đạt được:
>
> - Gửi 10 request lúc **10:00:59** (giây cuối cùng của phút 10:00). Counter phút 10:00 đạt 10/10 — đúng hạn mức.
> - Sang giây **10:01:00**, counter phút 10:01 reset về 0. Gửi thêm 10 request lúc 10:01:00 → counter phút 10:01 đạt 10/10 — vẫn "đúng luật".
> - Tổng cộng: 20 request trong khoảng thời gian từ 10:00:59 đến 10:01:00, tức chỉ trong 2 giây.
>
> Đây là lỗ hổng của fixed window: ranh giới phút tạo ra "khe hở" cho phép burst gấp đôi hạn mức. Sliding window (cửa sổ trượt) dùng Redis Sorted Set luôn nhìn lại đúng 60 giây gần nhất, không quan tâm ranh giới phút đồng hồ, nên không có kẽ hở này — bất kỳ cửa sổ 60 giây nào cũng chỉ cho phép tối đa 10 request.

---

### Câu 7 — Rate limit và cost guard (CP3)

Hai cơ chế này khác nhau ở điểm nào? Cho một tình huống mà rate limit cho qua
nhưng cost guard phải chặn, và một tình huống ngược lại.

> **Khác nhau cốt lõi:**
> - **Rate limit** giới hạn *tần suất* (số request trong 60 giây), bảo vệ khỏi flood/DDoS — trả 429.
> - **Cost guard** giới hạn *ngân sách tích lũy* (tổng tiền USD trong cả tháng), bảo vệ khỏi chi phí vượt kiểm soát — trả 402.
>
> **Tình huống rate limit cho qua, cost guard chặn:**
> User gửi 5 request/phút (dưới hạn mức 10), nhưng mỗi request dùng prompt rất dài (50.000 token) khiến chi phí mỗi lần rất cao. Sau vài ngày liên tục như vậy, tổng chi tiêu tháng vượt `MONTHLY_BUDGET_USD = 10.0`. Request tiếp theo: rate limit thấy 5 < 10 → cho qua, nhưng cost guard thấy `spent > 10.0` → chặn, trả 402.
>
> **Tình huống cost guard cho qua, rate limit chặn:**
> User mới tạo tài khoản, chưa tiêu đồng nào (`spent = 0.0`). User viết script gọi `/ask` 15 lần trong 1 giây. Cost guard thấy `0.0 < 10.0` → cho qua, nhưng rate limit đếm thấy request thứ 11 trong cửa sổ 60 giây → chặn, trả 429.

---

### Câu 8 — /health khác /ready (CP4)

Nếu gộp hai endpoint làm một và cho nó kiểm tra Redis, chuyện gì xảy ra với cụm
3 container khi Redis mất kết nối 30 giây? Trả lời theo đúng thứ tự sự kiện.

> Giả sử cụm chạy trên orchestrator có liveness probe tự restart container (Kubernetes, hoặc healthcheck của Railway/Render). Docker Compose thuần thì chỉ đánh dấu `unhealthy` chứ không tự restart — nhưng load balancer vẫn sẽ loại cả 3 instance.
>
> 1. **Giây 0:** Redis mất kết nối (network blip, Redis restart, hoặc bảo trì).
> 2. **Giây ~0–10:** Lần probe kế tiếp gọi endpoint gộp → `store.ping()` thất bại (client Redis có timeout 2 giây) → endpoint trả 503 trên **cả 3 container cùng lúc**, vì cả 3 dùng chung một Redis.
> 3. **Giây ~10–30:** Probe thất bại liên tiếp (ví dụ 3 lần × 10 giây) → orchestrator kết luận cả 3 container "đã chết" và **restart cả 3 cùng lúc**. Load balancer không còn instance nào để gửi request → **toàn bộ hệ thống down**, kể cả những request không cần Redis.
> 4. **Giây ~30:** Redis hoạt động lại — nhưng các container đang khởi động lại dở.
> 5. **Giây ~30–60:** Container mới khởi động, qua health check, rồi mới nhận traffic trở lại.
> 6. **Kết quả:** Sự cố Redis 30 giây biến thành downtime 30–60 giây toàn hệ thống, và mọi request đang xử lý dở lúc restart đều bị cắt.
>
> Nếu tách riêng: `/health` (liveness) không kiểm tra Redis → container không bị restart. `/ready` (readiness) thấy Redis chết → load balancer ngừng đẩy traffic vào, nhưng container vẫn sống. Khi Redis quay lại, `/ready` trả 200, traffic được đẩy vào lại ngay lập tức — không restart, không downtime.

---

### Câu 9 — Stateless (CP4)

Chạy `docker compose up --scale agent=3` rồi gọi `/ask` nhiều lần với cùng một
`X-User-Id`. Quan sát `history_length` trong response. Nếu lịch sử được lưu
trong một dict Python thay vì Redis, bạn sẽ thấy con số đó thay đổi thế nào?

> **Với Redis (hiện tại):** `history_length` tăng đều: 0, 2, 4, 6, 8... Bất kể request rơi vào container A, B, hay C (kiểm tra qua header `X-Served-By`), lịch sử đều liền mạch vì cả 3 container cùng đọc/ghi vào một Redis duy nhất. Mỗi lần gọi, `store.append()` thêm 2 entry (user + assistant) vào Redis list `history:<user_id>`, và mọi container đều thấy.
>
> **Nếu dùng dict Python (`conversation_history = {}`):** `history_length` sẽ nhảy loạn, ví dụ: 0, 0, 0, 2, 2, 2, 4... Vì nginx round-robin phân chia request qua 3 container, mỗi container có dict riêng trong RAM. Request 1 vào container A → A lưu history, length = 0 rồi tăng thành 2. Request 2 vào container B → B chưa có gì, length = 0. Request 3 vào container C → C cũng chưa có, length = 0. Request 4 quay lại A → length = 2, request 5 vào B → 2, request 6 vào C → 2, request 7 về A → 4. Agent "mất trí nhớ" ngẫu nhiên — user thấy agent quên hết cuộc hội thoại cứ mỗi 2-3 câu. Ngoài ra, khi container bị restart (deploy bản mới, crash, scale down), toàn bộ dict trong RAM mất sạch — history = 0 trở lại từ đầu.

---

### Câu 10 — Deploy thật (CP5)

Ghi lại **một** lỗi bạn gặp khi deploy lên cloud (build fail, health check
timeout, sai REDIS_URL, app không đọc `$PORT`...): thông báo lỗi là gì, bạn
tìm ra nguyên nhân bằng cách nào, và sửa ra sao?

> **Lỗi gặp phải:** Sau khi chạy `docker compose --profile public up -d --build`, script `public_url.py` in ra URL `https://louisiana-alfred-flexibility-currently.trycloudflare.com` nhưng gọi vào thì không được — Cloudflare trả **HTTP 530** (edge không tìm thấy tunnel đang sống cho hostname đó).
>
> **Cách tìm nguyên nhân:** Chạy `docker compose logs tunnel` thấy tiến trình cloudflared đầu tiên đã chết ngay lúc khởi động:
> ```
> ERR Initiating shutdown error="Could not lookup srv records on _v2-origintunneld._tcp.argotunnel.com:
>     lookup _v2-origintunneld._tcp.argotunnel.com on 127.0.0.11:53: server misbehaving"
> ```
> Vì có `restart: unless-stopped`, Docker khởi động lại container, và Quick Tunnel cấp một hostname **khác** (`controversial-exclude-routine-much.trycloudflare.com`). Script đã kịp đọc hostname của tiến trình cũ từ metrics (`/quicktunnel`) trước khi nó chết, nên URL in ra là URL không bao giờ sống lại. Nguyên nhân gốc: truy vấn DNS kiểu SRV đi qua DNS nội bộ của Docker (127.0.0.11) rồi chuyển tiếp lên resolver của máy, và resolver đó trả lỗi.
>
> **Cách sửa:**
> 1. Thêm `dns: [1.1.1.1, 1.0.0.1]` cho service `tunnel` trong `docker-compose.yml` — DNS nội bộ Docker vẫn phân giải tên `nginx`, còn truy vấn ra ngoài đi thẳng tới Cloudflare DNS. Sau khi sửa, tunnel đăng ký ngay lần đầu (`Registered tunnel connection ... location=sin16`).
> 2. Sửa `scripts/public_url.py` để hỏi lại hostname mỗi vòng và chỉ báo thành công khi chính hostname đó trả `/health` 200.
>
> Sau đó `https://metallica-handbook-effect-forward.trycloudflare.com/health` trả `200 {"status":"ok"}` và `pytest tests/test_cp5.py` pass 9/9.
