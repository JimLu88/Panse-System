"""Explicit one-time owner credential reuse; no business database connection.

The verifier arrives only via stdin, never argv, receipts, or normal exports.
Existing configured accounts cannot be overwritten by this bootstrap.
"""
import hashlib
import hmac
import json
import os
import re
import sys


def verify_password(password, config):
    try:
        scheme = config.get('password_scheme', 'scrypt')
        if scheme == 'bcrypt':
            import bcrypt
            return bcrypt.checkpw(password.encode(), config['password_hash'].encode())
        if scheme != 'scrypt':
            return False
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(config['salt']), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(value, config['password_hash'])
    except (ValueError, TypeError, KeyError):
        return False


def import_owner(store, payload):
    from app.isolated_store import dumps, safe_child
    if (payload.get('source') != 'panse-owner-user-authorized'
            or payload.get('role') != 'admin' or payload.get('active') is not True
            or payload.get('must_change_password') is not False
            or not isinstance(payload.get('username'), str)
            or not payload['username'].strip() or len(payload['username']) > 64
            or not re.fullmatch(r'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}', payload.get('password_hash', ''))):
        raise ValueError('confirmed_active_owner_required')
    path = safe_child(store.root, 'store.json')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        config = json.loads(path.read_text(encoding='utf8'))
        if config.get('store_id') != store.owner or config.get('instance_id') != store.instance:
            raise ValueError('store_identity_changed')
        if config.get('username') or config.get('password_hash'):
            raise ValueError('account_already_configured')
        config.update(username=payload['username'], password_hash=payload['password_hash'],
                      password_scheme='bcrypt', account_source='panse-owner-user-authorized')
        pending = safe_child(store.root, 'store.account.pending')
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf8') as f:
                f.write(dumps(config)); f.flush(); os.fsync(f.fileno())
            os.replace(pending, path)
        finally:
            if pending.exists(): pending.unlink()
        db.execute('DELETE FROM sessions')
        store.audit(db, 'import_authorized_owner_verifier', store.owner)
    return {'store_id': store.owner, 'account_ready': True, 'username': payload['username'],
            'plaintext_password_read': False, 'business_data_copied': False}


if __name__ == '__main__':
    from app.isolated_store import Store
    store = Store(os.environ['ISOLATED_STORE_ROOT'], os.environ['ISOLATED_STORE_ID'])
    print(json.dumps(import_owner(store, json.load(sys.stdin))))
