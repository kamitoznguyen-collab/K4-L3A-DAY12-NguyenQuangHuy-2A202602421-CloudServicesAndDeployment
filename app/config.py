"""CP1 — Cấu hình theo 12-Factor.

Nguyên tắc: **không có giá trị cấu hình nào nằm trong code**. Tất cả đến từ
biến môi trường, để cùng một image chạy được ở laptop, staging và production
mà không phải sửa một dòng code nào.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Giá trị mẫu trong .env.example. Copy .env mà quên đổi khóa thì coi như chưa
# set: app phải từ chối khởi động thay vì chạy với một khóa ai cũng biết.
EXAMPLE_API_KEY_PLACEHOLDER = "doi-thanh-khoa-cua-rieng-ban"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class Settings(BaseSettings):
    """Toàn bộ cấu hình của service, đọc từ biến môi trường cùng tên.

    ``agent_api_key`` không có mặc định: quên set secret trên cloud thì app
    chết ngay lúc khởi động (fail fast) thay vì chạy với một khóa mặc định.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    port: int = Field(default=8000, ge=1, le=65535)
    agent_api_key: str
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_per_minute: int = Field(default=10, ge=1)
    monthly_budget_usd: float = Field(default=10.0, gt=0)
    log_level: str = "INFO"

    @field_validator("agent_api_key")
    @classmethod
    def _api_key_phai_that(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("AGENT_API_KEY không được để trống")
        if value == EXAMPLE_API_KEY_PLACEHOLDER:
            raise ValueError(
                "AGENT_API_KEY vẫn là giá trị mẫu trong .env.example — hãy sinh khóa mới"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level_hop_le(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL phải là một trong {LOG_LEVELS}")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Đọc cấu hình một lần rồi cache lại (đọc env mỗi request là lãng phí)."""
    return Settings()
