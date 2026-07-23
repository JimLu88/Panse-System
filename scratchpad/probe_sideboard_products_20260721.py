# -*- coding: utf-8 -*-
"""Read-only probe for the sideboard custom-quote audit."""
import json
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

from app.database import SessionLocal
from app.models.bom import BomLine
from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import custom_quote_v2_service as v2


db = SessionLocal()
try:
    target_codes = ["PPS25250090403", "PPS24250080801", "PFG25250061201"]
    products = db.query(Product).filter(Product.code.in_(target_codes)).all()
    out = []
    for p in products:
        skus = [s for s in db.query(PricingSku).filter(PricingSku.product_code == p.code).all()
                if v2._is_quoteable_sku(s)]
        groups = defaultdict(list)
        for line in db.query(BomLine).filter(BomLine.product_code == p.code).all():
            m = db.query(Material).filter(Material.code == line.material_code).first()
            groups[line.sku or line.sku_code or "?"].append({
                "part": (line.remark or line.material_name or line.material_code),
                "material": (m.name if m else line.material_name),
                "unit": (m.unit if m else line.unit),
                "unit_price": float(m.price) if m and m.price is not None else None,
                "qty": float(line.qty_per_product or 0),
                "size_type": line.size_type,
            })
        out.append({
            "code": p.code, "name": p.name, "category": p.category, "main_material": p.main_material,
            "skus": [{"code": s.sku_code, "name": s.sku, "size": s.size_info,
                      "daily": float(s.daily_price) if s.daily_price is not None else None,
                      "accounting": float(s.accounting_cost) if s.accounting_cost is not None else None,
                      "physical": float(s.physical_cost) if s.physical_cost is not None else None,
                      "factory": float(s.factory_cost) if s.factory_cost is not None else None,
                      "wood": float(s.wood_cost) if s.wood_cost is not None else None,
                      "logistics": float(s.logistics_cost) if s.logistics_cost is not None else None,
                      "install": float(s.install_cost) if s.install_cost is not None else None,
                      "margin": float(s.gross_margin_rate) if s.gross_margin_rate is not None else None}
                     for s in skus],
            "bom_groups": {
                group: [f"{x['part']}|{x['material']}|{x['unit']}|{x['unit_price']}|qty{x['qty']}" for x in lines]
                for group, lines in groups.items()
                if not (group or "").startswith(p.code)
            },
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))
finally:
    db.close()
