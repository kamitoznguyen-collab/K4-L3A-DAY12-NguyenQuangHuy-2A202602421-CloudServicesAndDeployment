"""CP4 — Stateless: state sống ngoài process.

Nếu lịch sử hội thoại nằm trong một dict trong RAM, thì khi scale lên 3
instance, user hỏi câu 1 vào instance A và câu 2 vào instance B sẽ thấy agent
"mất trí nhớ". Container còn bị restart bất cứ lúc nào. Vì vậy state phải
nằm ở nơi mọi instance cùng nhìn thấy: Redis.
"""

from __future__ import annotations

import json

import redis

from .config import get_settings

HISTORY_MAX_MESSAGES = 20
HISTORY_TTL_SECONDS = 7 * 24 * 3600
REDIS_TIMEOUT_SECONDS = 2.0


def get_redis_client(url: str | None = None):
    """CHO SẴN — tạo client Redis từ URL.

    ``fake://`` trả về Redis giả chạy trong RAM, dùng khi máy bạn chưa có
    Docker. Tiện cho lúc học, nhưng KHÔNG dùng khi deploy: nó vẫn là state
    trong process, đúng cái mà CP4 đang tìm cách loại bỏ.
    """
    url = url or get_settings().redis_url
    if url.startswith("fake://"):
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    # Timeout ngắn: Redis treo thì /ready trả 503 trong ~2 giây, thay vì treo
    # theo và làm probe của platform timeout.
    return redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


class ConversationStore:
    """Lưu lịch sử hội thoại của từng user trong Redis List."""

    def __init__(self, client) -> None:
        self.client = client

    @staticmethod
    def _key(user_id: str) -> str:
        """CHO SẴN."""
        return f"history:{user_id}"

    def ping(self) -> bool:
        """Redis có trả lời không? Dùng cho endpoint /ready.

        Bắt mọi Exception (mất mạng, sai mật khẩu, Redis chưa khởi động...):
        /ready phải trả 503 có kiểm soát, không được để lỗi thành 500.
        """
        try:
            return bool(self.client.ping())
        except Exception:  # noqa: BLE001 — mọi lỗi đều nghĩa là "chưa sẵn sàng"
            return False

    def append(self, user_id: str, role: str, content: str) -> None:
        """Ghi thêm một lượt vào lịch sử, chỉ giữ HISTORY_MAX_MESSAGES lượt mới nhất.

        RPUSH + LTRIM + EXPIRE chạy trong một transaction: không container nào
        đọc được trạng thái "đã thêm nhưng chưa cắt", và key luôn có TTL.
        """
        key = self._key(user_id)
        entry = json.dumps({"role": role, "content": content}, ensure_ascii=False)
        pipe = self.client.pipeline(transaction=True)
        pipe.rpush(key, entry)
        pipe.ltrim(key, -HISTORY_MAX_MESSAGES, -1)
        pipe.expire(key, HISTORY_TTL_SECONDS)
        pipe.execute()

    def get_history(self, user_id: str) -> list[dict]:
        """Đọc lịch sử hội thoại, cũ nhất trước; chưa có gì → list rỗng."""
        return [json.loads(item) for item in self.client.lrange(self._key(user_id), 0, -1)]

    def clear(self, user_id: str) -> None:
        """CHO SẴN — xóa lịch sử của một user."""
        self.client.delete(self._key(user_id))
