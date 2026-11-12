"""Small durable alert state machine; never interpret scan output as instructions."""
import datetime as dt
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

TZ = dt.timezone(dt.timedelta(hours=8))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def finding(key, title, detail, severity='warning'):
    return dict(key=key, title=title, detail=detail, severity=severity)


class Journal:
    """One sender owns both health and NAS alerts. Reservations precede I/O.

    Uncertain delivery is NEVER retried. A new episode gets a new id.
    Recovery is only notified if the original alarm has a real message receipt.
    """
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS issues (
            key TEXT PRIMARY KEY, scope TEXT, episode TEXT, active INTEGER,
            severity TEXT, title TEXT, detail TEXT, first REAL, last REAL);
          CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, key TEXT, episode TEXT, kind TEXT,
            severity TEXT, title TEXT, detail TEXT, created REAL,
            status TEXT, message_id TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, attempted REAL);
        ''')

    def observe(self, scope, findings, now=None):
        now = time.time() if now is None else now
        current = {x['key']: x for x in findings}
        with self.db:
            for key, f in current.items():
                old = self.db.execute('SELECT * FROM issues WHERE key=?', (key,)).fetchone()
                new = old is None or not old['active']
                episode = uuid.uuid4().hex if new else old['episode']
                escalation = bool(old and old['active'] and old['severity'] != 'critical'
                                  and f['severity'] == 'critical')
                if new or escalation:
                    if escalation:
                        self.db.execute("UPDATE events SET status='superseded' WHERE episode=? AND status='pending'", (episode,))
                    self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                    (uuid.uuid4().hex, key, episode, 'alarm' if new else 'escalation',
                                     f['severity'], f['title'], f['detail'], now, 'pending', None, None))
                self.db.execute('INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?,?)',
                                (key, scope, episode, 1, f['severity'], f['title'], f['detail'],
                                 now if new else old['first'], now))
            for old in self.db.execute('SELECT * FROM issues WHERE scope=? AND active=1', (scope,)).fetchall():
                if old['key'] in current:
                    continue
                self.db.execute('UPDATE issues SET active=0,last=? WHERE key=?', (now, old['key']))
                self.db.execute("UPDATE events SET status='resolved_unsent' WHERE episode=? AND status='pending'", (old['episode'],))
                sent = self.db.execute("SELECT 1 FROM events WHERE episode=? AND message_id IS NOT NULL LIMIT 1", (old['episode'],)).fetchone()
                if sent:
                    self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                    (uuid.uuid4().hex, old['key'], old['episode'], 'recovery',
                                     'info', old['title'], '本轮复查已恢复；不代表整台NAS已完成安全验收。',
                                     now, 'pending', None, None))

    def dispatch(self, sender, now=None, daily_cap=12):
        now = time.time() if now is None else now
        local = dt.datetime.fromtimestamp(now, TZ)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        # Reserve-count is deliberately conservative across failed/unknown sends.
        used = self.db.execute('SELECT COUNT(*) FROM attempts WHERE attempted>=?', (day_start,)).fetchone()[0]
        if used >= daily_cap:
            return {'sent': False, 'reason': 'daily_cap'}
        quiet = local.hour >= 23 or local.hour < 7
        rows = self.db.execute("SELECT * FROM events WHERE status='pending' ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END,created LIMIT 100").fetchall()
        rows = [r for r in rows if not quiet or r['severity'] == 'critical'][:12]
        if not rows:
            return {'sent': False, 'reason': 'quiet' if quiet else 'no_new_event'}
        batch = uuid.uuid4().hex
        text = '【群晖与ERP安全监测】\n' + local.strftime('%Y-%m-%d %H:%M 北京时间')
        selected = []
        for r in rows:
            tag = '已恢复' if r['kind'] == 'recovery' else ('严重' if r['severity'] == 'critical' else '需检查')
            line = '\n\n[%s] %s\n%s' % (tag, r['title'][:100], r['detail'][:350])
            if len((text + line).encode('utf-8')) > 8000:
                break
            text += line
            selected.append(r)
        with self.db:
            self.db.execute('INSERT INTO attempts VALUES (?,?)', (batch, now))
            for r in selected:
                self.db.execute("UPDATE events SET status='unknown',error=? WHERE id=? AND status='pending'", (batch, r['id']))
        try:
            message_id = sender(text, batch)
        except Exception as exc:
            # No exception text: third-party responses can contain credentials.
            return {'sent': False, 'status': 'unknown', 'error_type': type(exc).__name__, 'batch': batch}
        if not message_id or not message_id.startswith('om_'):
            return {'sent': False, 'status': 'unknown', 'batch': batch}
        with self.db:
            self.db.execute("UPDATE events SET status='sent',message_id=? WHERE error=? AND status='unknown'", (message_id, batch))
        return {'sent': True, 'message_id': message_id, 'events': len(selected), 'batch': batch}

    def summary(self):
        return {'active': self.db.execute("SELECT COUNT(*) FROM issues WHERE active=1 AND scope!='commission'").fetchone()[0],
                'pending': self.db.execute("SELECT COUNT(*) FROM events WHERE status='pending'").fetchone()[0],
                'unknown': self.db.execute("SELECT COUNT(*) FROM events WHERE status='unknown'").fetchone()[0]}
