"""#3 AI 视频切片：长视频/直播 → 多条短视频笔记草稿。

输入长视频转写(transcript)，按语义段切片，每片配钩子+标题，生成 content_type=video 草稿。
对标 Postiz 的 AI 视频切片 agent（一鱼多吃）。
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import ContentEvent, Draft, Topic
from . import compliance
from .llm_router import get_router


def clip_long_video(db: Session, title: str, transcript: str, category: str = "餐桌",
                    max_clips: int = 5) -> list[Draft]:
    """把长视频转写切成多条短视频笔记草稿。"""
    segments = _segment(transcript, max_clips)
    if not segments:
        raise ValueError("转写内容为空或无法切片")
    topic = Topic(title=f"[切片]{title}", category=category, keywords=[category],
                  topic_kind="evergreen", platform_targets=["xhs"])
    db.add(topic)
    db.flush()
    router = get_router()
    out: list[Draft] = []
    for i, seg in enumerate(segments, 1):
        hook = router.complete("generator.style_injection",
                               f"给这段家具视频内容起一个3秒抓人的小红书钩子：{seg[:50]}")
        hook = hook.split("\n")[0][:20] if isinstance(hook, str) else f"片段{i}"
        body = f"〔本片要点〕{seg}\n\n（来自长视频「{title}」第{i}段切片，配该段画面发布）"
        hits = compliance.scan_banned(hook + body)
        d = Draft(topic_id=topic.id, title=hook or f"切片{i}", body=body,
                  tags=[category, "家具测评"], content_type="video", narrative_units=[],
                  compliance=hits, ai_likeness=compliance.ai_likeness_score(body),
                  info_density=compliance.info_density(body),
                  must_fix={"cover_text_density": 0.0, "para2_marketing": "ok",
                            "personal_anchor_count": body.count("我")},
                  lineage={"prompt_template": "clip_v1", "source_video": title},
                  status="drafted")
        db.add(d)
        db.flush()
        db.add(ContentEvent(content_id=d.id, event_type="draft_generated",
                            payload={"clip_index": i}))
        out.append(d)
    db.commit()
    return out


def _segment(transcript: str, max_clips: int) -> list[str]:
    """按句号/换行粗分，再合并成 max_clips 段。"""
    parts = [p.strip() for p in re.split(r"[。\n]", transcript) if len(p.strip()) > 4]
    if not parts:
        return []
    n = min(max_clips, len(parts))
    size = max(1, len(parts) // n)
    return ["。".join(parts[i:i + size]) + "。" for i in range(0, len(parts), size)][:max_clips]
