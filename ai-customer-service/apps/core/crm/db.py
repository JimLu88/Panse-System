from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = r"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=3000;

CREATE TABLE IF NOT EXISTS brands (
  brand_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shops (
  shop_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_code TEXT NOT NULL,
  display_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (brand_id, shop_code),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id)
);

CREATE TABLE IF NOT EXISTS channels (
  source_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  account_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  window_locator_json TEXT NOT NULL,
  roi_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  display_name TEXT,
  platform TEXT,
  external_ref TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (brand_id, shop_id, platform, external_ref),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  customer_id TEXT,
  state TEXT NOT NULL,
  manual_hold INTEGER NOT NULL DEFAULT 0,
  priority INTEGER NOT NULL DEFAULT 0,
  last_seen_at TEXT,
  last_event_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
  FOREIGN KEY (source_id) REFERENCES channels(source_id),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  direction TEXT NOT NULL,
  text TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  bbox_json TEXT,
  captured_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(session_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
  FOREIGN KEY (source_id) REFERENCES channels(source_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_time
  ON messages(session_id, captured_at);

CREATE TABLE IF NOT EXISTS risk_dictionaries (
  dict_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  dict_type TEXT NOT NULL,
  phrase TEXT NOT NULL,
  severity INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE INDEX IF NOT EXISTS idx_risk_dict_brand_type
  ON risk_dictionaries(brand_id, shop_id, dict_type, enabled);

CREATE TABLE IF NOT EXISTS risk_checks (
  risk_check_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  intent_level TEXT NOT NULL,
  categories_json TEXT NOT NULL,
  blocked INTEGER NOT NULL,
  reasons_json TEXT NOT NULL,
  reply_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(session_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
  FOREIGN KEY (source_id) REFERENCES channels(source_id)
);

CREATE INDEX IF NOT EXISTS idx_risk_checks_session_time
  ON risk_checks(session_id, created_at);

CREATE TABLE IF NOT EXISTS tags (
  tag_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (brand_id, shop_id, name),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS customer_tags (
  customer_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 1.0,
  last_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (customer_id, tag_id),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  FOREIGN KEY (tag_id) REFERENCES tags(tag_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_tags_brand_score
  ON customer_tags(brand_id, shop_id, score DESC, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS kb_entries (
  kb_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  entry_type TEXT NOT NULL DEFAULT 'normal',
  enabled INTEGER NOT NULL DEFAULT 1,
  start_at TEXT,
  end_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS kb_embeddings (
  kb_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  embedding_ref TEXT NOT NULL,
  model TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (kb_id) REFERENCES kb_entries(kb_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS push_settings (
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  enabled_wechat INTEGER NOT NULL DEFAULT 0,
  enabled_wecom INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (brand_id, shop_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS push_recipients (
  recipient_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  address TEXT NOT NULL,
  display_name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS push_templates (
  template_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  scene TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (brand_id, shop_id, scene),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS policy_settings (
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  followup_level TEXT NOT NULL DEFAULT 'medium',
  anger_hit_threshold INTEGER NOT NULL DEFAULT 2,
  unknown_topic_threshold REAL NOT NULL DEFAULT 0.45,
  unknown_topic_fallback_version INTEGER NOT NULL DEFAULT 1,
  ocr_polling_seconds INTEGER NOT NULL DEFAULT 8,
  strong_reminder INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (brand_id, shop_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  name TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS session_events (
  event_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  evidence_confidence REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
  FOREIGN KEY (session_id) REFERENCES sessions(session_id),
  FOREIGN KEY (source_id) REFERENCES channels(source_id)
);

CREATE INDEX IF NOT EXISTS idx_session_events_session_time
  ON session_events(session_id, created_at);

CREATE TABLE IF NOT EXISTS style_examples (
  example_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  user_text TEXT NOT NULL,
  seller_text TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  pinned INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE INDEX IF NOT EXISTS idx_style_examples_shop_enabled
  ON style_examples(brand_id, shop_id, enabled, pinned);

CREATE TABLE IF NOT EXISTS products (
  product_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  product_code TEXT NOT NULL,
  category TEXT,
  name TEXT NOT NULL,
  product_link TEXT,
  listing_status TEXT,
  copywriting TEXT,
  main_material TEXT,
  sub_material TEXT,
  size_details TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (brand_id, shop_id, product_code),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS product_skus (
  sku_id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  sku_name TEXT,
  sku_code TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (brand_id, shop_id, sku_code),
  FOREIGN KEY (product_id) REFERENCES products(product_id),
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id)
);

CREATE TABLE IF NOT EXISTS correction_cases (
  case_id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  customer_text TEXT NOT NULL,
  ai_reply_json TEXT NOT NULL,
  human_reply_text TEXT NOT NULL,
  adopted_to_kb INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY (brand_id) REFERENCES brands(brand_id),
  FOREIGN KEY (shop_id) REFERENCES shops(shop_id),
  FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # NOTE: Keep check_same_thread=True by default; do NOT share connections across threads.
    # If you need concurrency, open one connection per thread/task.
    conn = sqlite3.connect(str(db_path), timeout=3.0)
    conn.row_factory = sqlite3.Row
    _configure_connection(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """
    Initializes schema and enables WAL. Safe to call multiple times.
    """
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    from apps.core.crm.migrate import migrate

    migrate(conn)


def _configure_connection(conn: sqlite3.Connection) -> None:
    """
    Apply connection-local pragmas.

    Important: PRAGMAs are per-connection in SQLite, so we must configure every new connection.
    """
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=3000;")

