# -*- coding: utf-8 -*-
"""图库缩略图预热 (用户 2026-06-25: 全部先跑完直接秒开; 有新图自动补; 每次都这么设置)。

- 遍历 GALLERY_ROOT, 为缺缓存的图片预生成 WebP。默认预热 **两个尺寸**:
  列表缩略 _THUMB_EDGE(320) + 点开大图预览 _PREVIEW_EDGE(1280) —— 这样"看墙"和"打开大图"都秒开。
- 复用 gallery._compressed(自带限并发信号量 + JPEG draft + 原子写), 不会压垮弱 CPU NAS。
- 幂等: 已有缓存的(按 源mtime+尺寸 哈希命中)直接跳过, 不进 PIL。
- 跳过时刷新临近清理阈值的缩略 mtime → 让月度 90 天清理只删真孤儿(源图已删), 不误删在用缩略。
- 预算式: max_new 限制单次最多生成多少张(跨两个尺寸合计), 避免单轮跑太久; 多轮(定时)累计
  跑完全量, 之后稳态近乎空跑(只 stat 判断)。新图(丢进图库文件夹 / API 上传)下一轮自动补上。

调用方:
- scheduler._job_gallery_thumb_warm (白天每小时增量, 避开夜间盘休眠; 全失败会抛错触发告警)。
- 一次性全量初始化: python -c "from app.services import gallery_warm_service as w; print(w.warm_thumbnails(workers=4))"
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

_logger = logging.getLogger("panse.gallery_warm")

# 缩略 mtime 超过这个年龄才在"跳过"时刷新一次(防临近 90 天月度清理被删); 平时不写盘, 避免无谓 IO。
_REFRESH_AGE_SEC = 60 * 86400


def warm_thumbnails(max_new: Optional[int] = None, edges: Optional[list] = None,
                    workers: int = 1) -> dict:
    """为图库内缺缓存的图片预生成缩略图/预览。

    Args:
        max_new: 本轮最多生成多少张 (跨所有尺寸合计; None=不限, 全量跑完)。超额留下一轮 → deferred。
        edges:   要预热的最长边列表 (None=[_THUMB_EDGE(320), _PREVIEW_EDGE(1280)])。
        workers: 并发生成线程数 (默认 1=顺序, 对线上最稳, 只占 1 个压缩信号量许可、把 CPU 让给实时浏览;
                 一次性批跑可调大。受 gallery._compress_sem 全局封顶, 不会压垮 NAS)。

    Returns:
        {available, files, scanned, generated, skipped, failed, deferred, attempted, edges}。
        attempted = 实际进入生成的张数; generated==0 而 attempted>0 = 系统性失败(调用方据此告警)。
    """
    from app.api import gallery as g

    root = g._root()
    if not root.exists():
        return {"available": False, "files": 0, "scanned": 0, "generated": 0,
                "skipped": 0, "failed": 0, "deferred": 0, "attempted": 0}
    if edges is None:
        edges = [g._THUMB_EDGE, g._PREVIEW_EDGE]
    budget = max_new if (max_new and max_new > 0) else None
    refresh_before = time.time() - _REFRESH_AGE_SEC

    # 1) 扫描: 列出缺缓存的 (源图, 尺寸); 已缓存的跳过(并按需刷新 mtime)
    files = scanned = skipped = failed = 0
    todo: list = []
    for f in root.rglob("*"):
        try:
            if not f.is_file() or f.suffix.lower() not in g._IMAGE_EXT:
                continue
            files += 1
            for edge in edges:
                scanned += 1
                tp = g._thumb_cache_path(f, edge)
                try:
                    st = tp.stat()
                except FileNotFoundError:
                    todo.append((f, edge))
                    continue
                skipped += 1
                if st.st_mtime < refresh_before:   # 临近清理阈值才刷新, 平时不写盘
                    try:
                        os.utime(tp, None)
                    except OSError:
                        pass
        except OSError:
            failed += 1
            continue

    # 2) 预算: 超额部分本轮不生成, 留给下一轮
    deferred = 0
    if budget is not None and len(todo) > budget:
        deferred = len(todo) - budget
        todo = todo[:budget]
    attempted = len(todo)

    # 3) 生成 (受 gallery._compress_sem 全局限并发封顶, workers 只是喂任务的快慢)
    def _one(item) -> bool:
        f, edge = item
        try:
            g._compressed(f, edge)
            return True
        except Exception as e:  # noqa: BLE001
            _logger.warning("预热失败 %s @%spx: %s", f, edge, e)
            return False

    generated = 0
    if attempted:
        if workers <= 1:
            for it in todo:
                if _one(it):
                    generated += 1
                else:
                    failed += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for ok in ex.map(_one, todo):
                    if ok:
                        generated += 1
                    else:
                        failed += 1

    result = {"available": True, "files": files, "scanned": scanned,
              "generated": generated, "skipped": skipped, "failed": failed,
              "deferred": deferred, "attempted": attempted, "edges": edges}
    if generated or failed or deferred:
        _logger.info("图库缩略图预热: %s", result)
    return result
