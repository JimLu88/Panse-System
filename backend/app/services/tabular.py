"""统一表格读取: CSV / Excel(xlsx) → CSV 文本。

让所有"CSV 导入"自动也吃 Excel(用户要求: 所有 CSV 文件都必须也支持 Excel)。
- xlsx(zip, PK 头 / .xlsx/.xlsm 后缀): openpyxl 读首个有内容的 sheet → 写成 CSV 文本;
  日期/时间单元格格式化成 ISO 串(与各导入器的日期解析兼容);
- 其它: 当 CSV, 先 utf-8-sig 再 gbk 解码。
旧的 .xls(OLE 二进制)openpyxl 读不了 → 抛清晰错误提示另存为 .xlsx。
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Optional

_XLSX_MAGIC = b"PK\x03\x04"          # zip (xlsx/xlsm)
_XLS_MAGIC = b"\xd0\xcf\x11\xe0"     # OLE2 (老 .xls)


def looks_like_xlsx(content: bytes, filename: Optional[str] = None) -> bool:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return True
    if name.endswith(".csv"):
        return False
    return content[:4] == _XLSX_MAGIC


def is_legacy_xls(content: bytes, filename: Optional[str] = None) -> bool:
    name = (filename or "").lower()
    return name.endswith(".xls") or content[:4] == _XLS_MAGIC


def _fmt(cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, datetime):
        # 有时分秒 → 带时间; 纯 0 点 → 只日期
        if (cell.hour, cell.minute, cell.second) == (0, 0, 0):
            return cell.strftime("%Y-%m-%d")
        return cell.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(cell, date):
        return cell.strftime("%Y-%m-%d")
    if isinstance(cell, float):
        # 整数值的 float(如被 Excel 数字化的 订单号/流水号/金额)→ 不要写成科学计数 "1.2e+27",
        # 否则会污染 流水号/订单号 去重键。(注: >15 位的长号 openpyxl 已丢精度, 这是 Excel 固有问题,
        # 长号列建议保持文本格式或用 CSV; 此处至少避免科学计数串。)
        return str(int(cell)) if cell.is_integer() else repr(cell)
    return str(cell)


def _xlsx_to_csv(content: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    # 取第一个有数据的 sheet
    ws = next((s for s in wb.worksheets if s.max_row and s.max_row > 0), wb.active)
    out = io.StringIO()
    w = csv.writer(out)
    for row in ws.iter_rows(values_only=True):
        if all(c is None for c in row):
            continue
        w.writerow([_fmt(c) for c in row])
    return out.getvalue()


def to_csv_texts(content: bytes, filename: Optional[str] = None) -> list[tuple[str, str]]:
    """xlsx/csv → [(sheet名, CSV文本), ...] — 多 sheet 全读 (用户要求: 工厂对账单两个 sheet 都要用)。

    CSV 文件天然单 sheet, 返回一项。空 sheet 跳过。
    """
    if is_legacy_xls(content, filename) and not looks_like_xlsx(content, filename):
        raise ValueError("不支持老的 .xls 格式，请在 Excel 里『另存为 .xlsx』后再上传。")
    if not looks_like_xlsx(content, filename):
        return [("(CSV)", to_csv_text(content, filename))]
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    out: list[tuple[str, str]] = []
    for ws in wb.worksheets:
        if not ws.max_row or ws.max_row == 0:
            continue
        buf = io.StringIO()
        w = csv.writer(buf)
        has_data = False
        for row in ws.iter_rows(values_only=True):
            if all(c is None for c in row):
                continue
            has_data = True
            w.writerow([_fmt(c) for c in row])
        if has_data:
            out.append((ws.title, buf.getvalue()))
    return out or [("(空表)", "")]


def to_csv_text(content: bytes, filename: Optional[str] = None) -> str:
    """xlsx/csv 字节 → CSV 文本。给所有按 CSV 文本工作的导入器复用。"""
    if is_legacy_xls(content, filename) and not looks_like_xlsx(content, filename):
        raise ValueError("不支持老的 .xls 格式，请在 Excel 里『另存为 .xlsx』后再上传。")
    if looks_like_xlsx(content, filename):
        return _xlsx_to_csv(content)
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("gbk", errors="replace")


def read_header(content: bytes, filename: Optional[str] = None) -> list[str]:
    """读首行表头(规范化: 去空白)。用于按表头结构判表格类型。"""
    try:
        text = to_csv_text(content, filename)
    except Exception:
        return []
    for row in csv.reader(io.StringIO(text)):
        return [(c or "").replace(" ", "").replace("　", "").strip() for c in row]
    return []
