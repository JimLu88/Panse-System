from __future__ import annotations

import sqlite3
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apps.core.configs.base_settings import load_base_settings
from apps.core.crm.db import connect, init_db
from apps.core.crm.events import (
    ensure_brand_row,
    ensure_shop_row,
    register_shop_manual,
    update_shop_display_name,
)
from apps.core.crm.kb_import import (
    clear_kb_for_shop,
    import_kb_rows,
    parse_kb_import_any,
    write_kb_wide_marks_to_xlsx,
)
from apps.core.crm.shop_delete import delete_shop_cascade
from apps.core.crm.product_import import import_product_workbook
from apps.core.ai.llm_client import deep_analysis_api_configured
from apps.core.crm.kb_import_ai import (
    KbImportAIOutcome,
    KbWideImportAIOutcome,
    analyze_kb_rows_with_llm,
    analyze_kb_wide_hints_with_llm,
)
from apps.core.runtime_paths import default_sqlite_db_path

CLEAR_KB_PASSWORD = "56129268"


def _is_wide_kb_sheet(items: list[dict[str, str | None]]) -> bool:
    if not items or not isinstance(items[0], dict):
        return False
    return "product_anchor" in items[0]


def _sync_shop_display_from_shop_yaml(conn: sqlite3.Connection) -> None:
    """
    若 `configs/shops/*.yaml` 里配置了 shop_display_name，且库里显示名仍为默认（等于 shop_code 或整段 shop_id），
    则写回数据库，使下拉框显示「淘宝店名」而不是 demo_shop。
    若你已在「店铺管理」里改过显示名且不再等于编码，则不会被覆盖。
    """
    try:
        from apps.core.configs.loader import load_shop_config
        from apps.ui.shop_presets import list_shop_presets

        for _label, path in list_shop_presets():
            if not path.is_file():
                continue
            shop = load_shop_config(path)
            sid = (shop.shop_id or "").strip() or f"{shop.brand_id}:{shop.shop_code}"
            dn = (shop.shop_display_name or "").strip() or shop.shop_code
            row = conn.execute(
                "SELECT display_name, shop_code FROM shops WHERE shop_id = ?",
                (sid,),
            ).fetchone()
            if not row:
                continue
            cur_dn, code = (str(row[0] or "").strip(), str(row[1] or "").strip())
            if cur_dn == code or cur_dn == sid:
                conn.execute(
                    "UPDATE shops SET display_name = ? WHERE shop_id = ?",
                    (dn, sid),
                )
        conn.commit()
    except Exception:
        pass


class KBManagementView(QWidget):
    """按店铺展示知识库条目（kb_entries）；图库 gallery_entries / 产品 products 可由 SQLite 工具维护。"""

    shops_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._inner_page = QWidget()
        self._inner_page.setMinimumWidth(0)
        self._inner_page.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._scroll.setWidget(self._inner_page)
        layout = QVBoxLayout(self._inner_page)
        layout.setSpacing(12)

        outer.addWidget(self._scroll)

        layout.addWidget(QLabel("<h2>知识库 / 话术</h2>"))

        row = QHBoxLayout()
        row.addWidget(QLabel("店铺"))
        self.combo_shop = QComboBox()
        self.combo_shop.setMinimumWidth(160)
        self.combo_shop.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_refresh = QPushButton("刷新")
        self.btn_manage_shops = QPushButton("店铺管理…")
        self.btn_manage_shops.setToolTip(
            "修改下拉框中的店铺显示名称，或登记新店铺（内部 ID 为 品牌:店铺编码）。\n"
            "保存后会自动在 configs/shops 下生成千牛坐标配置文件（从模板复制），无需手拷 yaml。"
        )
        self.btn_import = QPushButton("导入话术…")
        self.btn_clear_kb = QPushButton("清空话术…")
        self.btn_clear_kb.setToolTip(
            "删除当前店铺下全部话术与向量；需两次确认并输入密码后方可执行。"
        )
        self.btn_import_products = QPushButton("导入产品知识库…")
        self.btn_import_products.setToolTip(
            "Excel .xlsx：表头需含「产品编码」；可选类目、畔色品名、淘宝链接、尺寸明细、"
            "可定制范围、SKU/SKU编码、文案、主材/辅材等。写入 products / product_skus。"
        )
        self.btn_build_vectors = QPushButton("生成向量")
        self.btn_build_vectors.setToolTip(
            "调用 OpenAI text-embedding-3-small 为全部话术生成向量。\n"
            "完成后检索将优先走向量相似度，大幅提升命中率。\n"
            "需在「设置中心」配置 OpenAI API Key。"
        )
        self.btn_import.setToolTip(
            "CSV / TXT：直接写入当前店铺。\n"
            "Excel（.xlsx）：\n"
            "• 若表头含「涉及产品」等宽表格式：A 列为产品锚定（不改写），"
            "将结合 B–G 列线索经 AI 逐批生成真实问法/答句，并把批注写回副本的 E、F 列；\n"
            "• 若为「问法 / 答」两列表：先经大模型合并重复问法、拆分复杂问法，"
            "「答」仅从表格原文回填；并给出话术意见。\n"
            "表头支持：问法、答（或 question / answer）；可选类型、start_at、end_at。"
        )
        row.addWidget(self.combo_shop)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_manage_shops)
        row.addWidget(self.btn_import)
        row.addWidget(self.btn_clear_kb)
        row.addWidget(self.btn_import_products)
        row.addWidget(self.btn_build_vectors)
        row.addStretch(1)
        layout.addLayout(row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["问法/标题", "答/内容", "类型", "有效期"])
        self.table.setMinimumHeight(280)
        layout.addWidget(self.table)

        self.combo_shop.currentIndexChanged.connect(self._fill_table)
        self.btn_refresh.clicked.connect(self._reload)
        self.btn_manage_shops.clicked.connect(self._open_shop_manager_dialog)
        self.btn_import.clicked.connect(self._import_kb_file)
        self.btn_clear_kb.clicked.connect(self._clear_kb_entries)
        self.btn_import_products.clicked.connect(self._import_product_workbook)
        self.btn_build_vectors.clicked.connect(self._build_vectors)
        self._reload()

        QTimer.singleShot(0, self._sync_inner_scroll_width)

    def _sync_inner_scroll_width(self) -> None:
        vp = self._scroll.viewport().width()
        w = max(vp, self._scroll.width(), self.width())
        if w > 0:
            self._inner_page.setFixedWidth(w)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._sync_inner_scroll_width()

    def _conn(self) -> sqlite3.Connection:
        conn = connect(default_sqlite_db_path())
        init_db(conn)
        return conn

    def _reload(self) -> None:
        self.combo_shop.blockSignals(True)
        self.combo_shop.clear()
        try:
            conn = self._conn()
            _sync_shop_display_from_shop_yaml(conn)
            rows = conn.execute(
                "SELECT shop_id, brand_id, display_name FROM shops ORDER BY created_at DESC"
            ).fetchall()
            if rows:
                for sid, bid, name in rows:
                    self.combo_shop.addItem(f"{name} ({bid}/{sid})", (bid, sid))
            else:
                kb_rows = conn.execute(
                    "SELECT DISTINCT brand_id, shop_id FROM kb_entries ORDER BY brand_id LIMIT 80"
                ).fetchall()
                for bid, sid in kb_rows:
                    self.combo_shop.addItem(f"{bid} / {sid}", (bid, sid))
            conn.close()
        except Exception:
            pass
        self.combo_shop.blockSignals(False)
        self.btn_manage_shops.setEnabled(True)
        self.btn_import.setEnabled(self.combo_shop.count() > 0)
        self.btn_clear_kb.setEnabled(self.combo_shop.count() > 0)
        self.btn_import_products.setEnabled(self.combo_shop.count() > 0)
        self.btn_build_vectors.setEnabled(self.combo_shop.count() > 0)
        if self.combo_shop.count():
            self._fill_table()

    def _open_shop_manager_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("店铺管理")
        dlg.resize(680, 460)
        lay = QVBoxLayout(dlg)
        lay.addWidget(
            QLabel(
                "<b>已有店铺</b><br/><small>修改第一列「显示名称」后点「保存显示名称」；"
                "第二列为内部 ID（导入话术、策略等按此区分），勿手改。"
                "第三列「删除」需两次确认并输入密码，将清空该店铺在本机的话术、产品、会话等全部关联数据。</small>"
            )
        )

        tbl = QTableWidget(0, 3)
        tbl.setHorizontalHeaderLabels(["显示名称", "内部店铺 ID", "操作"])
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        tbl.setColumnWidth(2, 80)

        def try_delete_shop(shop_id: str, display_name: str) -> None:
            r1 = QMessageBox.question(
                dlg,
                "删除店铺",
                f"将永久删除店铺「{display_name}」\n<code>{shop_id}</code>\n\n"
                "及其话术、产品、图库、会话等<b>本机数据库中的全部关联数据</b>，不可恢复。\n\n是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r1 != QMessageBox.StandardButton.Yes:
                return
            r2 = QMessageBox.question(
                dlg,
                "再次确认",
                f"请再次确认：删除后无法撤销。\n\n店铺：<b>{display_name}</b>\n内部 ID：<code>{shop_id}</code>",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r2 != QMessageBox.StandardButton.Yes:
                return
            pwd_dlg = QDialog(dlg)
            pwd_dlg.setWindowTitle("密码验证")
            fl = QFormLayout(pwd_dlg)
            fl.addRow(QLabel("请输入删除密码以执行："))
            le_pwd = QLineEdit()
            le_pwd.setEchoMode(QLineEdit.EchoMode.Password)
            fl.addRow("密码", le_pwd)
            bb_p = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            bb_p.accepted.connect(pwd_dlg.accept)
            bb_p.rejected.connect(pwd_dlg.reject)
            fl.addRow(bb_p)
            if pwd_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if (le_pwd.text() or "").strip() != CLEAR_KB_PASSWORD:
                QMessageBox.warning(dlg, "密码错误", "密码不正确，删除已取消。")
                return
            try:
                conn = self._conn()
                try:
                    delete_shop_cascade(conn, shop_id=shop_id)
                finally:
                    conn.close()
            except Exception as e:
                QMessageBox.critical(dlg, "删除失败", str(e))
                return
            QMessageBox.information(dlg, "已删除", "该店铺及关联数据已从本机数据库移除。")
            self._reload()
            load_table()
            self.shops_changed.emit()

        def load_table() -> None:
            conn = self._conn()
            rows = conn.execute(
                "SELECT display_name, shop_id FROM shops ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            tbl.setRowCount(len(rows))
            for i, (dn, sid) in enumerate(rows):
                sid_s, dn_s = str(sid), str(dn)
                it0 = QTableWidgetItem(dn_s)
                it1 = QTableWidgetItem(sid_s)
                it1.setFlags(it1.flags() & ~Qt.ItemFlag.ItemIsEditable)
                tbl.setItem(i, 0, it0)
                tbl.setItem(i, 1, it1)
                btn_del = QPushButton("删除…")
                btn_del.clicked.connect(
                    lambda _checked=False, s=sid_s, d=dn_s: try_delete_shop(s, d)
                )
                tbl.setCellWidget(i, 2, btn_del)

        load_table()
        lay.addWidget(tbl)

        def save_names() -> None:
            try:
                conn = self._conn()
                try:
                    for i in range(tbl.rowCount()):
                        it0 = tbl.item(i, 0)
                        it1 = tbl.item(i, 1)
                        if not it0 or not it1:
                            continue
                        dn = (it0.text() or "").strip()
                        sid = (it1.text() or "").strip()
                        if not dn or not sid:
                            continue
                        update_shop_display_name(conn, shop_id=sid, display_name=dn)
                finally:
                    conn.close()
            except Exception as e:
                QMessageBox.critical(dlg, "保存失败", str(e))
                return
            QMessageBox.information(dlg, "已保存", "显示名称已更新。")
            self._reload()
            load_table()
            self.shops_changed.emit()

        btn_save = QPushButton("保存显示名称更改")
        btn_save.clicked.connect(save_names)
        lay.addWidget(btn_save)

        lay.addWidget(
            QLabel(
                "<b>添加新店铺</b><br/><small>内部 ID 将生成为「品牌ID:店铺编码」。"
                "保存后程序会在 <code>configs/shops/</code> 自动生成对应的 .yaml（坐标初值为 0，"
                "请到工作台选该店托管前在配置文件里按屏幕校准取点）。</small>"
            )
        )
        form = QFormLayout()
        le_brand = QLineEdit()
        le_brand.setPlaceholderText("如 demo_brand")
        le_code = QLineEdit()
        le_code.setPlaceholderText("如 demo_shop")
        le_disp = QLineEdit()
        le_disp.setPlaceholderText("下拉框显示名，如：畔色天猫旗舰店")
        form.addRow("品牌 ID (brand_id)", le_brand)
        form.addRow("店铺编码 (shop_code)", le_code)
        form.addRow("显示名称", le_disp)
        lay.addLayout(form)

        def add_shop() -> None:
            try:
                conn = self._conn()
                try:
                    register_shop_manual(
                        conn,
                        brand_id=le_brand.text(),
                        shop_code=le_code.text(),
                        display_name=le_disp.text(),
                    )
                finally:
                    conn.close()
            except Exception as e:
                QMessageBox.warning(dlg, "无法添加", str(e))
                return
            le_brand.clear()
            le_code.clear()
            le_disp.clear()
            QMessageBox.information(
                dlg,
                "已添加",
                "新店铺已登记。导入话术、产品库前请在下拉框中选择该店铺。",
            )
            self._reload()
            load_table()
            self.shops_changed.emit()

        btn_add = QPushButton("添加店铺")
        btn_add.clicked.connect(add_shop)
        lay.addWidget(btn_add)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.accept)
        lay.addWidget(btn_close)

        dlg.exec()

    def _current_brand_shop(self) -> tuple[str, str] | None:
        idx = self.combo_shop.currentIndex()
        if idx < 0:
            return None
        data = self.combo_shop.currentData()
        if not data:
            return None
        bid, sid = data
        return str(bid), str(sid)

    def _show_kb_import_advice_dialog(
        self,
        *,
        n: int,
        bid: str,
        sid: str,
        outcome: KbImportAIOutcome | KbWideImportAIOutcome,
        marked_path: str | None = None,
        mark_error: str | None = None,
        wide_sheet: bool = False,
    ) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("导入完成 · 话术意见")
        dlg.resize(720, 520)
        lay = QVBoxLayout(dlg)
        meta = (
            f"<p>已为当前店铺写入 <b>{n}</b> 条话术（店铺：<code>{bid}</code> / <code>{sid}</code>）。"
            f"<br/>AI 分析：{'已调用模型' if outcome.used_ai else '已回退本地规则'}。"
            "</p>"
        )
        if wide_sheet:
            meta += (
                "<p><small>宽表导入：问法/答句由模型根据 A 列产品与 E–G 线索生成，"
                "<code>kb_tags</code> 与 A 列产品锚定一致。</small></p>"
            )
        if marked_path:
            meta += f"<p>已写出批注副本：<br/><code>{marked_path}</code>（已写入 E、F 列批注）</p>"
        if mark_error:
            meta += f"<p><small>批注写回失败：{mark_error}</small></p>"
        if outcome.note:
            meta += f"<p><small>提示：{outcome.note}</small></p>"
        lay.addWidget(QLabel(meta))
        lay.addWidget(QLabel("<b>话术意见（仅供参考，回复正文以表格为准）</b>"))
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(outcome.advice or "（模型未返回话术意见。）")
        lay.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def _import_product_workbook(self) -> None:
        pair = self._current_brand_shop()
        if not pair:
            QMessageBox.warning(self, "无法导入", "请先在下拉框中选择店铺。")
            return
        bid, sid = pair
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "导入产品知识库",
            "",
            "Excel (*.xlsx *.xlsm);;所有文件 (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            conn = self._conn()
            try:
                ensure_brand_row(conn, brand_id=bid)
                ensure_shop_row(
                    conn,
                    brand_id=bid,
                    shop_id=sid,
                    shop_code=sid.split(":", 1)[-1] if ":" in sid else sid,
                    display_name=sid,
                )
                n_p, n_s = import_product_workbook(path, conn, brand_id=bid, shop_id=sid)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return
        QMessageBox.information(
            self,
            "导入完成",
            f"产品行（含更新）：<b>{n_p}</b>；SKU 行：<b>{n_s}</b>（店铺：{bid} / {sid}）。",
        )

    def _clear_kb_entries(self) -> None:
        pair = self._current_brand_shop()
        if not pair:
            QMessageBox.warning(self, "无法清空", "请先在下拉框中选择店铺。")
            return
        bid, sid = pair
        r1 = QMessageBox.question(
            self,
            "清空话术",
            "将删除当前店铺下的<b>全部话术</b>及关联向量，操作不可恢复。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r1 != QMessageBox.StandardButton.Yes:
            return
        r2 = QMessageBox.question(
            self,
            "再次确认",
            "请再次确认：<b>删除后无法撤销</b>，确定清空当前店铺的全部话术？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r2 != QMessageBox.StandardButton.Yes:
            return

        pwd_dlg = QDialog(self)
        pwd_dlg.setWindowTitle("密码验证")
        fl = QFormLayout(pwd_dlg)
        fl.addRow(QLabel("请输入清空密码以执行删除："))
        le = QLineEdit()
        le.setEchoMode(QLineEdit.EchoMode.Password)
        fl.addRow("密码", le)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(pwd_dlg.accept)
        bb.rejected.connect(pwd_dlg.reject)
        fl.addRow(bb)
        if pwd_dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if (le.text() or "").strip() != CLEAR_KB_PASSWORD:
            QMessageBox.warning(self, "密码错误", "密码不正确，清空已取消。")
            return

        try:
            conn = self._conn()
            try:
                n = clear_kb_for_shop(conn, brand_id=bid, shop_id=sid)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.critical(self, "清空失败", str(e))
            return
        QMessageBox.information(
            self,
            "已清空",
            f"已删除当前店铺话术 <b>{n}</b> 条（含向量）。",
        )
        self._fill_table()

    def _import_kb_file(self) -> None:
        pair = self._current_brand_shop()
        if not pair:
            QMessageBox.warning(self, "无法导入", "请先在下拉框中选择店铺。")
            return
        bid, sid = pair
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "导入话术文件",
            "",
            "表格 (*.csv *.tsv *.txt *.xlsx *.xlsm);;所有文件 (*.*)",
        )
        if not path_str:
            return
        path = Path(path_str)
        suffix = path.suffix.lower()

        try:
            items = parse_kb_import_any(path)
        except OSError as e:
            QMessageBox.critical(self, "读取失败", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "解析失败", str(e))
            return
        if not items:
            QMessageBox.warning(
                self,
                "未导入任何行",
                "文件中未解析到有效的「问法 + 答」行；请检查表头或两列格式。",
            )
            return

        if suffix in (".xlsx", ".xlsm"):
            st = load_base_settings()
            if not deep_analysis_api_configured(st):
                QMessageBox.warning(
                    self,
                    "需要配置深度模型",
                    "Excel 导入会使用「设置中心」中的「AI 陪伴与深度分析模型」及对应供应商密钥。\n"
                    "请填写 DeepSeek / DashScope / OpenAI / Anthropic / Gemini 中与所选模型匹配的密钥。",
                )
                return

            wide = _is_wide_kb_sheet(items)
            prog = QProgressDialog(self)
            prog.setLabelText(
                "正在调用大模型分析宽表话术…"
                if wide
                else "正在调用大模型整理话术（答句仅从表格原文回填，不由模型改写）…"
            )
            if wide:
                prog.setRange(0, max(1, len(items)))
                prog.setValue(0)
            else:
                prog.setRange(0, 0)
            prog.setCancelButton(None)
            prog.setModal(True)
            prog.setWindowModality(Qt.WindowModality.WindowModal)
            prog.setMinimumDuration(0)
            prog.show()
            QApplication.processEvents()

            marked_path: str | None = None
            mark_error: str | None = None

            try:
                if wide:

                    def _on_wide(cur: int, tot: int) -> None:
                        prog.setMaximum(max(1, tot))
                        prog.setValue(min(cur, tot))
                        prog.setLabelText(f"宽表话术 AI 分块分析 {cur}/{tot}…")
                        QApplication.processEvents()

                    outcome = analyze_kb_wide_hints_with_llm(
                        settings=st, wide_rows=items, on_progress=_on_wide
                    )
                else:
                    outcome = analyze_kb_rows_with_llm(settings=st, rows=items)
            finally:
                prog.close()

            if not outcome.rows:
                QMessageBox.warning(self, "无可导入行", "整理后为空，未写入数据库。")
                return

            try:
                conn = self._conn()
                try:
                    n = import_kb_rows(
                        conn, brand_id=bid, shop_id=sid, rows=outcome.rows
                    )
                finally:
                    conn.close()
            except Exception as e:
                QMessageBox.critical(self, "写入数据库失败", str(e))
                return

            if wide and isinstance(outcome, KbWideImportAIOutcome):
                try:
                    marked_path = str(write_kb_wide_marks_to_xlsx(path, outcome.marks))
                except Exception as e:
                    mark_error = str(e)

            self._show_kb_import_advice_dialog(
                n=n,
                bid=bid,
                sid=sid,
                outcome=outcome,
                marked_path=marked_path,
                mark_error=mark_error,
                wide_sheet=wide,
            )
            self._fill_table()
            return

        try:
            conn = self._conn()
            try:
                n = import_kb_rows(conn, brand_id=bid, shop_id=sid, rows=items)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.critical(self, "写入数据库失败", str(e))
            return
        QMessageBox.information(
            self,
            "导入完成",
            f"已为当前店铺写入 <b>{n}</b> 条话术（店铺：{bid} / {sid}）。",
        )
        self._fill_table()

    def _build_vectors(self) -> None:
        pair = self._current_brand_shop()
        if not pair:
            QMessageBox.warning(self, "无法生成", "请先在下拉框中选择店铺。")
            return
        bid, sid = pair

        st = load_base_settings()
        if not (st.openai_api_key or "").strip():
            QMessageBox.warning(
                self,
                "未配置 OpenAI Key",
                "「生成向量」需要 OpenAI API Key。\n\n请到「设置中心 → 接入配置」填写 OpenAI API Key 后重试。",
            )
            return

        prog = QProgressDialog("正在生成话术向量（第 0 / ? 条）…", None, 0, 0, self)
        prog.setWindowTitle("生成向量中")
        prog.setWindowModality(Qt.WindowModality.ApplicationModal)
        prog.setMinimumDuration(0)
        prog.show()
        QApplication.processEvents()

        try:
            from apps.core.ai.kb_vector import build_kb_vectors

            def _progress(done: int, total: int) -> None:
                prog.setMaximum(total)
                prog.setValue(done)
                prog.setLabelText(f"正在生成话术向量（第 {done} / {total} 条）…")
                QApplication.processEvents()

            conn = self._conn()
            try:
                result = build_kb_vectors(
                    conn,
                    brand_id=bid,
                    shop_id=sid,
                    settings=st,
                    progress_cb=_progress,
                )
            finally:
                conn.close()
        except Exception as e:
            prog.close()
            QMessageBox.critical(self, "向量生成失败", str(e))
            return
        prog.close()

        if result.all_ok:
            QMessageBox.information(
                self,
                "向量生成完成",
                f"全部 <b>{result.done}</b> 条话术向量生成成功！\n"
                "今后回复将优先使用向量相似度匹配，命中率显著提升。",
            )
        elif result.has_errors and result.done == 0:
            # 全部失败
            err_text = "\n".join(f"  • {e}" for e in result.errors)
            QMessageBox.critical(
                self,
                "向量生成失败",
                f"全部 {result.skipped} 条均未能生成向量，请检查 OpenAI API Key 或网络。\n\n"
                f"失败详情：\n{err_text}",
            )
        else:
            # 部分失败
            err_text = "\n".join(f"  • {e}" for e in result.errors)
            dlg = QDialog(self)
            dlg.setWindowTitle("向量生成完成（部分失败）")
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.resize(560, 360)
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel(
                f"<b>成功</b>：{result.done} 条　"
                f"<b style='color:red'>失败</b>：{result.skipped} 条\n\n"
                "已成功的部分已存入向量库，失败批次的话术仍会使用字符串相似度兜底。\n"
                "可重新点击「生成向量」对失败批次进行补充生成（已成功的不会重复计费）。"
            ))
            te = QTextEdit()
            te.setReadOnly(True)
            te.setPlainText(f"失败批次详情：\n{err_text}")
            lay.addWidget(te)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            bb.accepted.connect(dlg.accept)
            lay.addWidget(bb)
            dlg.exec()

    def _fill_table(self) -> None:
        self.table.setRowCount(0)
        idx = self.combo_shop.currentIndex()
        if idx < 0:
            return
        data = self.combo_shop.currentData()
        if not data:
            return
        bid, sid = data
        conn = self._conn()
        cur = conn.execute(
            """
            SELECT question, answer, entry_type,
                   COALESCE(start_at,'') || ' ~ ' || COALESCE(end_at,'')
            FROM kb_entries WHERE brand_id = ? AND shop_id = ?
            ORDER BY updated_at DESC LIMIT 1000
            """,
            (bid, sid),
        )
        for qu, an, et, dr in cur.fetchall():
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(str(qu)[:120]))
            self.table.setItem(r, 1, QTableWidgetItem(str(an)[:200]))
            self.table.setItem(r, 2, QTableWidgetItem(str(et)))
            self.table.setItem(r, 3, QTableWidgetItem(str(dr)))
        conn.close()
