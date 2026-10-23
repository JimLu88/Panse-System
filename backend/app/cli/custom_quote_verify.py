"""Read-only recalculation. JSON stdin: {light: {...}, board: {...}}.

Uses service functions (no quote-log commit). Fingerprints all pricing/material
columns before and after; rolls back the session regardless of outcome.
"""
import hashlib
import json
import sys
from app.database import SessionLocal
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo, PricingSkuCosts
from app.models.material import Material
from app.services.custom_quote_v2_service import quote_light
from app.services.custom_quote_service import quote_from_spec, BoardSpec
from dataclasses import asdict


def fingerprints(db):
    out = {}
    for model in (PricingSku, PricingSkuPromo, PricingSkuCosts, Material):
        rows = db.query(model).order_by(model.id).all()
        data = [{c.name: getattr(r, c.name) for c in model.__table__.columns} for r in rows]
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()
        out[model.__tablename__] = {"count": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}
    return out


def run(db, payload):
    before = fingerprints(db)
    results = {}
    for name, args in payload.get("light", {}).items():
        results[name] = quote_light(db, **args)
    if payload.get("board"):
        args = dict(payload["board"])
        args["boards"] = [BoardSpec(**b) for b in args["boards"]]
        results["board"] = asdict(quote_from_spec(db, **args))
    after = fingerprints(db)
    if db.dirty or db.new or db.deleted or before != after:
        raise RuntimeError("readonly_quote_invariant_failed")
    return {"input": payload, "results": results, "before": before,
            "after": after, "prices_unchanged": True, "platform_actions": 0}


def main():
    payload = json.load(sys.stdin)
    with SessionLocal() as db:
        try:
            print(json.dumps(run(db, payload), ensure_ascii=False, default=str))
        finally:
            db.rollback()


if __name__ == "__main__":
    main()
