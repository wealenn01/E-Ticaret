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


def _test_hepsiburada(creds: dict) -> tuple[str, str]:
    """Hepsiburada kimlik bilgilerini Listing API ile doğrular."""
    import base64 as _b64
    merchant_id = str(creds.get("merchant_id", "") or "")
    username = str(creds.get("username", "") or merchant_id)
    password = str(creds.get("api_key", "") or creds.get("password", "") or "")
    if not merchant_id or not password:
        return "unverified", "Merchant ID veya Servis Şifresi eksik — bağlantı kaydedildi ama doğrulanamadı."
    token = _b64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "User-Agent": merchant_id,
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(
                f"https://listing-external.hepsiburada.com/listings/merchantid/{merchant_id}",
                headers=headers, params={"offset": 0, "limit": 1},
            )
            if r.status_code == 200:
                total = r.json().get("totalCount", "?")
                return "active", f"Bağlantı doğrulandı — {total} ürün erişilebilir."
            if r.status_code in (401, 403):
                return "error", "Kimlik doğrulama başarısız (401/403) — Merchant ID ve Servis Şifresini kontrol et."
            return "unverified", f"API {r.status_code} döndürdü — anahtarlar kaydedildi."
    except Exception as e:
        return "unverified", f"Bağlantı test edilemedi: {str(e)[:120]} — anahtarlar kaydedildi."


def _test_n11(creds: dict) -> tuple[str, str]:
    """N11 kimlik bilgilerini REST API ile doğrular."""
    app_key = str(creds.get("api_key", "") or "")
    app_secret = str(creds.get("api_secret", "") or "")
    if not app_key or not app_secret:
        return "unverified", "App Key veya App Secret eksik — bağlantı kaydedildi ama doğrulanamadı."
    headers = {"appkey": app_key, "appsecret": app_secret, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get("https://api.n11.com/ms/product-query", headers=headers,
                      params={"page": 0, "size": 1})
            if r.status_code == 200:
                total = r.json().get("totalElements", r.json().get("total", "?"))
                return "active", f"Bağlantı doğrulandı — {total} ürün erişilebilir."
            if r.status_code in (401, 403):
                return "error", "Kimlik doğrulama başarısız (401/403) — App Key ve App Secret'ı kontrol et."
            return "unverified", f"N11 API {r.status_code} döndürdü — anahtarlar kaydedildi."
    except Exception as e:
        return "unverified", f"Bağlantı test edilemedi: {str(e)[:120]} — anahtarlar kaydedildi."


def _test_amazon(creds: dict) -> tuple[str, str]:
    """Amazon LWA token alarak SP-API kimliklerini doğrular."""
    refresh_token = str(creds.get("refresh_token", "") or "")
    client_id = str(creds.get("lwa_client_id", "") or "")
    client_secret = str(creds.get("lwa_client_secret", "") or "")
    if not refresh_token or not client_id or not client_secret:
        return "unverified", "SP-API kimlik bilgileri eksik — bağlantı kaydedildi ama doğrulanamadı."
    try:
        with httpx.Client(timeout=15) as c:
            r = c.post("https://api.amazon.com/auth/o2/token", data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            })
            if r.status_code == 200 and r.json().get("access_token"):
                return "active", "Amazon SP-API kimlik doğrulaması başarılı."
            if r.status_code in (400, 401):
                err = r.json().get("error_description", r.json().get("error", ""))
                return "error", f"Amazon kimlik doğrulama başarısız: {err[:120]}"
            return "unverified", f"Amazon LWA {r.status_code} döndürdü — anahtarlar kaydedildi."
    except Exception as e:
        return "unverified", f"Bağlantı test edilemedi: {str(e)[:120]} — anahtarlar kaydedildi."


def _test_connection(platform: str, creds: dict) -> tuple[str, str]:
    """(status, message) döner; her platform gerçek API çağrısıyla doğrulanır."""
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
            # Kimlik doğrulaması gerektiren authenticated endpoint'i test et
            from app.marketplace_api.trendyol_api import TrendyolAPI
            api = TrendyolAPI(creds)
            products = api._req("GET", f"/product/sellers/{client.seller_id}/products",
                                params={"page": 0, "size": 1})
            count = products.get("totalElements", products.get("content", []))
            if isinstance(count, list):
                count = "?"
            return "active", f"Bağlantı doğrulandı — Satıcı ID geçerli, ürünler erişilebilir ({count} ürün)."
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return "error", "API anahtarları reddedildi (401/403) — Satıcı Paneli > Entegrasyon Bilgileri'nden kontrol et."
            return "error", f"Trendyol API hatası: {e.response.status_code}"
        except Exception as e:
            return "error", f"Bağlantı kurulamadı: {str(e)[:150]}"
    if platform == "hepsiburada":
        return _test_hepsiburada(creds)
    if platform == "n11":
        return _test_n11(creds)
    if platform == "amazon":
        return _test_amazon(creds)
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
