"""CP3 — Rate limiting bằng thuật toán sliding window.

Đếm số request trong 60 giây **gần nhất** (cửa sổ trượt), thay vì đếm theo
phút đồng hồ. Đếm theo phút đồng hồ có lỗ hổng: 10 request lúc 10:00:59 và
10 request lúc 10:01:01 = 20 request trong 2 giây mà vẫn "đúng luật".

Cấu trúc dữ liệu: Redis Sorted Set (ZSET), score = timestamp của request.
"""

from __future__ import annotations

import time
import uuid

from fastapi import HTTPException, status

WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, client, limit_per_minute: int) -> None:
        self.client = client
        self.limit = limit_per_minute

    @staticmethod
    def _key(user_id: str) -> str:
        """CHO SẴN — mỗi user một key riêng."""
        return f"ratelimit:{user_id}"

    def hit_count(self, user_id: str, now: float | None = None) -> int:
        """Số request của user trong ``WINDOW_SECONDS`` giây gần nhất."""
        now = now if now is not None else time.time()
        key = self._key(user_id)
        self.client.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
        return int(self.client.zcard(key))

    def check(self, user_id: str, now: float | None = None) -> None:
        """Cho qua nếu còn quota, ngược lại raise 429.

        Cách làm "đếm rồi mới ghi" (đếm → nếu chưa đủ thì ZADD) đúng khi chỉ
        có một process, nhưng khi scale nhiều instance thì hai request đồng
        thời cùng đếm thấy 9/10, cùng ghi, và user lọt qua 11 request.

        Ở đây dọn cửa sổ + ghi + đếm nằm trong MỘT transaction (MULTI/EXEC),
        nên mỗi request thấy đúng vị trí của mình trong hàng đợi. Vượt hạn mức
        thì xóa lại chính entry vừa ghi (request bị từ chối không chiếm quota)
        rồi trả 429. Kết quả: không bao giờ vượt ``limit``, kể cả khi nhiều
        container cùng ghi vào một Redis.

        Member là ``timestamp:uuid`` — phải duy nhất, nếu không hai request
        cùng timestamp ghi đè nhau trong ZSET và bị đếm thiếu.
        """
        now = now if now is not None else time.time()
        key = self._key(user_id)
        member = f"{now}:{uuid.uuid4().hex}"

        pipe = self.client.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
        pipe.zadd(key, {member: now})
        pipe.zcard(key)
        pipe.expire(key, WINDOW_SECONDS)
        _, _, count, _ = pipe.execute()

        if count > self.limit:
            self.client.zrem(key, member)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": str(WINDOW_SECONDS)},
            )
