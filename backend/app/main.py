from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ai as ai_api
from app.api import audit as audit_api
from app.api import auth as auth_api
from app.api import bom as bom_api
from app.api import exceptions as exceptions_api
from app.api import feishu as feishu_api
from app.api import finance as finance_api
from app.api import inventory as inventory_api
from app.api import marketing as marketing_api
from app.api import match as match_api
from app.api import materials as materials_api
from app.api import orders as orders_api
from app.api import producibility as producibility_api
from app.api import product_inventory as product_inventory_api
from app.api import products as products_api
from app.api import quotes as quotes_api
from app.api import scanners as scanners_api
from app.config import get_settings
from app.middleware import AuditMiddleware

settings = get_settings()

app = FastAPI(title="Panse ERP", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)

app.include_router(auth_api.router)
app.include_router(audit_api.router)
app.include_router(materials_api.router)
app.include_router(products_api.router)
app.include_router(inventory_api.router)
app.include_router(product_inventory_api.router)
app.include_router(bom_api.router)
app.include_router(exceptions_api.router)
app.include_router(feishu_api.router)
app.include_router(match_api.router)
app.include_router(quotes_api.router)
app.include_router(orders_api.router)
app.include_router(producibility_api.router)
app.include_router(finance_api.router)
app.include_router(scanners_api.router)
app.include_router(ai_api.router)
app.include_router(marketing_api.router)


@app.get("/api/health")
def health():
    return {"ok": True}
