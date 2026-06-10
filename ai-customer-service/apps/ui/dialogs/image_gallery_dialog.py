from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.core.ai.image_embed_service import clip_available, embed_missing_for_shop
from apps.core.configs.loader import load_shop_config
from apps.core.crm.db import connect, init_db
from apps.core.logging.image_library import insert_image_entry, list_images
from apps.core.runtime_paths import (
    brand_shop_image_kb_root,
    default_sqlite_db_path,
    image_library_products_dir,
    image_library_tutorials_dir,
)


class _EmbedThread(QThread):
    done_sig = pyqtSignal(int, int, str)
    log_sig = pyqtSignal(str)

    def __init__(
        self,
        *,
        db_path: Path,
        brand_id: str,
        shop_id: str,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._brand_id = brand_id
        self._shop_id = shop_id
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: ANN401
        try:
            conn = connect(self._db_path)
            init_db(conn)
            try:

                def lg(m: str) -> None:
                    self.log_sig.emit(m)

                ok, total = embed_missing_for_shop(
                    conn,
                    brand_id=self._brand_id,
                    shop_id=self._shop_id,
                    log=lg,
                    should_cancel=lambda: self._cancel,
                )
            finally:
                conn.close()
            self.done_sig.emit(ok, total, "")
        except Exception as e:
            self.done_sig.emit(0, 0, str(e))


class ImageGalleryDialog(QWidget):
    """
    图库：左侧目录树（数据库按文件夹分组 + image_kb 磁盘）+ 右侧列表；
    支持入库、发送、CLIP 批量向量化。
    """

    def __init__(
        self,
        *,
        shop_yaml: Path,
        category: str,
        on_send_image: Callable[[str, dict], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shop_yaml = Path(shop_yaml)
        self._category = "product" if category.strip().lower() != "tutorial" else "tutorial"
        self._on_send = on_send_image
        shop = load_shop_config(self._shop_yaml)
        self._brand_id = shop.brand_id
        self._shop_id = shop.shop_id or (shop.brand_id + ":" + shop.shop_code)
        self._db_path = default_sqlite_db_path()
        self._lib_root = (
            image_library_products_dir() if self._category == "product" else image_library_tutorials_dir()
        )
        self._kb_root = brand_shop_image_kb_root(self._brand_id, self._shop_id)

        title = "产品图库" if self._category == "product" else "教程 / 对比图库"
        self.setWindowTitle(title)
        self.setMinimumSize(720, 480)

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"<b>{title}</b> — 店铺：{shop.shop_display_name}"))

        split = QSplitter(Qt.Orientation.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["目录"])
        self._tree.setMinimumWidth(200)
        self._tree.currentItemChanged.connect(self._on_tree_sel)
        split.addWidget(self._tree)

        right = QWidget()
        rl = QVBoxLayout(right)
        self._list = QListWidget()
        self._list.setIconSize(QSize(72, 72))
        self._list.setMinimumHeight(220)
        rl.addWidget(self._list)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        root.addWidget(split)

        row = QHBoxLayout()
        self._btn_add = QPushButton("添加图片到库…")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_send = QPushButton("发送选中并发标注…")
        self._btn_send.clicked.connect(self._on_send_label)
        self._btn_plain_send = QPushButton("仅发送选中")
        self._btn_plain_send.clicked.connect(self._on_plain_send)
        self._btn_scan_disk = QPushButton("从 image_kb 磁盘导入…")
        self._btn_scan_disk.setToolTip(f"从 {self._kb_root} 选图写入图库（复制到 images 目录）")
        self._btn_scan_disk.clicked.connect(self._on_import_from_disk)
        self._btn_embed = QPushButton("图片进向量库分析")
        self._btn_embed.setToolTip("为当前店铺图库中尚未向量化的图片生成 CLIP 向量（需安装 torch + open-clip-torch）")
        self._btn_embed.clicked.connect(self._on_embed_batch)
        row.addWidget(self._btn_add)
        row.addWidget(self._btn_send)
        row.addWidget(self._btn_plain_send)
        row.addWidget(self._btn_scan_disk)
        row.addWidget(self._btn_embed)
        row.addStretch(1)
        root.addLayout(row)

        if self._on_send is None:
            self._btn_send.setEnabled(False)
            self._btn_plain_send.setEnabled(False)
            self._btn_send.setToolTip("请先启动全自动客服系统后再发送")
            self._btn_plain_send.setToolTip("请先启动全自动客服系统后再发送")

        self._embed_thread: _EmbedThread | None = None
        self._build_tree()
        self._reload_list_for_tree()

    def _build_tree(self) -> None:
        self._tree.clear()
        all_it = QTreeWidgetItem(["全部（数据库）"])
        all_it.setData(0, Qt.ItemDataRole.UserRole, ("all",))
        self._tree.addTopLevelItem(all_it)

        conn = connect(self._db_path)
        init_db(conn)
        try:
            rows = list_images(
                conn,
                brand_id=self._brand_id,
                shop_id=self._shop_id,
                category=self._category,
            )
        finally:
            conn.close()

        by_parent: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
        for iid, lp, ql, sc in rows:
            try:
                parent = str(Path(lp).resolve().parent)
            except OSError:
                parent = "(未知目录)"
            by_parent[parent].append((iid, lp, ql, sc))

        db_root = QTreeWidgetItem(["数据库 · 按存放子目录"])
        db_root.setData(0, Qt.ItemDataRole.UserRole, ("db_root",))
        self._tree.addTopLevelItem(db_root)
        for parent in sorted(by_parent.keys()):
            name = Path(parent).name if parent != "(未知目录)" else parent
            it = QTreeWidgetItem([name])
            it.setData(0, Qt.ItemDataRole.UserRole, ("dbdir", parent))
            db_root.addChild(it)

        kb_top = QTreeWidgetItem(["image_kb 磁盘（未入库可浏览）"])
        kb_top.setData(0, Qt.ItemDataRole.UserRole, ("kb_root",))
        self._tree.addTopLevelItem(kb_top)
        self._kb_root.mkdir(parents=True, exist_ok=True)
        for sub in sorted(self._kb_root.iterdir()) if self._kb_root.is_dir() else []:
            if sub.is_dir():
                ch = QTreeWidgetItem([sub.name])
                ch.setData(0, Qt.ItemDataRole.UserRole, ("kbdir", str(sub.resolve())))
                kb_top.addChild(ch)

        self._tree.expandToDepth(2)
        self._tree.setCurrentItem(all_it)

    def _tree_key(self) -> tuple[str, ...]:
        it = self._tree.currentItem()
        if it is None:
            return ("all",)
        d = it.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(d, tuple) and d:
            return tuple(str(x) for x in d)
        return ("all",)

    def _on_tree_sel(self, _cur, _prev) -> None:  # noqa: ANN001
        self._reload_list_for_tree()

    def _reload_list_for_tree(self) -> None:
        self._list.clear()
        key = self._tree_key()
        if key[0] == "kbdir" and len(key) >= 2:
            folder = Path(key[1])
            if folder.is_dir():
                for p in sorted(folder.rglob("*")):
                    if p.is_file() and p.suffix.lower() in (
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                        ".bmp",
                        ".gif",
                    ):
                        item = QListWidgetItem()
                        item.setData(Qt.ItemDataRole.UserRole, ("disk", str(p.resolve())))
                        item.setText(f"[磁盘] {p.relative_to(folder)}")
                        pix = QPixmap(str(p))
                        if not pix.isNull():
                            item.setIcon(QIcon(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio)))
                        self._list.addItem(item)
            return

        conn = connect(self._db_path)
        init_db(conn)
        try:
            rows = list_images(
                conn,
                brand_id=self._brand_id,
                shop_id=self._shop_id,
                category=self._category,
            )
        finally:
            conn.close()

        def include_row(parent: str, lp: str) -> bool:
            if key[0] == "all":
                return True
            if key[0] == "dbdir" and len(key) >= 2:
                try:
                    return str(Path(lp).resolve().parent) == key[1]
                except OSError:
                    return False
            return key[0] == "db_root"  # show all under db_root node - treat as all

        for iid, lp, ql, sc in rows:
            if not include_row("", lp):
                continue
            p = Path(lp)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, ("db", iid, str(p)))
            item.setText(f"{ql or '(无标签)'}\n发送{sc}次 · {p.name}")
            if p.is_file():
                pix = QPixmap(str(p))
                if not pix.isNull():
                    item.setIcon(QIcon(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio)))
            self._list.addItem(item)

    def _selected_payload(self) -> tuple[str, str, str] | None:
        """返回 (kind, image_id_or_empty, path)."""
        it = self._list.currentItem()
        if it is None:
            return None
        data = it.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) < 2:
            return None
        kind = str(data[0])
        if kind == "disk":
            return "disk", "", str(data[1])
        if kind == "db" and len(data) >= 3:
            return "db", str(data[1]), str(data[2])
        return None

    def _on_add(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*.*)",
        )
        if not files:
            return
        label, ok = QInputDialog.getText(self, "问题标签", "客户问什么样的问题时适合发这张图？（必填）")
        if not ok or not label.strip():
            QMessageBox.information(self, "已取消", "未填写标签，未入库。")
            return
        conn = connect(self._db_path)
        init_db(conn)
        try:
            for f in files:
                insert_image_entry(
                    conn,
                    brand_id=self._brand_id,
                    shop_id=self._shop_id,
                    category=self._category,
                    src_file=Path(f),
                    question_label=label.strip(),
                )
        except Exception as e:
            QMessageBox.critical(self, "入库失败", str(e))
            return
        finally:
            conn.close()
        self._build_tree()
        self._reload_list_for_tree()
        QMessageBox.information(self, "完成", f"已添加 {len(files)} 张图片。")

    def _on_import_from_disk(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "从 image_kb 选择图片导入图库",
            str(self._kb_root),
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*.*)",
        )
        if not files:
            return
        label, ok = QInputDialog.getText(self, "问题标签", "入库后用于自动匹配的问题标签（必填）")
        if not ok or not label.strip():
            return
        conn = connect(self._db_path)
        init_db(conn)
        try:
            for f in files:
                insert_image_entry(
                    conn,
                    brand_id=self._brand_id,
                    shop_id=self._shop_id,
                    category=self._category,
                    src_file=Path(f),
                    question_label=label.strip(),
                )
        except Exception as e:
            QMessageBox.critical(self, "入库失败", str(e))
            return
        finally:
            conn.close()
        self._build_tree()
        self._reload_list_for_tree()
        QMessageBox.information(self, "完成", f"已导入 {len(files)} 张。")

    def _on_embed_batch(self) -> None:
        if not clip_available():
            QMessageBox.warning(
                self,
                "无法分析",
                "当前环境未安装 torch / open_clip。\n请安装：pip install torch open-clip-torch",
            )
            return
        if self._embed_thread and self._embed_thread.isRunning():
            QMessageBox.information(self, "请稍候", "向量分析已在运行中。")
            return
        prog = QProgressDialog("正在生成 CLIP 向量…", "取消", 0, 0, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        th = _EmbedThread(db_path=self._db_path, brand_id=self._brand_id, shop_id=self._shop_id)
        self._embed_thread = th

        def on_log(m: str) -> None:
            prog.setLabelText(m)

        def on_done(ok: int, total: int, err: str) -> None:
            prog.cancel()
            self._embed_thread = None
            if err:
                QMessageBox.critical(self, "向量化失败", err)
            else:
                QMessageBox.information(self, "完成", f"成功写入 {ok} 条向量（本轮扫描 {total} 条待处理）。")

        th.log_sig.connect(on_log)
        th.done_sig.connect(on_done)
        prog.canceled.connect(th.cancel)
        th.start()

    def _on_plain_send(self) -> None:
        sel = self._selected_payload()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中一张图。")
            return
        kind, iid, path = sel
        if kind != "db" or not iid:
            QMessageBox.information(self, "提示", "请从「数据库」列表选择已入库图片再发送。")
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "文件缺失", path)
            return
        if self._on_send is None:
            return
        try:
            self._on_send(
                path,
                {
                    "customer_label": "",
                    "intent_label": "图库:手动发送",
                    "kb_node": f"图库:{iid}",
                    "image_library_id": iid,
                },
            )
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))
            return
        QMessageBox.information(self, "已排队", "图片已加入发送队列。")

    def _on_send_label(self) -> None:
        sel = self._selected_payload()
        if not sel:
            QMessageBox.information(self, "提示", "请先选中一张图。")
            return
        kind, iid, path = sel
        if kind != "db" or not iid:
            QMessageBox.information(self, "提示", "请从「数据库」列表选择已入库图片。")
            return
        if not Path(path).is_file():
            QMessageBox.warning(self, "文件缺失", path)
            return
        label, ok = QInputDialog.getText(
            self,
            "更新标签（可选）",
            "可更新该图的问题标签；留空则保持原标签不变。",
        )
        if not ok:
            return
        if self._on_send is None:
            return
        meta = {
            "customer_label": "",
            "intent_label": "图库:发送并标注",
            "kb_node": f"图库:{iid}",
            "image_library_id": iid,
        }
        if label.strip():
            conn = connect(self._db_path)
            init_db(conn)
            try:
                conn.execute(
                    "UPDATE image_library SET question_label = ?, updated_at = ? WHERE image_id = ?",
                    (label.strip()[:500], time.strftime("%Y-%m-%dT%H:%M:%S"), iid),
                )
                conn.commit()
            finally:
                conn.close()
            self._reload_list_for_tree()
        try:
            self._on_send(path, meta)
        except Exception as e:
            QMessageBox.critical(self, "发送失败", str(e))
            return
        QMessageBox.information(self, "已排队", "图片已加入发送队列。")
