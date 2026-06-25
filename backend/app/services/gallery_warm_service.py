# -*- coding: utf-8 -*-
"""图库缩略图预热 (用户 2026-06-25: 全部先跑完直接秒开; 有新图自动补; 每次都这么设置)。

- 遍历 GALLERY_ROOT, 为缺缩略图的图片预生成 WebP 缩略 (默认 _THUMB_EDGE=320)。
- 复用 gallery._compressed(自带限并发信号量 + JPEG draft + 原子写), 不会压垮弱 CPU NAS。
- 幂等: 已有缓存的(按 源mtime+尺寸 哈希命中)直接跳过, 不进 PIL。
- 预算式: max_new 限制单次最多生成多少张, 避免单轮跑太久; 多轮(定时)累计跑完全量,
  之后稳态近乎空跑(只 stat 判断)。新图(直接丢进图库文件夹 / API 上传)下一轮自动补上。

调用方:
- scheduler._job_gallery_thumb_warm (白天每小时增量, 避开夜间盘休眠)。
- 一次性全量初始化: python -c "from app.services import gallery_warm_service as w; print(w.warm_thumbnails(workers=4))"
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

_logger = logging.getLogger("panse.gallery_warm")


def warm_thumbnails(max_new: Optional[int] = None, edge: Optional[int] = None,
                    workers: int = 1) -> dict:
    """为图库内缺缩略图的图片预生成缩略图。

    Args:
        max_new: 本轮最多生成多少张 (None=不限, 全量跑完)。超额的留给下一轮, 记入 deferred。
        edge:    缩略图最长边 (None=用 gallery._THUMB_EDGE)。
        workers: 并发生成线程数 (默认 1=顺序, 最稳; 一次性批跑可调大。受 _compress_sem 全局封顶, 不会压垮 NAS)。

    Returns:
        {available, scanned, generated, skipped, failed, deferred} 计数。
    """
    from app.api import gallery as g

    root = g._root()
    if not root.exists():
        return {"available": False, "scanned": 0, "generated": 0,
                "skipped": 0, "failed": 0, "deferred": 0}
    edge = edge or g._THUMB_EDGE
    budget = max_new if (max_new and max_new > 0) else None

    # 1) 扫描: 找出缺缩略图的源图 (已缓存的快速跳过, 不进 PIL)
    scanned = skipped = failed = 0
    todo: list = []
    for f in root.rglob("*"):
        try:
            if not f.is_file() or f.suffix.lower() not in g._IMAGE_EXT:
                continue
            scanned += 1
            if g._thumb_cache_path(f, edge).exists():
                skipped += 1
                continue
            todo.append(f)
        except OSError:
            failed += 1
            continue

    # 2) 预算: 超额部分本轮不生成, 留给下一轮
    deferred = 0
    if budget is not None and len(todo) > budget:
        deferred = len(todo) - budget
        todo = todo[:budget]

    # 3) 生成 (受 gallery._compress_sem 全局限并发封顶, workers 只是喂任务的快慢)
    def _one(f) -> bool:
        try:
            g._compressed(f, edge)
            return True
        except Exception as e:  # noqa: BLE001
            _logger.warning("预热缩略图失败 %s: %s", f, e)
            return False

    generated = 0
    if not todo:
        pass
    elif workers <= 1:
        for f in todo:
            if _one(f):
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

    result = {"available": True, "scanned": scanned, "generated": generated,
              "skipped": skipped, "failed": failed, "deferred": deferred}
    if generated or failed or deferred:
        _logger.info("图库缩略图预热: %s", result)
    return result
