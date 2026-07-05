"""Pazaryeri bağlantı yönetimi — kullanıcı başına mağaza hesapları.

Trendyol: API anahtarlarıyla canlı bağlantı (ürün gönderimi + kategori çekme).
Hepsiburada / N11 / Amazon: bağlantı kaydı tutulur; yorum çekme (link ile) ve
CSV importu her platformda çalışır, API tabanlı ürün gönderimi Trendyol'dadır.
"""
import json
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import db
from .trendyol_client import TrendyolClient

router = APIRouter(prefix="/api/connections", tags=["connections"])

PLATFORMS = {
    "trendyol": {
        "name": "Trendyol",
        "fields": [
            {"key": "seller_id", "label": "Satıcı ID", "required": True},
            {"key": "api_key", "label": "API Key", "required": True},
            {"key": "api_secret", "label": "API Secret", "required": True, "secret": True},
            {"key": "env", "label": "Ortam (stage/prod)", "required": False},
        ],
        "capabilities": ["AI ile ürün ilanı (API)", "Kategori/özellik eşleştirme", "Yorum çekme", "CSV analiz"],
    },
    "hepsiburada": {
        "name": "Hepsiburada",
        "fields": [
            {"key": "merchant_id", "label": "Merchant ID", "required": True},
            {"key": "username", "label": "API Kullanıcı Adı", "required": False},
            {"key": "api_key", "label": "Servis Şifresi (API Key)", "required": False, "secret": True},
        ],
        "capabilities": ["Sipariş & paket yönetimi (API)", "Fiyat/stok güncelleme (API)", "Yorum çekme", "CSV analiz"],
    },
    "n11": {
        "name": "N11",
        "fields": [
            {"key": "api_key", "label": "App Key", "required": False, "secret": True},
            {"key": "api_secret", "label": "App Secret", "required": False, "secret": True},
        ],
        "capabilities": ["Sipariş listeleme (API)", "Ürün & fiyat/stok (API)", "Yorum çekme", "CSV analiz"],
    },
    "amazon": {
        "name": "Amazon TR",
        "fields": [
            {"key": "seller_id", "label": "Satıcı (Merchant) ID", "required": False},
            {"key": "refresh_token", "label": "SP-API Refresh Token", "required": False, "secret": True},
            {"key": "lwa_client_id", "label": "LWA Client ID", "required": False, "secret": True},
            {"key": "lwa_client_secret", "label": "LWA Client Secret", "required": False, "secret": True},
        ],
        "capabilities": ["Sipariş listeleme (SP-API)", "Fiyat/stok güncelleme (SP-API)", "Finans & buybox (SP-API)", "CSV analiz"],
    },
}


def _require_user(request: Request) -> dict:
    from . import auth

    user = auth.get_session_user(request)
    if not user:
        raise HTTPException(401, "Bu işlem için giriş yapmalısın.")
    return user


def _mask(row: dict) -> dict:
    creds = db.connection_credentials(row)
    platform = PLATFORMS.get(row["platform"], {"fields": []})
    masked = {}
    for f in platform["fields"]:
        v = creds.get(f["key"], "")
        if v and f.get("secret"):
            masked[f["key"]] = "••••" + str(v)[-4:]
        else:
            masked[f["key"]] = v
    return {
        "id": row["id"],
        "platform": row["platform"],
        "platform_name": platform.get("name", row["platform"]),
        "label": row["label"],
        "status": row["status"],
        "created_at": row["created_at"],
        "last_check": row["last_check"],
        "credentials": masked,
        "capabilities": platform.get("capabilities", []),
    }


def list_user_connections(user_id: str) -> list[dict]:
    return db.query("SELECT * FROM connections WHERE user_id=? ORDER BY created_at", (user_id,))


def trendyol_client_for(user_id: str) -> TrendyolClient:
    """Kullanıcının Trendyol bağlantısından istemci üretir; bağlantı yoksa mock çalışır."""
    for row in list_user_connections(user_id):
        if row["platform"] == "trendyol":
            creds = db.connection_credentials(row)
            return TrendyolClient(
                seller_id=str(creds.get("seller_id", "")),
                api_key=str(creds.get("api_key", "")),
                api_secret=str(creds.get("api_secret", "")),
                env=str(creds.get("env") or "prod"),
            )
    return TrendyolClient()


def _test_connection(platform: str, creds: dict) -> tuple[str, str]:
    """(status, message) döner. Yalnızca Trendyol canlı doğrulanabilir."""
    if platform == "trendyol":
        client = TrendyolClient(
            seller_id=str(creds.get("seller_id", "")),
            api_key=str(creds.get("api_key", "")),
            api_secret=str(creds.get("api_secret", "")),
            env=str(creds.get("env") or "prod"),
        )
        if client.mock:
            return "unverified", "Anahtarlar eksik — bağlantı kaydedildi ama doğrulanamadı."
        try:
            cats = client.get_leaf_categories()
            if cats:
                return "active", f"Bağlantı doğrulandı — {len(cats)} kategori erişilebilir."
            return "error", "API yanıt verdi ama kategori listesi boş döndü."
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return "error", "API anahtarları reddedildi (401/403) — Satıcı Paneli > Entegrasyon Bilgileri'nden kontrol et."
            return "error", f"Trendyol API hatası: {e.response.status_code}"
        except Exception as e:
            return "error", f"Bağlantı kurulamadı: {str(e)[:150]}"
    # Diğer platformlar: kayıt bazlı (API doğrulaması eklenene kadar)
    return "active", "Bağlantı kaydedildi — yorum çekme ve CSV analizi bu mağaza için aktif."


# ---- API uçları ----

class ConnectionRequest(BaseModel):
    platform: str
    label: str = ""
    credentials: dict = {}


@router.get("/platforms")
def platforms():
    return [{"key": k, **v} for k, v in PLATFORMS.items()]


@router.get("")
def my_connections(request: Request):
    user = _require_user(request)
    return [_mask(r) for r in list_user_connections(user["id"])]


@router.post("")
def add_connection(req: ConnectionRequest, request: Request):
    from . import billing

    user = _require_user(request)
    if req.platform not in PLATFORMS:
        raise HTTPException(400, "Bilinmeyen platform.")
    existing = list_user_connections(user["id"])
    if any(c["platform"] == req.platform for c in existing):
        raise HTTPException(409, f"{PLATFORMS[req.platform]['name']} bağlantın zaten var — önce onu düzenle veya sil.")
    store_limit = billing.limit_for(user["id"], "stores")
    if store_limit != -1 and len(existing) >= store_limit:
        raise HTTPException(
            402,
            f"Paketin en fazla {store_limit} pazaryeri bağlantısına izin veriyor. "
            "Daha fazla mağaza için paketini yükselt.",
        )
    status, message = _test_connection(req.platform, req.credentials)
    conn_id = db.new_id()
    db.execute(
        "INSERT INTO connections (id, user_id, platform, label, credentials, status, created_at, last_check) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (conn_id, user["id"], req.platform, req.label.strip(), json.dumps(req.credentials),
         status, db.now(), db.now()),
    )
    row = db.query_one("SELECT * FROM connections WHERE id=?", (conn_id,))
    return {"connection": _mask(row), "message": message}


@router.put("/{conn_id}")
def update_connection(conn_id: str, req: ConnectionRequest, request: Request):
    user = _require_user(request)
    row = db.query_one("SELECT * FROM connections WHERE id=? AND user_id=?", (conn_id, user["id"]))
    if not row:
        raise HTTPException(404, "Bağlantı bulunamadı.")
    # Maskeli (••••) gönderilen alanları eski değerle koru
    old = db.connection_credentials(row)
    creds = {}
    for k, v in req.credentials.items():
        creds[k] = old.get(k, "") if isinstance(v, str) and v.startswith("••••") else v
    status, message = _test_connection(row["platform"], creds)
    db.execute(
        "UPDATE connections SET label=?, credentials=?, status=?, last_check=? WHERE id=?",
        (req.label.strip(), json.dumps(creds), status, db.now(), conn_id),
    )
    updated = db.query_one("SELECT * FROM connections WHERE id=?", (conn_id,))
    return {"connection": _mask(updated), "message": message}


@router.post("/{conn_id}/test")
def test_connection(conn_id: str, request: Request):
    user = _require_user(request)
    row = db.query_one("SELECT * FROM connections WHERE id=? AND user_id=?", (conn_id, user["id"]))
    if not row:
        raise HTTPException(404, "Bağlantı bulunamadı.")
    status, message = _test_connection(row["platform"], db.connection_credentials(row))
    db.execute("UPDATE connections SET status=?, last_check=? WHERE id=?", (status, db.now(), conn_id))
    return {"status": status, "message": message}


@router.delete("/{conn_id}")
def delete_connection(conn_id: str, request: Request):
    user = _require_user(request)
    row = db.query_one("SELECT * FROM connections WHERE id=? AND user_id=?", (conn_id, user["id"]))
    if not row:
        raise HTTPException(404, "Bağlantı bulunamadı.")
    db.execute("DELETE FROM connections WHERE id=?", (conn_id,))
    return {"ok": True}
