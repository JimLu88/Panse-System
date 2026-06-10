#!/usr/bin/env python3
"""
宿主机强提醒接收器（示例）：HTTP POST 任意 JSON 时在 Windows 上蜂鸣一声。

用法（物理机）::
    python scripts/host_strong_alert_listener.py --port 9777

在「设置中心」推送或自建转发里把 Webhook 指到 http://<本机IP>:9777/ping
（需自行在防火墙放行）。VM 内应用仍走 Server酱/企微等；本脚本仅作本地声音补充。
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def _beep() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        print("\a", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/ping", "/"):
            self.send_error(404)
            return
        ln = int(self.headers.get("Content-Length") or 0)
        if ln > 65536:
            self.send_error(413)
            return
        _ = self.rfile.read(ln)
        _beep()
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "hint": "POST /ping"}).encode("utf-8"))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9777)
    args = p.parse_args()
    httpd = HTTPServer((args.host, args.port), Handler)
    print(f"listening http://{args.host}:{args.port} (POST /ping -> beep)", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
