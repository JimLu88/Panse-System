"""删除店铺及其在本机 SQLite 中的关联数据（外键无 CASCADE，需按依赖顺序删除）。"""

from __future__ import annotations

import sqlite3


def delete_shop_cascade(conn: sqlite3.Connection, *, shop_id: str) -> None:
    """
    删除 shops 中一行，并清理所有引用该 brand_id+shop_id 的业务数据。
    shop_id 为主键；brand_id 从 shops 表读取。
    """
    row = conn.execute(
        "SELECT brand_id FROM shops WHERE shop_id = ? LIMIT 1",
        (shop_id,),
    ).fetchone()
    if not row:
        raise ValueError("店铺不存在或已删除")
    brand_id = str(row[0])
    bid, sid = brand_id, shop_id

    def ex(sql: str, params: tuple = ()) -> None:
        conn.execute(sql, params)

    # 图库 / 图片向量
    ex("DELETE FROM image_embeddings WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM image_library WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM gallery_entries WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    # 话术向量 → 话术
    ex("DELETE FROM kb_embeddings WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM kb_entries WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    # SKU → 产品
    ex("DELETE FROM product_skus WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM products WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    # 会话相关（先于 sessions / customers）
    ex("DELETE FROM messages WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM risk_checks WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM session_events WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM correction_cases WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM sessions WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    ex("DELETE FROM customer_tags WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM tags WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM customers WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    ex("DELETE FROM risk_dictionaries WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM style_examples WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM campaigns WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    ex("DELETE FROM push_recipients WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM push_templates WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM push_settings WHERE brand_id = ? AND shop_id = ?", (bid, sid))
    ex("DELETE FROM policy_settings WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    ex("DELETE FROM channels WHERE brand_id = ? AND shop_id = ?", (bid, sid))

    try:
        ex("DELETE FROM system_health_logs WHERE shop_id = ?", (sid,))
    except sqlite3.Error:
        pass

    ex("DELETE FROM shops WHERE shop_id = ?", (sid,))
    conn.commit()
