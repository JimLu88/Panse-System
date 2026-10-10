"""Dispatch-v1 local coordinator; no platform adapter or automatic live launch.

Uses the existing SendJournal and its shared OS lock. Drivers must implement
prepare (no send), commit (one immediate guarded send), and reconcile (one
read-only lookup). Legacy ExternalCommandDriver is deliberately incompatible.
Transport methods commit the server transaction BEFORE returning a response.
This module does not store chat text, ERP tokens, platform cookies or passwords.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import sqlite3
from typing import Callable, Protocol
import time

from .send_journal import SendBlocked, SendJournal


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def aware(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise SendBlocked("时间证据缺少时区")
    return result.astimezone(timezone.utc)


class WindowsPermitProtector:
    """DPAPI current-user encryption, UI forbidden. Never plaintext fallback."""
    def _crypt(self, data: bytes, *, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise SendBlocked("真实本机许可持久化需要 Windows DPAPI")

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source, target = Blob(len(data), buffer), Blob()
        fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise SendBlocked("本机许可加解密失败；禁止发送或改用明文")
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            kernel.LocalFree(target.pbData)

    def seal(self, value: str) -> bytes:
        return self._crypt(value.encode(), decrypt=False)

    def open(self, value: bytes) -> str:
        return self._crypt(value, decrypt=True).decode()


class DispatchTransport(Protocol):
    def reserve(self, action: dict) -> dict: ...
    def permit(self, intent_id: str) -> dict: ...
    def unknown(self, intent_id: str) -> dict: ...
    def begin_reconcile(self, intent_id: str) -> dict: ...
    def unresolved(self, intent_id: str) -> dict: ...
    def confirm(self, intent_id: str, receipt: dict, permit: str) -> dict: ...


class BoundedDriver(Protocol):
    protocol: str
    def prepare(self, action: dict) -> dict: ...
    def commit(self, action: dict, before_click: Callable[[dict], None]) -> dict: ...
    def reconcile(self, action: dict) -> dict | None: ...


class BoundedDispatcher:
    """One local input owner. Restart recovery NEVER invokes commit/permit."""
    def __init__(self, journal: SendJournal, transport: DispatchTransport, *,
                 protector=None, monotonic=time.monotonic, stopped=lambda: False):
        self.journal, self.transport = journal, transport
        self.protector = protector or WindowsPermitProtector()
        self.monotonic, self.stopped = monotonic, stopped
        with journal._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS bounded_outbox (
                intent_key TEXT PRIMARY KEY REFERENCES send_attempts(intent_key),
                action_hash TEXT NOT NULL, payload_hash TEXT NOT NULL,
                server_intent TEXT UNIQUE, phase TEXT NOT NULL,
                permit_cipher BLOB, receipt_json TEXT,
                reconcile_used INTEGER NOT NULL DEFAULT 0 CHECK(reconcile_used IN (0,1))
            )""")

    def _key(self, action):
        return self.journal._identity(action)[0]

    def _validate(self, action):
        self._key(action)
        for key in ("account_id", "executor_id", "input_group", "channel",
                    "account_identity_hash", "merchant_external_id", "suggested_message", "deadline_at"):
            if not isinstance(action.get(key), str) or not action[key].strip():
                raise SendBlocked("发送动作缺少冻结字段：" + key)
        if action["channel"] not in {"taobao", "1688", "xiaohongshu"}:
            raise SendBlocked("平台尚未接入")
        if len(action["account_identity_hash"]) != 64:
            raise SendBlocked("账号摘要无效")
        aware(action["deadline_at"])
        # Extra driver hints never alter the frozen send identity.
        fields = {k: action[k] for k in ("task_id", "inquiry_id", "action_key", "account_id",
                  "executor_id", "input_group", "channel", "account_identity_hash",
                  "merchant_external_id", "suggested_message", "deadline_at")}
        return digest(json.dumps(fields, sort_keys=True, ensure_ascii=False)), digest(action["suggested_message"])

    def _row(self, action):
        with self.journal._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM bounded_outbox WHERE intent_key=?", (self._key(action),)).fetchone()
        if row and row["action_hash"] != self._validate(action)[0]:
            raise SendBlocked("本机意图冻结字段不匹配，不得换收件人或正文")
        return dict(row) if row else None

    def _save(self, action, phase, *, server_intent=None, cipher=None, receipt=None, reconcile_used=None):
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE bounded_outbox SET phase=?,
                server_intent=COALESCE(?,server_intent), permit_cipher=COALESCE(?,permit_cipher),
                receipt_json=COALESCE(?,receipt_json), reconcile_used=COALESCE(?,reconcile_used)
                WHERE intent_key=?""", (phase, server_intent, cipher,
                json.dumps(receipt) if receipt else None, reconcile_used, self._key(action)))

    def status(self, action):
        row = self._row(action)
        return {k: row[k] for k in ("phase", "server_intent", "reconcile_used")} if row else None

    def _identity(self, action, proof):
        if not isinstance(proof, dict) or any(proof.get(k) != action[k] for k in (
            "channel", "account_identity_hash", "merchant_external_id")):
            raise SendBlocked("平台回读账号或当前商家不匹配")
        if proof.get("input_owned") is not True or proof.get("access_gate") is not False:
            raise SendBlocked("输入归属未确认或平台要求人工验证")

    def _receipt(self, action, proof):
        self._identity(action, proof)
        if proof.get("direction") != "outbound":
            raise SendBlocked("入站消息或方向未知不能作为发送成功证据")
        external = proof.get("external_message_id")
        if (not isinstance(external, str) or not external.strip() or len(external) > 255
                or external.lower().startswith(("manual-review-", "human-review-"))):
            raise SendBlocked("缺少真实平台消息标识")
        if proof.get("sent_content") != action["suggested_message"]:
            raise SendBlocked("平台正文与原意图不一致")
        aware(proof.get("observed_at", ""))
        # Durable receipt contains no message body.
        return {"external_message_id": external, "observed_at": proof["observed_at"],
                "payload_hash": digest(proof["sent_content"])}

    def _ack(self, action):
        row = self._row(action)
        if row["phase"] == "confirmed":
            return self.status(action)
        if row["phase"] != "receipt" or not row["receipt_json"] or not row["permit_cipher"]:
            raise SendBlocked("没有可补记的持久回执")
        receipt = json.loads(row["receipt_json"])
        response = self.transport.confirm(row["server_intent"], receipt, self.protector.open(row["permit_cipher"]))
        if response.get("state") != "confirmed_sent" or response.get("intent_id") != row["server_intent"]:
            raise SendBlocked("服务端尚未确认原发送结果")
        # Atomic local finalization; acknowledgement loss retries this only.
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE bounded_outbox SET phase='confirmed', permit_cipher=NULL WHERE intent_key=?", (self._key(action),))
            conn.execute("UPDATE send_attempts SET state='confirmed', updated_at=CURRENT_TIMESTAMP WHERE intent_key=?", (self._key(action),))
        return self.status(action)

    def send_once(self, action, driver: BoundedDriver):
        action_hash, payload_hash = self._validate(action)
        if getattr(driver, "protocol", None) != "dispatch-v1":
            raise SendBlocked("旧驱动不能接收短期发送许可")
        with self.journal.execution_lock():
            if self.stopped():
                raise SendBlocked("本机已停止发送")
            if self._row(action):
                raise SendBlocked("已有本机意图；只允许恢复，不得重发")
            self.journal.begin(action)
            with self.journal._connect() as conn:
                conn.execute("INSERT INTO bounded_outbox(intent_key,action_hash,payload_hash,phase) VALUES (?,?,?,'prepared')",
                             (self._key(action), action_hash, payload_hash))
            try:
                reserved = self.transport.reserve(action)
                if (reserved.get("state") != "reserved" or reserved.get("payload_hash") != payload_hash
                        or not isinstance(reserved.get("intent_id"), str) or not reserved["intent_id"]):
                    raise SendBlocked("服务端意图不是相同内容的新预留")
                self._save(action, "reserved", server_intent=reserved["intent_id"])
                if any(reserved.get(k) != action[k] for k in (
                    "task_id", "inquiry_id", "action_key", "account_id", "executor_id", "input_group",
                    "channel", "account_identity_hash", "merchant_external_id", "deadline_at"
                )):
                    raise SendBlocked("本机发送对象与服务端冻结身份不一致")
                self._identity(action, driver.prepare(action))  # No send allowed.
                if self.stopped():
                    raise SendBlocked("本机已停止发送")
                # Record BEFORE asking; a lost grant never triggers another request.
                self._save(action, "permit_requested")
                requested = self.monotonic()
                grant = self.transport.permit(reserved["intent_id"])
                lifetime = (aware(grant["expires_at"]) - aware(grant["server_time"])).total_seconds()
                if (grant.get("protocol") != "dispatch-v1" or grant.get("intent_id") != reserved["intent_id"]
                        or grant.get("payload_hash") != payload_hash or not 0 < lifetime <= 5
                        or grant.get("requires_local_durable_fence") is not True
                        or not isinstance(grant.get("permit"), str) or not grant["permit"]
                        or aware(grant["expires_at"]) > aware(action["deadline_at"])):
                    raise SendBlocked("许可身份、内容或有效期不合格")
                self._save(action, "executing", cipher=self.protector.seal(grant["permit"]))
                called = False

                def before_click(fresh_identity):
                    nonlocal called
                    self._identity(action, fresh_identity)
                    elapsed = self.monotonic() - requested
                    if called or self.stopped() or not math.isfinite(elapsed) or not 0 <= elapsed < lifetime - 0.1:
                        raise SendBlocked("单次许可已用、过期或本机已停止")
                    called = True

                # A real adapter MUST call this immediately before its sole
                # send primitive with a fresh account/target/input observation,
                # without await/navigation/delay after the check.
                proof = driver.commit(action, before_click)
                if not called:
                    raise SendBlocked("驱动未执行最终发送检查")
                receipt = self._receipt(action, proof)
                self._save(action, "receipt", receipt=receipt)
                return self._ack(action)
            except Exception:
                row = self._row(action)
                # Never downgrade a saved platform receipt when callback fails.
                if row and row["phase"] not in {"receipt", "confirmed"}:
                    self._save(action, "unknown")
                    self.journal.mark(action, "unknown")
                    if row["server_intent"]:
                        try:
                            self.transport.unknown(row["server_intent"])
                        except Exception:
                            pass  # Durable unknown remains the recovery source.
                raise

    def recover_once(self, action, driver: BoundedDriver):
        self._validate(action)
        with self.journal.execution_lock():
            self.journal.require_only(action)
            row = self._row(action)
            if not row:
                raise SendBlocked("没有本机原意图；不能用恢复新建发送")
            if row["phase"] in {"receipt", "confirmed"}:
                return self._ack(action)  # No platform read/click.
            if row["reconcile_used"]:
                # Process loss after lookup reservation consumes the one chance.
                if row["phase"] == "reconciling":
                    self._save(action, "unresolved")
                    self.transport.unresolved(row["server_intent"])
                raise SendBlocked("一次核对已用，仍未确认；需向用户汇报")
            if not row["permit_cipher"] or not row["server_intent"]:
                raise SendBlocked("发送许可未完整落盘；禁止点击或自动重发，需人工核对")
            if getattr(driver, "protocol", None) != "dispatch-v1":
                raise SendBlocked("没有兼容的只读核对驱动")
            self.transport.unknown(row["server_intent"])
            # Both local and server reservations committed before platform read.
            self._save(action, "reconciling", reconcile_used=1)
            response = self.transport.begin_reconcile(row["server_intent"])
            if response.get("state") != "reconciling" or response.get("intent_id") != row["server_intent"]:
                raise SendBlocked("服务端未确认核对占位")
            try:
                proof = driver.reconcile(action)
                receipt = self._receipt(action, proof) if proof else None
                if receipt:
                    self._save(action, "receipt", receipt=receipt)
                    return self._ack(action)
            except Exception:
                if self._row(action)["phase"] == "receipt":
                    raise  # Only callback retry remains, not another lookup.
                self._save(action, "unresolved")
                self.transport.unresolved(row["server_intent"])
                raise
            self._save(action, "unresolved")
            self.transport.unresolved(row["server_intent"])
            return self.status(action)
