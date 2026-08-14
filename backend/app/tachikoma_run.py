"""Local connection-layer launcher with a PORT override.

This file is intentionally separate from Docker/Compose production startup.
Running it is an explicit operator action; importing it starts nothing.
"""
from __future__ import annotations

import os

import uvicorn

from app.tachikoma_connection import configured_port


def main() -> None:
    os.environ.setdefault("PANSE_TACHIKOMA_CONNECTION_ONLY", "1")
    os.environ.setdefault("DISABLE_WATCHDOG", "1")
    os.environ.setdefault("DISABLE_SCHEDULER", "1")
    os.environ.setdefault("ENABLE_FEISHU_BOT", "0")
    os.environ.setdefault("PANSE_DISABLE_NOTIFY", "1")
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=configured_port(),
        reload=False,
    )


if __name__ == "__main__":
    main()
