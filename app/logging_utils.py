"""CP1 — Structured logging.

`print("user abc hỏi gì đó")` là log cho người đọc. Cloud (Railway, Render,
Cloud Run, Datadog...) đọc log bằng máy: một dòng = một JSON object thì mới
lọc/đếm/cảnh báo được. Đây là khác biệt lớn giữa localhost và production.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def utc_now_iso() -> str:
    """CHO SẴN — thời điểm hiện tại theo ISO-8601, múi giờ UTC."""
    return datetime.now(timezone.utc).isoformat()


def log_event(event: str, level: str = "info", **fields) -> str:
    """Ghi một dòng log JSON ra stdout và trả về chính dòng đó.

    Ba khóa cố định ``event``, ``level``, ``timestamp`` đứng trước; các
    ``**fields`` gộp vào sau nhưng không được ghi đè ba khóa này (nếu không,
    một field tên ``level`` sẽ làm hỏng việc lọc log theo mức).

    ``default=str`` để các kiểu không phải JSON (datetime, Exception...) vẫn
    ghi được thay vì làm crash request đang xử lý.
    """
    record = {"event": event, "level": level.lower(), "timestamp": utc_now_iso()}
    record.update({key: value for key, value in fields.items() if key not in record})
    line = json.dumps(record, ensure_ascii=False, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return line
