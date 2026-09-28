#!/usr/bin/env python3
"""In URL công khai của Cloudflare Quick Tunnel đang chạy trong docker compose.

    docker compose --profile public up -d --build
    python scripts/public_url.py              # in URL, đợi tới khi /health trả 200
    python scripts/public_url.py --write      # và ghi URL vào DEPLOYMENT.md

cloudflared mở metrics server ở 127.0.0.1:2000 (xem docker-compose.yml);
endpoint /quicktunnel của nó trả về hostname *.trycloudflare.com vừa được cấp.
Quick Tunnel đổi URL mỗi lần container `tunnel` khởi động lại.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_FILE = ROOT / "DEPLOYMENT.md"
METRICS_URL = "http://127.0.0.1:2000/quicktunnel"
PUBLIC_URL_ROW = re.compile(r"^(\| Public URL \| ).*?( \|)$", re.MULTILINE)


def fetch_hostname(deadline: float) -> str:
    """Hỏi cloudflared tới khi nó báo hostname (tunnel cần vài giây để đăng ký)."""
    last_error = "chưa có phản hồi"
    while time.monotonic() < deadline:
        try:
            hostname = httpx.get(METRICS_URL, timeout=5).json().get("hostname", "")
            if hostname:
                return hostname
            last_error = "tunnel chưa đăng ký xong"
        except (httpx.HTTPError, ValueError) as err:
            last_error = f"{type(err).__name__}: {err}"
        time.sleep(2)
    raise SystemExit(
        f"Không lấy được URL từ {METRICS_URL} ({last_error}).\n"
        "Tunnel đã chạy chưa?  docker compose --profile public up -d\n"
        "Xem log:             docker compose logs tunnel"
    )


def is_serving(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=10).status_code == 200
    except httpx.HTTPError:
        return False


def wait_for_public_url(deadline: float) -> str | None:
    """Đọc hostname rồi đợi nó phục vụ được. Hỏi lại hostname mỗi vòng: nếu
    cloudflared vừa restart, Quick Tunnel đã cấp một hostname KHÁC, và hostname
    cũ sẽ không bao giờ sống lại. DNS của hostname mới cũng cần vài giây."""
    url = None
    while time.monotonic() < deadline:
        url = f"https://{fetch_hostname(deadline)}"
        if is_serving(url):
            return url
        time.sleep(3)
    return None


def write_deployment(url: str) -> None:
    text = DEPLOYMENT_FILE.read_text(encoding="utf-8")
    updated, count = PUBLIC_URL_ROW.subn(rf"\g<1>{url}\g<2>", text, count=1)
    if not count:
        raise SystemExit("Không tìm thấy dòng '| Public URL | ... |' trong DEPLOYMENT.md")
    DEPLOYMENT_FILE.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Đã ghi URL vào {DEPLOYMENT_FILE.name}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="ghi URL vào DEPLOYMENT.md")
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    url = wait_for_public_url(deadline)
    if url is None:
        print(
            f"Tunnel chưa phục vụ được sau {args.timeout:.0f}s (hostname hiện tại: "
            f"{fetch_hostname(time.monotonic() + 10)}). Nếu trình duyệt mở được mà máy "
            "này không, có thể DNS của nhà mạng chậm — thử lại sau ít phút."
        )
        return 1
    print(url)
    print("Tunnel đang phục vụ: /health → 200")

    if args.write:
        write_deployment(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
