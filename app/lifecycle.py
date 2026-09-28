"""CP4 — Graceful shutdown.

Khi bạn deploy phiên bản mới, orchestrator (Docker, Railway, Cloud Run, K8s)
gửi **SIGTERM** rồi đợi vài chục giây trước khi SIGKILL. Nếu app bỏ qua tín
hiệu đó, mọi request đang xử lý dở bị cắt giữa chừng — user thấy lỗi 502 mỗi
lần bạn deploy.

Ứng xử đúng: nhận SIGTERM → báo "tôi sắp tắt" qua health check để load
balancer ngừng đẩy traffic mới vào → xử lý nốt request đang chạy → thoát.
"""

from __future__ import annotations

import signal


class Lifecycle:
    """Giữ trạng thái vòng đời của process."""

    def __init__(self) -> None:
        self.shutting_down = False
        # Handler đã được đăng ký trước ta (của uvicorn) — xem install()
        self._previous: dict = {}

    def request_shutdown(self, signum=None, frame=None) -> None:
        """Signal handler: đánh dấu process đang tắt dần rồi nhường cho handler cũ.

        Mỗi tín hiệu chỉ có MỘT handler: đăng ký handler của mình là ghi đè
        handler của uvicorn — thứ thật sự dừng server. Không gọi lại nó thì
        app bật cờ "đang tắt" rồi chạy tiếp mãi, tới khi bị SIGKILL.

        Handler chạy xen giữa bytecode nên chỉ làm việc nhẹ: bật cờ, gọi tiếp.
        """
        self.shutting_down = True
        previous = self._previous.get(signum)
        if callable(previous):
            previous(signum, frame)

    def install(self) -> None:
        """Đăng ký handler cho SIGTERM (orchestrator) và SIGINT (Ctrl+C).

        Nhớ handler cũ TRƯỚC khi ghi đè để request_shutdown gọi lại được.
        Gọi install() hai lần không được tự lưu chính mình làm "handler cũ"
        (sẽ đệ quy vô hạn khi nhận tín hiệu).
        """
        for sig in (signal.SIGTERM, signal.SIGINT):
            current = signal.getsignal(sig)
            if current != self.request_shutdown:
                self._previous[sig] = current
            signal.signal(sig, self.request_shutdown)


# Một instance dùng chung cho cả app
lifecycle = Lifecycle()
