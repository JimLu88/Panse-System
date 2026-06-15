"""#7 受众超个性化：同一选题按人群画像生成多版本精准内容。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import AUDIENCE_SEGMENTS
from ..models import ContentEvent, Draft, Topic
from . import compliance
from .llm_router import get_router


def generate_for_segments(db: Session, topic_id: int, segments: list[str],
                          account_id: int | None = None) -> list[Draft]:
    """对一个选题，按指定人群各出一篇草稿（角度/痛点不同）。"""
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise ValueError("选题不存在")
    router = get_router()
    out: list[Draft] = []
    for seg in segments:
        angle = AUDIENCE_SEGMENTS.get(seg, seg)
        prompt = (f"为家具品牌写小红书笔记，选题：{topic.title}，"
                  f"目标人群：{seg}（{angle}）。闺蜜口吻，含真实细节。")
        body = router.complete("generator.style_injection", prompt)
        title = f"[{seg}]{topic.title}"[:20]
        hits = compliance.scan_banned(title + body)
        d = Draft(topic_id=topic.id, account_id=account_id, title=title, body=body,
                  tags=topic.keywords + [seg], narrative_units=[],
                  compliance=hits, ai_likeness=compliance.ai_likeness_score(body),
                  info_density=compliance.info_density(body),
                  must_fix={"cover_text_density": 0.4, "para2_marketing": "ok",
                            "personal_anchor_count": body.count("我")},
                  lineage={"prompt_template": "segment_v1", "segment": seg,
                           "generated_by_model": router.provider},
                  status="drafted")
        db.add(d)
        db.flush()
        db.add(ContentEvent(content_id=d.id, event_type="draft_generated",
                            payload={"segment": seg}))
        out.append(d)
    db.commit()
    return out
