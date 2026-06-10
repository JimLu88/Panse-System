# -*- coding: utf-8 -*-
"""
子文件夹批量提取工具（可视化）
=====================================
场景：
  根目录下有 N 个外层文件夹，每个外层文件夹里又有若干子文件夹。
  你要从每个外层文件夹里挑出一个【指定子文件夹】，提取到一个新目录，
  新目录里的文件夹名 = 外层文件夹名（也可逐行自定义）。

特点：
  - 纯 Python 自带 Tkinter，零依赖，启动快、响应快
  - 一张表格列出全部外层文件夹，一次设好、一次跑完
  - 提供「统一选择子文件夹 → 应用到全部」一键批量设定
  - 支持 移动 / 复制 两种方式
  - 处理在后台线程，界面不卡；带进度条和日志

运行：python 子文件夹提取工具.py
"""

import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class ExtractorApp:
    def __init__(self, root):
        self.root = root
        root.title("子文件夹批量提取工具")
        root.geometry("920x640")

        self.src_root = tk.StringVar()       # 根目录
        self.dst_root = tk.StringVar()       # 输出目录
        self.mode = tk.StringVar(value="move")  # move / copy
        self.batch_pick = tk.StringVar()     # 统一选择用的子文件夹名

        self.rows = []   # 每行: dict(outer, subs, include_var, pick_var, name_var, combo)

        self._build_top()
        self._build_table()
        self._build_bottom()

    # ---------- 顶部：选目录 ----------
    def _build_top(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="根目录（含外层文件夹）:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.src_root, width=70).grid(row=0, column=1, padx=6)
        ttk.Button(top, text="选择…", command=self.choose_src).grid(row=0, column=2)

        ttk.Label(top, text="输出目录:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(top, textvariable=self.dst_root, width=70).grid(row=1, column=1, padx=6, pady=(6, 0))
        ttk.Button(top, text="选择…", command=self.choose_dst).grid(row=1, column=2, pady=(6, 0))

        # 批量工具
        batch = ttk.LabelFrame(self.root, text="批量设定（可选）", padding=8)
        batch.pack(fill="x", padx=10)
        ttk.Label(batch, text="把所有行的【提取子文件夹】统一设为:").pack(side="left")
        self.batch_combo = ttk.Combobox(batch, textvariable=self.batch_pick, width=30, state="readonly")
        self.batch_combo.pack(side="left", padx=6)
        ttk.Button(batch, text="应用到全部", command=self.apply_batch).pack(side="left")
        ttk.Button(batch, text="全选/全不选", command=self.toggle_all).pack(side="left", padx=6)

    # ---------- 中部：表格 ----------
    def _build_table(self):
        # 表头
        header = ttk.Frame(self.root, padding=(12, 4))
        header.pack(fill="x")
        ttk.Label(header, text="✓", width=3).grid(row=0, column=0)
        ttk.Label(header, text="外层文件夹", width=28).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="提取哪个子文件夹", width=32).grid(row=0, column=2, sticky="w")
        ttk.Label(header, text="新名称（默认=外层名）", width=28).grid(row=0, column=3, sticky="w")

        # 可滚动区域
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True, padx=12)
        canvas = tk.Canvas(container, highlightthickness=0)
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.table = ttk.Frame(canvas)
        self.table.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.table, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        # 鼠标滚轮
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    # ---------- 底部：操作 ----------
    def _build_bottom(self):
        bottom = ttk.Frame(self.root, padding=10)
        bottom.pack(fill="x")

        ttk.Radiobutton(bottom, text="移动（原文件夹会被搬走）", variable=self.mode, value="move").pack(side="left")
        ttk.Radiobutton(bottom, text="复制（保留原文件夹）", variable=self.mode, value="copy").pack(side="left", padx=10)

        self.run_btn = ttk.Button(bottom, text="开始处理", command=self.start)
        self.run_btn.pack(side="right")

        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 4))

        self.log = tk.Text(self.root, height=8, state="disabled")
        self.log.pack(fill="both", padx=10, pady=(0, 10))

    # ---------- 逻辑 ----------
    def choose_src(self):
        d = filedialog.askdirectory(title="选择根目录")
        if d:
            self.src_root.set(d)
            self.load_folders(d)

    def choose_dst(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.dst_root.set(d)

    def load_folders(self, root_dir):
        """扫描根目录下的外层文件夹及各自的子文件夹，铺成表格。"""
        for w in self.table.winfo_children():
            w.destroy()
        self.rows.clear()

        outers = sorted(
            d for d in os.listdir(root_dir)
            if os.path.isdir(os.path.join(root_dir, d))
        )
        all_sub_names = set()

        for i, outer in enumerate(outers):
            outer_path = os.path.join(root_dir, outer)
            subs = sorted(
                d for d in os.listdir(outer_path)
                if os.path.isdir(os.path.join(outer_path, d))
            )
            all_sub_names.update(subs)

            include_var = tk.BooleanVar(value=True)
            pick_var = tk.StringVar(value=subs[0] if subs else "")
            name_var = tk.StringVar(value=outer)

            ttk.Checkbutton(self.table, variable=include_var).grid(row=i, column=0, padx=2, pady=2)
            ttk.Label(self.table, text=outer, width=28, anchor="w").grid(row=i, column=1, sticky="w")
            combo = ttk.Combobox(self.table, textvariable=pick_var, values=subs,
                                 width=30, state="readonly")
            combo.grid(row=i, column=2, padx=4)
            if not subs:
                combo.configure(state="disabled")
                include_var.set(False)
            ttk.Entry(self.table, textvariable=name_var, width=28).grid(row=i, column=3, padx=4)

            self.rows.append(dict(outer=outer, outer_path=outer_path, subs=subs,
                                 include=include_var, pick=pick_var, name=name_var))

        # 批量下拉用所有出现过的子文件夹名
        self.batch_combo.configure(values=sorted(all_sub_names))
        self._log(f"已加载 {len(outers)} 个外层文件夹。")

    def apply_batch(self):
        target = self.batch_pick.get()
        if not target:
            messagebox.showinfo("提示", "请先在上方下拉里选一个子文件夹名。")
            return
        count = 0
        for r in self.rows:
            if target in r["subs"]:
                r["pick"].set(target)
                r["include"].set(True)
                count += 1
            else:
                # 该外层没有这个子文件夹 → 标记不处理，避免误操作
                r["include"].set(False)
        self._log(f"已把 {count} 个外层文件夹的提取目标统一设为「{target}」"
                  f"（其余 {len(self.rows) - count} 个不含此子文件夹，已取消勾选）。")

    def toggle_all(self):
        new_val = not all(r["include"].get() for r in self.rows)
        for r in self.rows:
            if r["subs"]:
                r["include"].set(new_val)

    def start(self):
        if not self.rows:
            messagebox.showwarning("提示", "请先选择根目录加载文件夹。")
            return
        if not self.dst_root.get():
            messagebox.showwarning("提示", "请先选择输出目录。")
            return

        jobs = []
        for r in self.rows:
            if not r["include"].get() or not r["pick"].get():
                continue
            src = os.path.join(r["outer_path"], r["pick"].get())
            name = r["name"].get().strip() or r["outer"]
            dst = os.path.join(self.dst_root.get(), name)
            jobs.append((src, dst, r["outer"]))

        if not jobs:
            messagebox.showinfo("提示", "没有勾选任何要处理的行。")
            return

        if not messagebox.askyesno(
            "确认",
            f"将{'移动' if self.mode.get() == 'move' else '复制'} {len(jobs)} 个子文件夹到\n"
            f"{self.dst_root.get()}\n\n确定开始吗？",
        ):
            return

        self.run_btn.configure(state="disabled")
        threading.Thread(target=self._process, args=(jobs,), daemon=True).start()

    def _process(self, jobs):
        self.progress.configure(maximum=len(jobs), value=0)
        ok = fail = 0
        is_move = self.mode.get() == "move"

        for i, (src, dst, outer) in enumerate(jobs, 1):
            try:
                if os.path.exists(dst):
                    # 目标重名 → 加序号避免覆盖
                    base, n = dst, 1
                    while os.path.exists(dst):
                        dst = f"{base}_{n}"
                        n += 1
                if is_move:
                    shutil.move(src, dst)
                else:
                    shutil.copytree(src, dst)
                ok += 1
                self._log(f"[{i}/{len(jobs)}] {outer} → {os.path.basename(dst)}  ✓")
            except Exception as e:
                fail += 1
                self._log(f"[{i}/{len(jobs)}] {outer}  ✗ 失败：{e}")
            self.progress.configure(value=i)

        self._log(f"\n完成！成功 {ok} 个，失败 {fail} 个。")
        self.root.after(0, lambda: self.run_btn.configure(state="normal"))
        self.root.after(0, lambda: messagebox.showinfo("完成", f"成功 {ok} 个，失败 {fail} 个。"))

    def _log(self, msg):
        def append():
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, append)


if __name__ == "__main__":
    root = tk.Tk()
    ExtractorApp(root)
    root.mainloop()
