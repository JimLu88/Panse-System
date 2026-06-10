"""Incremental SQLite migrations (additive columns / new tables)."""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    _ensure_columns(
        conn,
        "sessions",
        [
            ("anger_streak", "INTEGER NOT NULL DEFAULT 0"),
            ("followup_after_can", "INTEGER NOT NULL DEFAULT 0"),
        ],
    )
    _ensure_columns(
        conn,
        "policy_settings",
        [
            ("strong_reminder_until", "TEXT"),
            ("popup_auto_dismiss", "INTEGER NOT NULL DEFAULT 0"),
            ("jim_intercept_push", "INTEGER NOT NULL DEFAULT 1"),
            ("price_sensitive_handoff", "INTEGER NOT NULL DEFAULT 1"),
            ("real_photo_jim_intercept", "INTEGER NOT NULL DEFAULT 1"),
            ("handoff_soothe_line", "TEXT"),
            ("outbound_preview_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("outbound_preview_delay_seconds", "INTEGER NOT NULL DEFAULT 8"),
            ("jim_price_full_takeover", "INTEGER NOT NULL DEFAULT 1"),
            ("jim_photo_full_takeover", "INTEGER NOT NULL DEFAULT 1"),
        ],
    )
    _ensure_columns(
        conn,
        "products",
        [
            ("customization_scope", "TEXT NOT NULL DEFAULT ''"),
        ],
    )
    _ensure_columns(
        conn,
        "products",
        [
            ("customization_scope", "TEXT NOT NULL DEFAULT ''"),
        ],
    )
    _ensure_columns(
        conn,
        "kb_entries",
        [
            # 逗号分隔或 JSON 数组字符串，供元数据硬过滤；空表示全类目通用
            ("kb_tags", "TEXT NOT NULL DEFAULT ''"),
        ],
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_health_logs (
          log_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          brand_id TEXT,
          shop_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_logs_created ON system_health_logs(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_health_logs_type ON system_health_logs(event_type)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_settings (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          enabled INTEGER NOT NULL DEFAULT 0,
          anchor_started_at TEXT,
          last_bug_report_date TEXT,
          last_optimization_report_date TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_reports (
          report_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          report_kind TEXT NOT NULL,
          body_md TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO companion_settings(singleton, enabled) VALUES (1, 0)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gallery_entries (
          gallery_id TEXT PRIMARY KEY,
          brand_id TEXT NOT NULL,
          shop_id TEXT NOT NULL,
          title TEXT NOT NULL,
          local_path TEXT,
          taobao_url TEXT,
          tags TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
          FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_library (
          image_id TEXT PRIMARY KEY,
          brand_id TEXT NOT NULL,
          shop_id TEXT NOT NULL,
          category TEXT NOT NULL,
          local_path TEXT NOT NULL,
          question_label TEXT NOT NULL,
          match_keywords TEXT,
          send_count INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
          FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_image_library_shop_cat "
        "ON image_library(brand_id, shop_id, category, enabled)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS image_embeddings (
          embedding_id TEXT PRIMARY KEY,
          image_id TEXT NOT NULL,
          brand_id TEXT NOT NULL,
          shop_id TEXT NOT NULL,
          model_name TEXT NOT NULL,
          dim INTEGER NOT NULL,
          vector_blob BLOB NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE (image_id, model_name),
          FOREIGN KEY (image_id) REFERENCES image_library(image_id),
          FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
          FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_chat_sessions (
          mode TEXT PRIMARY KEY,
          summary_md TEXT NOT NULL DEFAULT '',
          history_json TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_chat_sessions (
          mode TEXT PRIMARY KEY,
          summary_md TEXT NOT NULL DEFAULT '',
          history_json TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection, table: str, cols: list[tuple[str, str]]) -> None:
    existing = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in cols:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
