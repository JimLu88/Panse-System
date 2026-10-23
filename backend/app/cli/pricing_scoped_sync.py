"""Explicit maintenance CLI. Default preview; never drives platform actions."""
import argparse
import hashlib
import json
from pathlib import Path
from app.services.pricing_scoped_sync import preview, apply, digest


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--preview-sha256')
    args = p.parse_args()
    raw = args.manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.manifest_sha256:
        raise ValueError('manifest_file_changed')
    manifest = json.loads(raw)
    if args.apply and not args.preview_sha256:
        raise ValueError('reviewed_preview_hash_required')
    from app.database import SessionLocal
    with SessionLocal() as db:
        try:
            if args.apply:
                result = apply(db, manifest, args.preview_sha256)
                db.commit()
                result['committed'] = True
            else:
                result = preview(db, manifest)
                result = {'preview': result, 'preview_sha256': digest(result), 'committed': False}
                db.rollback()
        except BaseException:
            db.rollback()
            raise
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
