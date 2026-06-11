# -*- coding: utf-8 -*-
"""
图片工具箱（可视化）
=====================================
两个功能合一，标签页切换：
  1) 子文件夹提取：从每个外层文件夹里挑指定子文件夹，提取到新目录
  2) 批量压缩：批量压缩图片，可选质量、格式转换、最大尺寸

支持格式：
  输入：jpg/jpeg/png/webp/bmp/tif/tiff/gif/ico + HEIC/HEIF（苹果手机照片）+ avif
  输出：JPG / PNG / WebP / AVIF / TIFF / BMP / GIF / ICO

依赖（仅压缩功能需要）：pillow  pillow-heif  pillow-avif-plugin
运行：python image_toolbox.py
"""

import io
import os
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---- 图片库与扩展格式插件（缺失时压缩页友好降级） ----
try:
    from PIL import Image, ImageOps
    PIL_OK = True
except ImportError:
    PIL_OK = False

# HEIC/HEIF 读取（苹果照片）
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:
    HEIF_OK = False

# AVIF 读写
try:
    import pillow_avif  # noqa: F401  导入即注册 AVIF 插件
    AVIF_OK = True
except Exception:
    # pillow-heif 也能提供 AVIF 支持
    try:
        pillow_heif.register_avif_opener()
        AVIF_OK = True
    except Exception:
        AVIF_OK = False

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff",
             ".gif", ".ico", ".heic", ".heif", ".avif"}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}"
        n /= 1024


# =====================================================================
# 标签页一：子文件夹提取
# =====================================================================
class ExtractorTab(ttk.Frame):
    def __init__(self, master, compress_tab=None, notebook=None):
        super().__init__(master, padding=8)
        self.compress_tab = compress_tab   # 复用「批量压缩」页的设置
        self.notebook = notebook           # 用于“去调整压缩设置”跳转
        self.src_root = tk.StringVar()
        self.dst_root = tk.StringVar()
        self.mode = tk.StringVar(value="move")
        self.batch_pick = tk.StringVar()
        self.compress_on = tk.BooleanVar(value=False)
        self.cancel = threading.Event()    # 停止标志
        self.rows = []
        self._build()
        # 监听「批量压缩」页设置变化，实时刷新摘要（否则改了质量这里不更新）
        if self.compress_tab:
            for v in (self.compress_tab.quality, self.compress_tab.out_fmt,
                      self.compress_tab.max_size, self.compress_tab.target_mb):
                v.trace_add("write", lambda *a: self._refresh_comp_summary())

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="根目录（含外层文件夹）:").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.src_root, width=60).grid(row=0, column=1, padx=6)
        ttk.Button(top, text="选择…", command=self.choose_src).grid(row=0, column=2)
        ttk.Label(top, text="输出目录:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(top, textvariable=self.dst_root, width=60).grid(row=1, column=1, padx=6, pady=(6, 0))
        ttk.Button(top, text="选择…", command=self.choose_dst).grid(row=1, column=2, pady=(6, 0))

        batch = ttk.LabelFrame(self, text="批量设定（可选）", padding=6)
        batch.pack(fill="x", pady=6)
        ttk.Label(batch, text="所有行统一提取:").pack(side="left")
        self.batch_combo = ttk.Combobox(batch, textvariable=self.batch_pick, width=26, state="readonly")
        self.batch_combo.pack(side="left", padx=6)
        ttk.Button(batch, text="应用到全部", command=self.apply_batch).pack(side="left")
        ttk.Button(batch, text="全选/全不选", command=self.toggle_all).pack(side="left", padx=6)

        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="✓", width=3).grid(row=0, column=0)
        ttk.Label(header, text="外层文件夹", width=26).grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="提取哪个子文件夹", width=30).grid(row=0, column=2, sticky="w")
        ttk.Label(header, text="新名称（默认=外层名）", width=26).grid(row=0, column=3, sticky="w")

        cont = ttk.Frame(self)
        cont.pack(fill="both", expand=True)
        canvas = tk.Canvas(cont, highlightthickness=0)
        sb = ttk.Scrollbar(cont, orient="vertical", command=canvas.yview)
        self.table = ttk.Frame(canvas)
        self.table.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.table, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        # 复制/移动时顺带压缩图片（复用「批量压缩」页设置）
        comp = ttk.Frame(self)
        comp.pack(fill="x", pady=(4, 0))
        ttk.Checkbutton(comp, text="复制时压缩图片内容", variable=self.compress_on,
                        command=self._toggle_compress).pack(side="left")
        self.comp_frame = ttk.Frame(comp)  # 勾选后才显示
        self.comp_summary = ttk.Label(self.comp_frame, text="", foreground="gray")
        self.comp_summary.pack(side="left", padx=8)
        ttk.Button(self.comp_frame, text="去调整压缩设置",
                   command=self._goto_compress).pack(side="left")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", pady=6)
        ttk.Radiobutton(bottom, text="移动", variable=self.mode, value="move").pack(side="left")
        ttk.Radiobutton(bottom, text="复制", variable=self.mode, value="copy").pack(side="left", padx=8)
        self.run_btn = ttk.Button(bottom, text="开始提取", command=self.start)
        self.run_btn.pack(side="right")
        self.stop_btn = ttk.Button(bottom, text="停止", command=self.stop_run, state="disabled")
        self.stop_btn.pack(side="right", padx=6)

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x")
        self.log = tk.Text(self, height=6, state="disabled")
        self.log.pack(fill="both", pady=(4, 0))

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
        for w in self.table.winfo_children():
            w.destroy()
        self.rows.clear()
        outers = sorted(d for d in os.listdir(root_dir)
                        if os.path.isdir(os.path.join(root_dir, d)))
        all_subs = set()
        for i, outer in enumerate(outers):
            op = os.path.join(root_dir, outer)
            subs = sorted(d for d in os.listdir(op) if os.path.isdir(os.path.join(op, d)))
            all_subs.update(subs)
            inc = tk.BooleanVar(value=bool(subs))
            pick = tk.StringVar(value=subs[0] if subs else "")
            name = tk.StringVar(value=outer)
            ttk.Checkbutton(self.table, variable=inc).grid(row=i, column=0, padx=2, pady=2)
            ttk.Label(self.table, text=outer, width=26, anchor="w").grid(row=i, column=1, sticky="w")
            combo = ttk.Combobox(self.table, textvariable=pick, values=subs, width=28,
                                 state="readonly" if subs else "disabled")
            combo.grid(row=i, column=2, padx=4)
            ttk.Entry(self.table, textvariable=name, width=26).grid(row=i, column=3, padx=4)
            self.rows.append(dict(outer=outer, outer_path=op, subs=subs,
                                  include=inc, pick=pick, name=name))
        self.batch_combo.configure(values=sorted(all_subs))
        self._log(f"已加载 {len(outers)} 个外层文件夹。")

    def apply_batch(self):
        t = self.batch_pick.get()
        if not t:
            messagebox.showinfo("提示", "请先在下拉里选一个子文件夹名。")
            return
        c = 0
        for r in self.rows:
            if t in r["subs"]:
                r["pick"].set(t); r["include"].set(True); c += 1
            else:
                r["include"].set(False)
        self._log(f"已统一设为「{t}」：{c} 个匹配，其余 {len(self.rows) - c} 个已取消勾选。")

    def toggle_all(self):
        nv = not all(r["include"].get() for r in self.rows)
        for r in self.rows:
            if r["subs"]:
                r["include"].set(nv)

    # ---- 压缩选项 ----
    def _toggle_compress(self):
        if self.compress_on.get():
            if not PIL_OK:
                messagebox.showwarning("提示", "未安装 Pillow，无法压缩。请先 pip 安装相关库。")
                self.compress_on.set(False)
                return
            self._refresh_comp_summary()
            self.comp_frame.pack(side="left")
        else:
            self.comp_frame.pack_forget()

    def _refresh_comp_summary(self):
        ct = self.compress_tab
        if not ct:
            self.comp_summary.configure(text="（找不到压缩页设置）")
            return
        mx = ct.max_size.get().strip() or "不限"
        tgt = ct.target_mb.get().strip()
        tgt_txt = f" / 目标 {tgt}MB" if tgt else ""
        self.comp_summary.configure(
            text=f"当前压缩设置：质量 {ct.quality.get()} / 格式 {ct.out_fmt.get()} / 最大边 {mx}px{tgt_txt}")

    def _goto_compress(self):
        if self.notebook and self.compress_tab:
            self.notebook.select(self.compress_tab)

    def start(self):
        if not self.rows:
            messagebox.showwarning("提示", "请先选择根目录。"); return
        if not self.dst_root.get():
            messagebox.showwarning("提示", "请先选择输出目录。"); return
        jobs = []
        for r in self.rows:
            if r["include"].get() and r["pick"].get():
                src = os.path.join(r["outer_path"], r["pick"].get())
                nm = r["name"].get().strip() or r["outer"]
                jobs.append((src, os.path.join(self.dst_root.get(), nm), r["outer"]))
        if not jobs:
            messagebox.showinfo("提示", "没有勾选任何行。"); return
        do_compress = self.compress_on.get()
        tip = "（图片将按压缩页设置压缩）" if do_compress else ""
        if not messagebox.askyesno("确认", f"将{'移动' if self.mode.get() == 'move' else '复制'} "
                                            f"{len(jobs)} 个子文件夹{tip}，确定吗？"):
            return
        self.cancel.clear()
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._process, args=(jobs, do_compress), daemon=True).start()

    def stop_run(self):
        self.cancel.set()
        self._log("正在停止…（当前文件夹处理完即停）")

    def _compress_settings(self):
        """从「批量压缩」页读取当前设置，返回 (quality, target_ext, max_size, tgt_bytes)。"""
        ct = self.compress_tab
        ext_map = {"JPG": ".jpg", "PNG": ".png", "WebP": ".webp", "AVIF": ".avif",
                   "TIFF": ".tiff", "BMP": ".bmp", "GIF": ".gif", "ICO": ".ico"}
        target_ext = ext_map.get(ct.out_fmt.get())  # None = 保持原格式
        try:
            mx = int(ct.max_size.get()) if ct.max_size.get().strip() else None
        except ValueError:
            mx = None
        try:
            tgt = int(float(ct.target_mb.get()) * 1024 * 1024) if ct.target_mb.get().strip() else None
        except ValueError:
            tgt = None
        return ct.quality.get(), target_ext, mx, tgt

    def _copy_compress_tree(self, src, dst, quality, target_ext, max_size, tgt_bytes):
        """复制整个子文件夹；图片压缩，非图片原样复制。"""
        for root, _, files in os.walk(src):
            out_dir = os.path.join(dst, os.path.relpath(root, src))
            os.makedirs(out_dir, exist_ok=True)
            for f in files:
                sp = os.path.join(root, f)
                if os.path.splitext(f)[1].lower() in SUPPORTED:
                    dp = os.path.join(out_dir, f)
                    if target_ext:
                        dp = os.path.splitext(dp)[0] + target_ext
                    try:
                        CompressTab._compress_one(sp, dp, quality, target_ext, max_size, tgt_bytes)
                    except Exception:
                        shutil.copy2(sp, os.path.join(out_dir, f))  # 压缩失败则保留原图
                else:
                    shutil.copy2(sp, os.path.join(out_dir, f))

    def _process(self, jobs, do_compress):
        self.progress.configure(maximum=len(jobs), value=0)
        ok = fail = 0
        is_move = self.mode.get() == "move"
        cfg = self._compress_settings() if do_compress else None
        stopped = False
        for i, (src, dst, outer) in enumerate(jobs, 1):
            if self.cancel.is_set():
                stopped = True
                break
            try:
                base, n = dst, 1
                while os.path.exists(dst):
                    dst = f"{base}_{n}"; n += 1
                if do_compress:
                    self._copy_compress_tree(src, dst, *cfg)
                    if is_move:
                        shutil.rmtree(src)  # 压缩是重新编码，先压成新目录再删原文件夹
                else:
                    shutil.move(src, dst) if is_move else shutil.copytree(src, dst)
                ok += 1
                self._log(f"[{i}/{len(jobs)}] {outer} → {os.path.basename(dst)} ✓")
            except Exception as e:
                fail += 1
                self._log(f"[{i}/{len(jobs)}] {outer} ✗ {e}")
            self.progress.configure(value=i)
        head = "已停止。" if stopped else "完成！"
        self._log(f"\n{head}成功 {ok}，失败 {fail}。" + ("（已压缩）" if do_compress else ""))
        self.after(0, self._finish)
        self.after(0, lambda: messagebox.showinfo("已停止" if stopped else "完成",
                                                  f"成功 {ok}，失败 {fail}。"))

    def _finish(self):
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _log(self, msg):
        def a():
            self.log.configure(state="normal"); self.log.insert("end", msg + "\n")
            self.log.see("end"); self.log.configure(state="disabled")
        self.after(0, a)


# =====================================================================
# 标签页二：批量压缩
# =====================================================================
class CompressTab(ttk.Frame):
    OUT_FORMATS = ["保持原格式", "JPG", "PNG", "WebP", "AVIF", "TIFF", "BMP", "GIF", "ICO"]

    def __init__(self, master):
        super().__init__(master, padding=8)
        self.recursive = tk.BooleanVar(value=False)
        self.quality = tk.IntVar(value=85)
        self.out_fmt = tk.StringVar(value="保持原格式")
        self.max_size = tk.StringVar(value="")
        self.target_mb = tk.StringVar(value="")   # 目标大小(MB)，留空=不限
        self.dst_root = tk.StringVar()
        self.folders = []
        self.cancel = threading.Event()
        self._build()

    def _build(self):
        if not PIL_OK:
            ttk.Label(self, text="未安装 Pillow，压缩功能不可用。\n请运行：pip install pillow pillow-heif pillow-avif-plugin",
                      foreground="red").pack(pady=20)
            return

        caps = []
        caps.append("HEIC/HEIF " + ("✓" if HEIF_OK else "✗（缺 pillow-heif）"))
        caps.append("AVIF " + ("✓" if AVIF_OK else "✗（缺 pillow-avif-plugin）"))
        ttk.Label(self, text="扩展格式支持：  " + "    ".join(caps), foreground="gray").pack(anchor="w")

        fr = ttk.LabelFrame(self, text="要压缩的文件夹", padding=6)
        fr.pack(fill="both", expand=True, pady=4)
        self.listbox = tk.Listbox(fr, height=6)
        self.listbox.pack(side="left", fill="both", expand=True)
        btns = ttk.Frame(fr)
        btns.pack(side="right", fill="y", padx=6)
        ttk.Button(btns, text="添加文件夹", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="移除选中", command=self.remove_folder).pack(fill="x", pady=2)
        ttk.Checkbutton(btns, text="含子文件夹", variable=self.recursive).pack(anchor="w", pady=4)

        opt = ttk.Frame(self)
        opt.pack(fill="x", pady=4)
        ttk.Label(opt, text="质量:").grid(row=0, column=0, sticky="w")
        ttk.Scale(opt, from_=1, to=100, variable=self.quality, orient="horizontal",
                  length=180, command=lambda e: self.qlabel.configure(text=str(self.quality.get()))).grid(row=0, column=1, padx=4)
        self.qlabel = ttk.Label(opt, text="85", width=4)
        self.qlabel.grid(row=0, column=2)
        ttk.Label(opt, text="（PNG/BMP 无损，质量无效）", foreground="gray").grid(row=0, column=3, sticky="w")

        ttk.Label(opt, text="输出格式:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(opt, textvariable=self.out_fmt, values=self.OUT_FORMATS,
                     width=14, state="readonly").grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(opt, text="最大边像素(留空=不限):").grid(row=1, column=2, sticky="e", pady=(6, 0))
        ttk.Entry(opt, textvariable=self.max_size, width=8).grid(row=1, column=3, sticky="w", pady=(6, 0))

        ttk.Label(opt, text="目标大小(MB):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(opt, textvariable=self.target_mb, width=8).grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(opt, text="留空=只按上面的质量；填了会自动降质量/缩尺寸压到该大小以内",
                  foreground="gray").grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))

        dst = ttk.Frame(self)
        dst.pack(fill="x", pady=4)
        ttk.Label(dst, text="输出目录:").pack(side="left")
        ttk.Entry(dst, textvariable=self.dst_root, width=50).pack(side="left", padx=6)
        ttk.Button(dst, text="选择…", command=self.choose_dst).pack(side="left")

        bot = ttk.Frame(self)
        bot.pack(fill="x")
        self.run_btn = ttk.Button(bot, text="开始压缩", command=self.start)
        self.run_btn.pack(side="right")
        self.stop_btn = ttk.Button(bot, text="停止", command=self.stop_run, state="disabled")
        self.stop_btn.pack(side="right", padx=6)
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 0))
        self.log = tk.Text(self, height=6, state="disabled")
        self.log.pack(fill="both", pady=(4, 0))

    def add_folder(self):
        d = filedialog.askdirectory(title="选择要压缩的文件夹")
        if d and d not in self.folders:
            self.folders.append(d)
            self.listbox.insert("end", d)

    def remove_folder(self):
        for idx in reversed(self.listbox.curselection()):
            self.folders.pop(idx)
            self.listbox.delete(idx)

    def choose_dst(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.dst_root.set(d)

    def _collect(self, folder):
        out = []
        if self.recursive.get():
            for root, _, files in os.walk(folder):
                out += [os.path.join(root, f) for f in files
                        if os.path.splitext(f)[1].lower() in SUPPORTED]
        else:
            out = [os.path.join(folder, f) for f in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, f))
                   and os.path.splitext(f)[1].lower() in SUPPORTED]
        return out

    def start(self):
        if not PIL_OK:
            return
        if not self.folders:
            messagebox.showwarning("提示", "请先添加要压缩的文件夹。"); return
        if not self.dst_root.get():
            messagebox.showwarning("提示", "请先选择输出目录。"); return

        ext_map = {"JPG": ".jpg", "PNG": ".png", "WebP": ".webp", "AVIF": ".avif",
                   "TIFF": ".tiff", "BMP": ".bmp", "GIF": ".gif", "ICO": ".ico"}
        target_ext = ext_map.get(self.out_fmt.get())  # None = 保持原格式
        try:
            mx = int(self.max_size.get()) if self.max_size.get().strip() else None
        except ValueError:
            mx = None

        try:
            tgt_bytes = int(float(self.target_mb.get()) * 1024 * 1024) if self.target_mb.get().strip() else None
        except ValueError:
            tgt_bytes = None

        tasks = []
        for folder in self.folders:
            for src in self._collect(folder):
                tasks.append((src, folder))
        if not tasks:
            messagebox.showinfo("提示", "没有找到图片。"); return

        self.cancel.clear()
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._process,
                         args=(tasks, self.quality.get(), target_ext, mx, tgt_bytes),
                         daemon=True).start()

    def stop_run(self):
        self.cancel.set()
        self._log("正在停止…（当前图片处理完即停）")

    def _process(self, tasks, quality, target_ext, max_size, tgt_bytes):
        self.progress.configure(maximum=len(tasks), value=0)
        ok = fail = 0
        total_o = total_n = 0
        stopped = False
        for i, (src, base) in enumerate(tasks, 1):
            if self.cancel.is_set():
                stopped = True
                break
            rel = os.path.relpath(src, base)
            dst_root = os.path.join(self.dst_root.get(), os.path.basename(base.rstrip("/\\")))
            dst = os.path.join(dst_root, rel)
            if target_ext:
                dst = os.path.splitext(dst)[0] + target_ext
            try:
                o, n = self._compress_one(src, dst, quality, target_ext, max_size, tgt_bytes)
                total_o += o; total_n += n; ok += 1
                pct = (1 - n / o) * 100 if o else 0
                self._log(f"[{i}/{len(tasks)}] {rel}  {human(o)}→{human(n)} (-{pct:.0f}%)")
            except Exception as e:
                fail += 1
                self._log(f"[{i}/{len(tasks)}] ✗ {rel}  {e}")
            self.progress.configure(value=i)
        head = "已停止。" if stopped else "完成！"
        if total_o:
            saved = total_o - total_n
            self._log(f"\n{head}成功 {ok}，失败 {fail}。"
                      f"总计 {human(total_o)}→{human(total_n)}，省 {human(saved)} "
                      f"(-{saved / total_o * 100:.0f}%)")
        else:
            self._log(f"\n{head}成功 {ok}，失败 {fail}。")
        self.after(0, self._finish)
        self.after(0, lambda: messagebox.showinfo("已停止" if stopped else "完成",
                                                  f"成功 {ok}，失败 {fail}。"))

    def _finish(self):
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    _FMT = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
            ".avif": "AVIF", ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP",
            ".gif": "GIF", ".ico": "ICO"}

    @staticmethod
    def _save_kwargs(out_ext, quality):
        if out_ext in (".jpg", ".jpeg"):
            return {"quality": quality, "optimize": True, "progressive": True}
        if out_ext == ".webp":
            return {"quality": quality, "method": 6}
        if out_ext == ".avif":
            return {"quality": quality}
        if out_ext == ".png":
            return {"optimize": True}
        if out_ext in (".tif", ".tiff"):
            return {"compression": "tiff_lzw"}
        return {}

    @classmethod
    def _compress_one(cls, src, dst, quality, target_ext, max_size, tgt_bytes=None):
        orig = os.path.getsize(src)
        img = Image.open(src)
        # 按 EXIF 方向把像素转正（否则手机竖图重存后会显示成横的）
        img = ImageOps.exif_transpose(img)
        if max_size and (img.width > max_size or img.height > max_size):
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        out_ext = (target_ext or os.path.splitext(src)[1]).lower()

        # 不支持透明通道的格式 → 贴白底转 RGB
        if out_ext in (".jpg", ".jpeg", ".bmp"):
            if img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
        elif out_ext == ".gif":
            img = img.convert("P", palette=Image.ADAPTIVE)

        os.makedirs(os.path.dirname(dst), exist_ok=True)

        # 普通模式：直接按质量保存
        if not tgt_bytes:
            img.save(dst, **cls._save_kwargs(out_ext, quality))
            return orig, os.path.getsize(dst)

        # 目标大小模式：先降质量，仍超标再缩尺寸，迭代逼近
        fmt = cls._FMT.get(out_ext, "JPEG")
        lossy = out_ext in (".jpg", ".jpeg", ".webp", ".avif")
        work = img
        fallback = None  # 达不到目标时的最小结果兜底
        for _ in range(7):                       # 最多缩 7 轮尺寸
            if lossy:
                q = quality
                while q >= 20:
                    buf = io.BytesIO()
                    work.save(buf, format=fmt, **cls._save_kwargs(out_ext, q))
                    if buf.tell() <= tgt_bytes:
                        with open(dst, "wb") as f:
                            f.write(buf.getvalue())
                        return orig, os.path.getsize(dst)
                    q -= 10
                fallback = buf.getvalue()        # 记录最低质量结果
            else:                                 # 无损格式只能靠缩尺寸
                buf = io.BytesIO()
                work.save(buf, format=fmt, **cls._save_kwargs(out_ext, quality))
                if buf.tell() <= tgt_bytes:
                    with open(dst, "wb") as f:
                        f.write(buf.getvalue())
                    return orig, os.path.getsize(dst)
                fallback = buf.getvalue()
            nw, nh = int(work.width * 0.85), int(work.height * 0.85)
            if nw < 100 or nh < 100:
                break
            work = work.resize((nw, nh), Image.LANCZOS)

        # 没压到目标以内：写入能做到的最小结果
        with open(dst, "wb") as f:
            f.write(fallback)
        return orig, os.path.getsize(dst)

    def _log(self, msg):
        def a():
            self.log.configure(state="normal"); self.log.insert("end", msg + "\n")
            self.log.see("end"); self.log.configure(state="disabled")
        self.after(0, a)


def main():
    root = tk.Tk()
    root.title("图片工具箱")
    root.geometry("900x680")
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True)
    compress_tab = CompressTab(nb)
    extractor_tab = ExtractorTab(nb, compress_tab=compress_tab, notebook=nb)
    nb.add(extractor_tab, text="  子文件夹提取  ")
    nb.add(compress_tab, text="  批量压缩  ")
    root.mainloop()


if __name__ == "__main__":
    main()
