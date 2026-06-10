"""Server酱 / PushPlus / 企微 Webhook 推送（标准库 urllib，无额外依赖）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from apps.core.configs.base_settings import BaseSettings


@dataclass(frozen=True, slots=True)
class PushResult:
    ok: bool
    channel: str
    detail: str


def _post_form(url: str, data: dict[str, str], timeout_s: float = 12.0) -> tuple[bool, str]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, raw[:500]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {(e.read() or b'').decode('utf-8', errors='replace')[:300]}"
    except Exception as e:
        return False, repr(e)


def _post_json(url: str, payload: dict, timeout_s: float = 12.0) -> tuple[bool, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=UTF-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, raw[:500]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {(e.read() or b'').decode('utf-8', errors='replace')[:300]}"
    except Exception as e:
        return False, repr(e)


def push_serverchan(*, sendkey: str, title: str, body: str) -> PushResult:
    key = (sendkey or "").strip()
    if not key:
        return PushResult(False, "serverchan", "empty key")
    url = f"https://sctapi.ftqq.com/{urllib.parse.quote(key)}.send"
    ok, detail = _post_form(url, {"title": title[:99], "desp": body[:32000]})
    return PushResult(ok, "serverchan", detail)


def push_pushplus(*, token: str, title: str, body: str) -> PushResult:
    tok = (token or "").strip()
    if not tok:
        return PushResult(False, "pushplus", "empty token")
    payload = {"token": tok, "title": title[:99], "content": body[:20000], "template": "txt"}
    ok, detail = _post_json("https://www.pushplus.plus/send", payload)
    return PushResult(ok, "pushplus", detail)


def push_host_http(*, alert_url: str, title: str, body: str) -> PushResult:
    """POST JSON 到自定义 URL（宿主机蜂鸣/自建转发等；服务端可忽略 body 结构）。"""
    url = (alert_url or "").strip()
    if not url:
        return PushResult(False, "host_http", "empty url")
    payload = {
        "title": title[:500],
        "body": body[:32000],
        "source": "panse_workbench",
    }
    ok, detail = _post_json(url, payload)
    return PushResult(ok, "host_http", detail)


def push_wecom_webhook(*, webhook_url: str, text: str) -> PushResult:
    url = (webhook_url or "").strip()
    if not url:
        return PushResult(False, "wecom", "empty webhook")
    payload = {"msgtype": "text", "text": {"content": text[:4000]}}
    ok, detail = _post_json(url, payload)
    return PushResult(ok, "wecom", detail)


def push_all(settings: BaseSettings, *, title: str, body: str) -> list[PushResult]:
    """按配置并行尝试所有已填写的通道。"""
    results: list[PushResult] = []
    full = f"{title}\n\n{body}"
    if settings.push_serverchan_sendkey.strip():
        results.append(push_serverchan(sendkey=settings.push_serverchan_sendkey, title=title, body=body))
    if settings.push_pushplus_token.strip():
        results.append(push_pushplus(token=settings.push_pushplus_token, title=title, body=body))
    if settings.push_wecom_webhook.strip():
        results.append(push_wecom_webhook(webhook_url=settings.push_wecom_webhook, text=full))
    if settings.push_host_alert_url.strip():
        results.append(
            push_host_http(
                alert_url=settings.push_host_alert_url,
                title=title,
                body=body,
            )
        )
    return results
