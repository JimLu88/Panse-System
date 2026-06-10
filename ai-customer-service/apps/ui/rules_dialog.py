from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from apps.core.rules.bundle import (
    MATCH_OPTIONS,
    RulesBundle,
    RuleRow,
    TemplateRow,
    match_label_for_type,
    new_rule_id,
    new_template_id,
    parse_bundle_yaml,
    serialize_bundle_yaml,
)
from apps.core.rules.store import (
    default_rules_path,
    load_rules_yaml_text,
    save_rules_yaml_text,
    validate_rules_yaml,
)


GUIDE_TEXT = """您好，用这个窗口设置「什么情况给客户回哪句话」，不用写代码。

【第一步】看下面第一个表——「话术模板」
每一行是一条将要发给客户的完整句子。左边的「话术代号」是给电脑辨认用的英文名；不懂就别改，新增一行时会自动生成。

【第二步】看第二个表——「分支规则」
· 勾选「启用」表示这条规则生效。
· 「规则说明」只给您自己看，客户看不见。
· 「客户属于哪种情况」点开下拉框选一种（在范围内 / 超出范围）。
· 「商品Excel列名」填商品表里用来判断定制的列名；不清楚可以问同事，或先填 customizable_tags。
· 「使用哪条话术」选第一步里对应的那句话。

【重要】如果您在第一个表里新增或改了话术，请先点中间的按钮「刷新下方话术下拉框」，再检查第二个表里的选项是否正确。

最后点右下角「保存全部」。保存成功后规则文件会写到电脑上，程序自动回复时会按这里的设置来。"""


class RulesEditorDialog(QDialog):
    """表格化规则编辑，面向完全非技术操作员。"""

    def __init__(self, parent=None, *, rules_path: Path | None = None) -> None:
        super().__init__(parent)
        self._path = rules_path or default_rules_path()
        self._file_version = 1
        self.setWindowTitle("分支规则设置（简单表格）")
        self.resize(960, 720)

        root = QVBoxLayout(self)

        title = QLabel("分支规则设置")
        f = title.font()
        f.setPointSize(f.pointSize() + 2)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        guide = QTextEdit()
        guide.setReadOnly(True)
        guide.setPlainText(GUIDE_TEXT)
        guide.setMinimumHeight(160)
        guide.setStyleSheet("background:#f8f9fa; border:1px solid #dee2e6; border-radius:4px; padding:8px;")
        root.addWidget(guide)

        splitter = QSplitter(Qt.Orientation.Vertical)

        box_tpl = QGroupBox("① 话术模板（发给客户看的原话）")
        lt = QVBoxLayout(box_tpl)
        self._tbl_templates = QTableWidget(0, 2)
        self._tbl_templates.setHorizontalHeaderLabels(["话术代号（英文，新增可自动生成）", "发给客户的内容（必填）"])
        self._tbl_templates.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl_templates.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl_templates.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl_templates.setAlternatingRowColors(True)
        lt.addWidget(self._tbl_templates)
        row_tpl_btn = QHBoxLayout()
        btn_add_tpl = QPushButton("添加一条话术")
        btn_add_tpl.clicked.connect(self._add_template_row)
        row_tpl_btn.addWidget(btn_add_tpl)
        btn_del_tpl = QPushButton("删除所选话术")
        btn_del_tpl.clicked.connect(self._del_template_row)
        row_tpl_btn.addWidget(btn_del_tpl)
        row_tpl_btn.addStretch(1)
        lt.addLayout(row_tpl_btn)

        box_rules = QGroupBox("② 分支规则（什么情况用哪条话术）")
        lr = QVBoxLayout(box_rules)
        self._tbl_rules = QTableWidget(0, 5)
        self._tbl_rules.setHorizontalHeaderLabels(
            ["启用", "规则说明（仅自己看）", "客户属于哪种情况", "商品Excel列名（高级）", "使用哪条话术"]
        )
        self._tbl_rules.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl_rules.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl_rules.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tbl_rules.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl_rules.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._tbl_rules.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl_rules.setAlternatingRowColors(True)
        lr.addWidget(self._tbl_rules)
        row_rule_btn = QHBoxLayout()
        btn_add_rule = QPushButton("添加一条规则")
        btn_add_rule.clicked.connect(self._add_rule_row)
        row_rule_btn.addWidget(btn_add_rule)
        btn_del_rule = QPushButton("删除所选规则")
        btn_del_rule.clicked.connect(self._del_rule_row)
        row_rule_btn.addWidget(btn_del_rule)
        row_rule_btn.addStretch(1)
        lr.addLayout(row_rule_btn)
        btn_refresh = QPushButton("刷新下方「使用哪条话术」下拉框（改了话术模板后必点）")
        btn_refresh.clicked.connect(self._refresh_rule_combos)
        lr.addWidget(btn_refresh)

        splitter.addWidget(box_tpl)
        splitter.addWidget(box_rules)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        path_lab = QLabel(f"保存位置：{self._path}")
        path_lab.setWordWrap(True)
        path_lab.setStyleSheet("color:#555;")
        root.addWidget(path_lab)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_reload = QPushButton("重新载入文件")
        btn_reload.clicked.connect(self._reload)
        bottom.addWidget(btn_reload)
        btn_save = QPushButton("保存全部")
        btn_save.setStyleSheet("font-weight:bold; padding:6px 16px;")
        btn_save.clicked.connect(self._on_save)
        bottom.addWidget(btn_save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

        self._reload()

    def _reload(self) -> None:
        text = load_rules_yaml_text(self._path)
        bundle = parse_bundle_yaml(text)
        self._fill_templates(bundle.templates)
        self._fill_rules(bundle.rules)

    def _fill_templates(self, rows: list[TemplateRow]) -> None:
        self._tbl_templates.setRowCount(0)
        for r in rows:
            self._append_template_row(r.template_id, r.body)

    def _append_template_row(self, tid: str, body: str) -> None:
        i = self._tbl_templates.rowCount()
        self._tbl_templates.insertRow(i)
        self._tbl_templates.setItem(i, 0, QTableWidgetItem(tid))
        self._tbl_templates.setItem(i, 1, QTableWidgetItem(body))

    def _add_template_row(self) -> None:
        self._append_template_row(new_template_id(), "")

    def _del_template_row(self) -> None:
        r = self._tbl_templates.currentRow()
        if r >= 0:
            self._tbl_templates.removeRow(r)

    def _fill_rules(self, rows: list[RuleRow]) -> None:
        self._tbl_rules.setRowCount(0)
        for r in rows:
            self._append_rule_row(r)

    def _make_match_combo(self, current_type: str) -> QComboBox:
        cb = QComboBox()
        for _key, label in MATCH_OPTIONS:
            cb.addItem(label, _key)
        idx = cb.findData(current_type)
        if idx < 0:
            cb.insertItem(0, match_label_for_type(current_type), current_type)
            idx = 0
        cb.setCurrentIndex(idx)
        return cb

    def _make_template_combo(self, ids: list[str], current: str) -> QComboBox:
        cb = QComboBox()
        for tid in ids:
            cb.addItem(tid, tid)
        idx = cb.findData(current)
        if idx < 0 and current:
            cb.insertItem(0, current, current)
            idx = 0
        cb.setCurrentIndex(max(0, idx))
        return cb

    def _append_rule_row(self, r: RuleRow) -> None:
        i = self._tbl_rules.rowCount()
        self._tbl_rules.insertRow(i)

        en = QTableWidgetItem()
        en.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        en.setCheckState(Qt.CheckState.Checked if r.enabled else Qt.CheckState.Unchecked)
        self._tbl_rules.setItem(i, 0, en)

        desc_item = QTableWidgetItem(r.description)
        desc_item.setData(Qt.ItemDataRole.UserRole, r.rule_id)
        self._tbl_rules.setItem(i, 1, desc_item)

        self._tbl_rules.setCellWidget(i, 2, self._make_match_combo(r.match_type))

        pf = r.product_field or "customizable_tags"
        self._tbl_rules.setItem(i, 3, QTableWidgetItem(pf))

        ids = self._collect_template_ids()
        self._tbl_rules.setCellWidget(i, 4, self._make_template_combo(ids, r.reply_template_id))

    def _collect_template_ids(self) -> list[str]:
        out: list[str] = []
        for i in range(self._tbl_templates.rowCount()):
            it = self._tbl_templates.item(i, 0)
            if it is None:
                continue
            tid = (it.text() or "").strip()
            if tid:
                out.append(tid)
        return out

    def _add_rule_row(self) -> None:
        ids = self._collect_template_ids()
        pick = ids[0] if ids else ""
        self._append_rule_row(
            RuleRow(
                rule_id=new_rule_id(),
                enabled=True,
                description="",
                match_type=MATCH_OPTIONS[0][0],
                product_field="customizable_tags",
                reply_template_id=pick,
            )
        )

    def _del_rule_row(self) -> None:
        r = self._tbl_rules.currentRow()
        if r >= 0:
            self._tbl_rules.removeRow(r)

    def _refresh_rule_combos(self) -> None:
        ids = self._collect_template_ids()
        if not ids:
            QMessageBox.warning(self, "提示", "请先在「话术模板」里填写至少一条话术，并填写「话术代号」。")
            return
        for i in range(self._tbl_rules.rowCount()):
            w = self._tbl_rules.cellWidget(i, 4)
            current = ""
            if isinstance(w, QComboBox):
                current = str(w.currentData() or w.currentText() or "")
            self._tbl_rules.setCellWidget(i, 4, self._make_template_combo(ids, current))

    def _tables_to_bundle(self) -> RulesBundle | None:
        templates: list[TemplateRow] = []
        seen_ids: set[str] = set()
        for i in range(self._tbl_templates.rowCount()):
            it0 = self._tbl_templates.item(i, 0)
            it1 = self._tbl_templates.item(i, 1)
            tid = (it0.text() if it0 else "").strip()
            body = (it1.text() if it1 else "").strip()
            if not tid and not body:
                continue
            if not tid:
                tid = new_template_id()
                if it0:
                    it0.setText(tid)
            if tid in seen_ids:
                QMessageBox.warning(self, "错误", f"话术代号重复：{tid}\n请改成不同的英文名。")
                return None
            seen_ids.add(tid)
            templates.append(TemplateRow(tid, body))

        if not templates:
            QMessageBox.warning(self, "错误", "请至少填写一条话术模板（发给客户的内容）。")
            return None

        rules: list[RuleRow] = []
        valid_ids = {t.template_id for t in templates}

        for i in range(self._tbl_rules.rowCount()):
            en = self._tbl_rules.item(i, 0)
            desc_it = self._tbl_rules.item(i, 1)
            pf_it = self._tbl_rules.item(i, 3)

            enabled = en.checkState() == Qt.CheckState.Checked if en else True
            description = (desc_it.text() if desc_it else "").strip()

            w_match = self._tbl_rules.cellWidget(i, 2)
            if not isinstance(w_match, QComboBox):
                QMessageBox.warning(self, "错误", f"第 {i + 1} 行规则：找不到「客户情况」选项。")
                return None
            mtype = str(w_match.currentData() or "")

            product_field = (pf_it.text() if pf_it else "").strip() or "customizable_tags"

            w_tpl = self._tbl_rules.cellWidget(i, 4)
            if not isinstance(w_tpl, QComboBox):
                QMessageBox.warning(self, "错误", f"第 {i + 1} 行规则：找不到话术下拉框。请先点「刷新下方话术下拉框」。")
                return None
            rtid = str(w_tpl.currentData() or w_tpl.currentText() or "").strip()
            if not rtid:
                QMessageBox.warning(self, "错误", f"第 {i + 1} 行规则：请选择「使用哪条话术」。")
                return None
            if rtid not in valid_ids:
                QMessageBox.warning(
                    self,
                    "错误",
                    f"第 {i + 1} 行规则：话术「{rtid}」在话术模板表里不存在。\n请先刷新下拉框或检查代号是否一致。",
                )
                return None

            rid = ""
            if desc_it:
                rid = str(desc_it.data(Qt.ItemDataRole.UserRole) or "")
            if not rid:
                rid = new_rule_id()
                desc_it.setData(Qt.ItemDataRole.UserRole, rid)

            rules.append(
                RuleRow(
                    rule_id=rid,
                    enabled=enabled,
                    description=description,
                    match_type=mtype,
                    product_field=product_field,
                    reply_template_id=rtid,
                )
            )

        if not rules:
            QMessageBox.warning(self, "错误", "请至少添加一条分支规则。")
            return None

        return RulesBundle(version=1, templates=templates, rules=rules)

    def _on_save(self) -> None:
        bundle = self._tables_to_bundle()
        if bundle is None:
            return
        text = serialize_bundle_yaml(bundle)
        ok, err, _ = validate_rules_yaml(text)
        if not ok:
            QMessageBox.warning(self, "无法保存", err or "校验失败")
            return
        try:
            save_rules_yaml_text(text, self._path)
        except OSError as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return
        QMessageBox.information(self, "已保存", "规则已保存。\n\n" + str(self._path))
