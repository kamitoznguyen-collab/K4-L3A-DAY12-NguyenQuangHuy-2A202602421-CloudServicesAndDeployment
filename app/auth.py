"""CP3 — Xác thực bằng API key.

Public URL = ai cũng gọi được. Không có lớp này, hóa đơn LLM của bạn do
người lạ quyết định.
"""

from __future__ import annotations

import re
import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings

ANONYMOUS_USER = "anonymous"

# user_id đi thẳng vào tên Redis key (ratelimit:<id>, cost:<id>:<tháng>...),
# nên chỉ nhận một bộ ký tự an toàn và độ dài có giới hạn.
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def verify_api_key(
    x_api_key: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> str:
    """Kiểm tra header ``X-API-Key``; trả về user_id nếu hợp lệ.

    - Thiếu hoặc sai khóa → 401.
    - So sánh bằng ``secrets.compare_digest`` (thời gian không phụ thuộc vị
      trí ký tự sai đầu tiên → chống timing attack). So sánh trên bytes UTF-8
      vì ``compare_digest`` với ``str`` chứa ký tự ngoài ASCII sẽ ném
      TypeError — tức là một header lạ biến thành lỗi 500.
    - ``X-User-Id`` không gửi → ``ANONYMOUS_USER``; gửi mà sai định dạng → 400.
    """
    expected = get_settings().agent_api_key
    if x_api_key is None or not secrets.compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if x_user_id is None:
        return ANONYMOUS_USER
    if not USER_ID_PATTERN.fullmatch(x_user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id must be 1-64 chars of letters, digits, _ . @ -",
        )
    return x_user_id
