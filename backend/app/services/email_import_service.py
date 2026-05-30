"""邮箱轮询自动导入支付宝流水 CSV。

工作流程:
1. 读取 settings 中的 IMAP 配置 (email_imap_host / port / user / pass / folder)
2. 连接 IMAP 服务器，在指定文件夹中搜索未读的支付宝账单邮件
3. 提取 CSV/ZIP 附件 → 调用 alipay_import.import_alipay_csv
4. 同步完成后将邮件标记为已读，避免重复导入
5. 返回汇总 {scanned, imported, skipped, errors}

每 6 小时由调度器调用 poll_and_import(db)。
也可以通过 POST /api/finance/email-poll/trigger 手动触发。

IMAP 配置 (通过 /api/settings 或环境变量配置):
    email_imap_host       IMAP 服务器地址  (如 imap.qq.com / imap.163.com)
    email_imap_port       993 (SSL) 或 143
    email_imap_ssl        true/false (默认 true)
    email_username        登录账号
    email_password        登录密码 / 授权码
    email_folder          搜索文件夹 (默认 INBOX)
    email_subject_filter  邮件主题关键词 (默认 "支付宝")
    email_sender_filter   发件人过滤  (默认 "notify@pay.alipay.com")
    email_alipay_account  导入时使用的账户名 (默认 "企业号")
"""
from __future__ import annotations

import email
import email.header
import imaplib
import logging
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.email_import")


@dataclass
class EmailPollReport:
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _get_cfg(db: Session) -> dict:
    def _s(k, default=""):
        return settings_service.get(db, k) or default

    return {
        "host": _s("email_imap_host"),
        "port": int(_s("email_imap_port", "993")),
        "ssl": _s("email_imap_ssl", "true").lower() != "false",
        "user": _s("email_username"),
        "password": _s("email_password"),
        "folder": _s("email_folder", "INBOX"),
        "subject_filter": _s("email_subject_filter", "支付宝"),
        "sender_filter": _s("email_sender_filter", "notify@pay.alipay.com"),
        "alipay_account": _s("email_alipay_account", "企业号"),
    }


def _decode_header_value(v: str) -> str:
    parts = email.header.decode_header(v)
    result = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            result.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(chunk)
    return "".join(result)


def _extract_csv_texts(msg: email.message.Message) -> list[tuple[str, str]]:
    """返回 [(filename, csv_text), ...], 支持直接附件和 ZIP 包内的 CSV。"""
    results = []
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if "attachment" not in cd:
            continue
        filename_raw = part.get_filename() or ""
        filename = _decode_header_value(filename_raw)
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        fname_lower = filename.lower()
        if fname_lower.endswith(".csv"):
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    results.append((filename, raw.decode(enc)))
                    break
                except UnicodeDecodeError:
                    continue
        elif fname_lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(BytesIO(raw)) as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".csv"):
                            data = zf.read(name)
                            for enc in ("utf-8-sig", "gbk", "utf-8"):
                                try:
                                    results.append((name, data.decode(enc)))
                                    break
                                except UnicodeDecodeError:
                                    continue
            except zipfile.BadZipFile:
                pass
    return results


def poll_and_import(db: Session) -> EmailPollReport:
    """连接 IMAP 轮询未读支付宝流水邮件并自动导入。"""
    from app.services import alipay_import, smart_matching_service

    report = EmailPollReport()
    cfg = _get_cfg(db)
    if not cfg["host"] or not cfg["user"]:
        _logger.info("邮箱 IMAP 未配置，跳过轮询")
        return report

    try:
        if cfg["ssl"]:
            imap = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        else:
            imap = imaplib.IMAP4(cfg["host"], cfg["port"])
        imap.login(cfg["user"], cfg["password"])
    except Exception as exc:
        report.errors.append(f"IMAP 连接失败: {exc}")
        _logger.warning("IMAP 连接失败: %s", exc)
        return report

    try:
        imap.select(cfg["folder"])
        # 搜索未读邮件（如需过滤发件人可加 FROM 条件）
        _, msg_ids_raw = imap.search(None, "UNSEEN")
        msg_ids = msg_ids_raw[0].split() if msg_ids_raw[0] else []
        report.scanned = len(msg_ids)

        for mid in msg_ids:
            try:
                _, data = imap.fetch(mid, "(RFC822)")
                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                # 检查主题 / 发件人过滤
                subject = _decode_header_value(msg.get("Subject", ""))
                sender = msg.get("From", "")
                subject_ok = cfg["subject_filter"].lower() in subject.lower()
                sender_ok = (not cfg["sender_filter"]) or cfg["sender_filter"].lower() in sender.lower()
                if not (subject_ok or sender_ok):
                    report.skipped += 1
                    continue

                csv_texts = _extract_csv_texts(msg)
                if not csv_texts:
                    report.skipped += 1
                    continue

                imported_any = False
                for filename, text in csv_texts:
                    try:
                        r = alipay_import.import_alipay_csv(
                            db, text, account=cfg["alipay_account"],
                        )
                        smart_matching_service.run(db, account=cfg["alipay_account"])
                        db.flush()
                        report.imported += r.inserted
                        _logger.info("邮件 CSV '%s' 导入 %d 条", filename, r.inserted)
                        imported_any = True
                    except Exception as exc2:
                        report.errors.append(f"{filename}: {exc2}")
                        _logger.warning("导入失败 %s: %s", filename, exc2)

                if imported_any:
                    # 标记为已读，避免重复导入
                    imap.store(mid, "+FLAGS", "\\Seen")

            except Exception as exc:
                report.errors.append(f"处理邮件 {mid}: {exc}")
                _logger.warning("处理邮件失败 %s: %s", mid, exc)

        db.commit()
    finally:
        try:
            imap.close()
            imap.logout()
        except Exception:
            pass

    _logger.info("邮箱轮询完成: 扫描=%d 导入=%d 跳过=%d 错误=%d",
                 report.scanned, report.imported, report.skipped, len(report.errors))
    return report


def get_config(db: Session) -> dict:
    cfg = _get_cfg(db)
    return {
        "host": cfg["host"],
        "port": cfg["port"],
        "ssl": cfg["ssl"],
        "user": cfg["user"],
        "password_set": bool(cfg["password"]),
        "folder": cfg["folder"],
        "subject_filter": cfg["subject_filter"],
        "sender_filter": cfg["sender_filter"],
        "alipay_account": cfg["alipay_account"],
    }


def save_config(db: Session, **kwargs) -> None:
    allowed = {
        "email_imap_host", "email_imap_port", "email_imap_ssl",
        "email_username", "email_password", "email_folder",
        "email_subject_filter", "email_sender_filter", "email_alipay_account",
    }
    for k, v in kwargs.items():
        if k in allowed:
            settings_service.set_value(db, k, str(v))
    db.flush()
