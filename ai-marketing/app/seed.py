"""种子数据：家具品牌账号矩阵。运行 `python -m app.seed`。

矩阵建议（评审报告）：2 品牌专业号 + 2 家居人设小号 + 1 垂类测评 + 1 备用 + 2 知乎。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from .database import SessionLocal, init_db
from .models import Account

_SEED_ACCOUNTS = [
    # (昵称, 平台, 角色, 性格档案, 阶段, 粉丝)
    ("畔色家居官方", "xhs", "brand",
     {"catchphrases": ["实木控必看", "细节决定质感"], "emoji_preference": ["🌿", "✨"],
      "first_person_ratio": 0.4, "visual_style": "morandi_warm"}, "active", 12000),
    ("孚格木作", "xhs", "brand",
     {"catchphrases": ["原木的温度", "用十年的家具"], "emoji_preference": ["🪵", "🤎"],
      "first_person_ratio": 0.4, "visual_style": "wood_natural"}, "active", 8600),
    ("小户型改造日记", "xhs", "persona",
     {"catchphrases": ["真的会谢", "建议人手一个"], "emoji_preference": ["🏠", "😭", "✨"],
      "first_person_ratio": 0.8, "visual_style": "cozy_small"}, "active", 3200),
    ("装修避坑酱", "xhs", "persona",
     {"catchphrases": ["踩过的坑别再踩", "听我一句劝"], "emoji_preference": ["⚠️", "🙋", "💡"],
      "first_person_ratio": 0.75, "visual_style": "real_life"}, "trial", 800),
    ("实木测评室", "xhs", "review",
     {"catchphrases": ["数据说话", "拆给你看"], "emoji_preference": ["🔬", "📐"],
      "first_person_ratio": 0.5, "visual_style": "clean_test"}, "nurturing", 150),
    ("备用孵化号01", "xhs", "spare",
     {"catchphrases": [], "emoji_preference": [], "first_person_ratio": 0.6}, "nurturing", 30),
    ("畔色知乎机构号", "zhihu", "zhihu",
     {"catchphrases": ["从专业角度", "结构化拆解"], "emoji_preference": [],
      "first_person_ratio": 0.3, "visual_style": "rational"}, "active", 5400),
    ("一个做家具的答主", "zhihu", "zhihu",
     {"catchphrases": ["行业内幕", "说点实话"], "emoji_preference": [],
      "first_person_ratio": 0.6}, "trial", 1200),
]


def run() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.scalar(select(Account).limit(1))
        if existing:
            print("已有账号数据，跳过种子。如需重置请删除 marketing.db。")
            return
        days_ago = dt.date.today() - dt.timedelta(days=40)
        for nick, plat, role, persona, stage, fans in _SEED_ACCOUNTS:
            db.add(Account(
                nickname=nick, platform=plat, role=role, voice_persona=persona,
                negative_words=["全网最低", "强烈推荐"], topic_affinity={"餐桌": 0.9, "柜类": 0.6},
                follower_count=fans, stage=stage, stage_since=days_ago,
                post_alive_rate=0.95, health_score=100, health_flag="green",
            ))
        db.commit()
        print(f"已写入 {len(_SEED_ACCOUNTS)} 个种子账号（小红书6 + 知乎2）。")

        # 自动预填内容：知乎20题答案初稿 + 各品类选题/草稿，开箱即有料
        from .services import content_seeder, crawl_service, inbox_comments, ops_content
        n = ops_content.generate_all_zhihu_answers(db)
        print(f"已 AI 生成 {n} 篇知乎答案初稿。")
        r = content_seeder.seed_batch(db, per_category=1)
        print(f"已预热 {r['topics']} 个选题 / {r['drafts']} 篇小红书草稿。")
        # Phase2：竞品爆文 + 品牌舆情（评论需先有已发布笔记，开箱时为空，运营发布后抓）
        for cat in ("餐桌", "茶几", "柜类"):
            crawl_service.mine_hot_notes(db, cat)
        m = inbox_comments.scan_mentions(db)
        print(f"已抓取竞品爆文(含低粉爆文) + {m['added']} 条品牌/竞品舆情。")
        # Phase4：演示数字人(已授权就绪，可直接渲染口播脚本)
        from .services import avatar_service
        avatar_service.create_avatar(db, name="畔色老板IP", real_person="老板",
                                     face_ref="demo_face.png", voice_sample_ref="demo_voice.wav",
                                     authorized=True, persona={"tone": "亲和实在"})
        print("已创建演示数字人「畔色老板IP」(已授权)。")
    finally:
        db.close()


if __name__ == "__main__":
    run()
