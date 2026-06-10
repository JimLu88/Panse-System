# -*- coding: utf-8 -*-
"""
批量图片压缩工具（本地运行）
---------------------------------
功能：
  1. 选择要处理的一个或多个文件夹
  2. 选择导出质量（95 / 85 / 75 / 65 或自定义）
  3. 可选格式转换（保持原格式 / JPG / WebP / PNG）
  4. 压缩结果输出到新目录，绝不覆盖原图
  5. 显示每张图及总体的压缩前后大小对比

依赖：pip install pillow
用法：
  直接双击 / 运行：python 批量压缩图片.py      → 全程交互问答
  也可命令行参数：python 批量压缩图片.py 文件夹A 文件夹B
"""

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("缺少依赖 Pillow，请先运行：  pip install pillow")
    sys.exit(1)

# 支持的输入图片扩展名
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def human(size_bytes):
    """把字节数转成人类可读（KB/MB）。"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024 or unit == "GB":
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024


def ask(prompt, default=None):
    """带默认值的输入。"""
    suffix = f"（默认 {default}）" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else (default if default is not None else "")


def pick_folders(cli_args):
    """收集要处理的文件夹：优先命令行参数，否则交互逐个输入。"""
    folders = []
    for a in cli_args:
        if os.path.isdir(a):
            folders.append(os.path.abspath(a))
        else:
            print(f"  ⚠ 跳过（不是文件夹）：{a}")
    if folders:
        return folders

    print("\n请输入要压缩的文件夹路径，一行一个；留空回车结束。")
    print("（可以直接把文件夹拖进窗口，会自动填路径）")
    while True:
        line = input(f"  文件夹 {len(folders) + 1}: ").strip().strip('"').strip("'")
        if not line:
            break
        if os.path.isdir(line):
            folders.append(os.path.abspath(line))
        else:
            print(f"  ⚠ 找不到这个文件夹，请重输：{line}")
    return folders


def pick_quality():
    print("\n选择导出质量：")
    print("  1) 95  接近无损，体积略减")
    print("  2) 85  推荐，肉眼几乎无差")
    print("  3) 75  明显减小，日常够用")
    print("  4) 65  最省空间，质量可见下降")
    print("  5) 自定义（1-100）")
    choice = ask("请选择 1-5", "2")
    mapping = {"1": 95, "2": 85, "3": 75, "4": 65}
    if choice in mapping:
        return mapping[choice]
    try:
        q = int(ask("输入自定义质量 1-100", "85"))
        return max(1, min(100, q))
    except ValueError:
        return 85


def pick_format():
    print("\n格式转换：")
    print("  1) 保持原格式")
    print("  2) 全部转 JPG（体积小，无透明通道）")
    print("  3) 全部转 WebP（同画质体积最小）")
    print("  4) 全部转 PNG（无损，适合图标/截图）")
    choice = ask("请选择 1-4", "1")
    return {"1": None, "2": ".jpg", "3": ".webp", "4": ".png"}.get(choice, None)


def pick_max_size():
    print("\n是否限制最大尺寸？（把超大图等比缩到不超过某个宽/高，留空=不限制）")
    val = ask("最大边像素，如 1920", "")
    if not val:
        return None
    try:
        return max(1, int(val))
    except ValueError:
        return None


def collect_images(folder, recursive):
    """收集一个文件夹下的所有图片路径。"""
    result = []
    if recursive:
        for root, _, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in SUPPORTED:
                    result.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.isfile(p) and os.path.splitext(f)[1].lower() in SUPPORTED:
                result.append(p)
    return result


def compress_one(src, dst, quality, target_ext, max_size):
    """压缩单张图片，返回 (原大小, 新大小)。"""
    orig_size = os.path.getsize(src)
    img = Image.open(src)

    # 限制最大尺寸（等比缩放）
    if max_size and (img.width > max_size or img.height > max_size):
        img.thumbnail((max_size, max_size), Image.LANCZOS)

    out_ext = (target_ext or os.path.splitext(src)[1]).lower()

    # JPG 不支持透明通道 → 贴白底转 RGB
    if out_ext in (".jpg", ".jpeg"):
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")

    os.makedirs(os.path.dirname(dst), exist_ok=True)

    save_kwargs = {}
    if out_ext in (".jpg", ".jpeg"):
        save_kwargs = {"quality": quality, "optimize": True, "progressive": True}
    elif out_ext == ".webp":
        save_kwargs = {"quality": quality, "method": 6}
    elif out_ext == ".png":
        save_kwargs = {"optimize": True}  # PNG 无损，质量参数无效

    img.save(dst, **save_kwargs)
    return orig_size, os.path.getsize(dst)


def main():
    print("=" * 50)
    print("        批量图片压缩工具")
    print("=" * 50)

    folders = pick_folders(sys.argv[1:])
    if not folders:
        print("\n没有选择任何文件夹，退出。")
        return

    recursive = ask("\n是否包含子文件夹？(y/n)", "n").lower().startswith("y")
    quality = pick_quality()
    target_ext = pick_format()
    max_size = pick_max_size()

    out_dir = ask(
        "\n输出目录（留空则在每个源文件夹旁建 '原名_compressed'）", ""
    ).strip().strip('"').strip("'")

    # 汇总待处理图片
    tasks = []  # (src, base_folder)
    for folder in folders:
        imgs = collect_images(folder, recursive)
        print(f"  {folder}  →  找到 {len(imgs)} 张图")
        for src in imgs:
            tasks.append((src, folder))

    if not tasks:
        print("\n没有找到任何图片，退出。")
        return

    print(f"\n共 {len(tasks)} 张图片待处理。质量={quality}，"
          f"格式={target_ext or '保持原样'}，"
          f"最大尺寸={max_size or '不限'}")
    if not ask("确认开始？(y/n)", "y").lower().startswith("y"):
        print("已取消。")
        return

    total_orig = total_new = 0
    ok = fail = 0
    print("\n开始压缩...\n")

    for i, (src, base) in enumerate(tasks, 1):
        rel = os.path.relpath(src, base)
        if out_dir:
            # 所有文件夹合并输出，用源文件夹名做子目录避免重名冲突
            dst_root = os.path.join(out_dir, os.path.basename(base.rstrip("/\\")))
        else:
            dst_root = base.rstrip("/\\") + "_compressed"

        dst = os.path.join(dst_root, rel)
        if target_ext:
            dst = os.path.splitext(dst)[0] + target_ext

        try:
            o, n = compress_one(src, dst, quality, target_ext, max_size)
            total_orig += o
            total_new += n
            ok += 1
            pct = (1 - n / o) * 100 if o else 0
            print(f"[{i}/{len(tasks)}] {rel}  {human(o)} → {human(n)}  (-{pct:.0f}%)")
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(tasks)}] ⚠ 失败：{rel}  ({e})")

    print("\n" + "=" * 50)
    print(f"完成！成功 {ok} 张，失败 {fail} 张。")
    if total_orig:
        saved = total_orig - total_new
        print(f"总大小：{human(total_orig)} → {human(total_new)}  "
              f"省下 {human(saved)}（-{saved / total_orig * 100:.0f}%）")
    print("输出位置：" + (out_dir if out_dir else "各源文件夹旁的 *_compressed"))
    print("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。")
    input("\n按回车键退出...")
