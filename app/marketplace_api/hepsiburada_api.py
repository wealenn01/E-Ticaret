"""Hepsiburada entegrasyon istemcisi.

Dokümantasyon: https://developers.hepsiburada.com
Auth: Basic (merchant kullanıcı adı + servis şifresi), User-Agent zorunlu.

Servisler:
  Listing (fiyat/stok):  https://listing-external.hepsiburada.com
  OMS (sipariş/paket):   https://oms-external.hepsiburada.com
İadeler ve soru-cevap uçları merchant onayı gerektirir; anahtar girilmediyse
tüm modüller DEMO veriyle çalışır.
"""
import base64
from typing import Any, Optional

import httpx

from . import base as B

LISTING_BASE = "https://listing-external.hepsiburada.com"
OMS_BASE = "https://oms-external.hepsiburada.com"

STATUS_MAP = {
    "Open": "Created", "Packaged": "Picking", "InTransit": "Shipped",
    "Shipped": "Shipped", "Delivered": "Delivered", "CancelledByCustomer": "Cancelled",
    "CancelledByMerchant": "Cancelled", "Returned": "Returned",
}


class HepsiburadaAPI(B.MarketplaceClient):
    platform = "hepsiburada"
    platform_name = "Hepsiburada"

    def __init__(self, creds: Optional[dict] = None):
        super().__init__(creds)
        self.merchant_id = str(self.creds.get("merchant_id", "") or "")
        self.username = str(self.creds.get("username", "") or self.merchant_id)
        self.password = str(self.creds.get("api_key", "") or self.creds.get("password", "") or "")

    @property
    def configured(self) -> bool:
        return bool(self.merchant_id and self.password)

    def _headers(self) -> dict:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "User-Agent": self.merchant_id,
            "Accept": "application/json",
        }

    def _req(self, base_url: str, method: str, path: str, *, params: Optional[dict] = None, json: Any = None) -> Any:
        try:
            with httpx.Client(timeout=30) as c:
                r = c.request(method, f"{base_url}{path}", headers=self._headers(), params=params, json=json)
                r.raise_for_status()
                return r.json() if r.content else {}
        except httpx.HTTPStatusError as e:
            raise B.MarketplaceError(f"Hepsiburada API {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise B.MarketplaceError(f"Hepsiburada bağlantı hatası: {str(e)[:150]}") from e

    # ---- Ürünler (listing) ----

    def list_products(self, page: int = 0, size: int = 50) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_products())
        data = self._req(LISTING_BASE, "GET", f"/listings/merchantid/{self.merchant_id}",
                         params={"offset": page * size, "limit": size})
        rows = []
        for l in data.get("listings", []):
            rows.append({
                "sku": l.get("merchantSku", ""),
                "barcode": l.get("hepsiburadaSku", ""),
                "title": l.get("productName", "") or l.get("merchantSku", ""),
                "price": float(l.get("price", 0) or 0),
                "list_price": float(l.get("price", 0) or 0),
                "stock": int(l.get("availableStock", 0) or 0),
                "status": "active" if l.get("isSalable") else "passive",
                "image": "", "url": "",
            })
        return self._tag(rows)

    def update_price_stock(self, items: list[dict]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"{len(items)} ürün güncellendi (DEMO — anahtar girilince gerçek mağazaya gider)."}
        payload = []
        for it in items:
            entry: dict = {"merchantSku": it.get("sku") or it.get("barcode")}
            if it.get("stock") is not None:
                entry["availableStock"] = int(it["stock"])
            if it.get("price") is not None:
                entry["price"] = float(it["price"])
            payload.append(entry)
        data = self._req(LISTING_BASE, "POST",
                         f"/listings/merchantid/{self.merchant_id}/inventory-uploads", json=payload)
        return {"ok": True, "mode": "live", "batch_id": str(data.get("id", "")),
                "message": f"{len(items)} ürün güncelleme kuyruğa alındı."}

    # ---- Siparişler (OMS) ----

    def list_orders(self, status: Optional[str] = None, days: int = 14) -> list[dict]:
        if not self.configured:
            rows = B.demo_orders("HB")
            return self._tag([r for r in rows if not status or r["status"] == status])
        data = self._req(OMS_BASE, "GET", f"/packages/merchantid/{self.merchant_id}",
                         params={"offset": 0, "limit": 100})
        # HB OMS yanıtı: {"packages": [...]} veya doğrudan liste olabilir
        raw = data.get("packages") or data.get("items") or (data if isinstance(data, list) else [])
        rows = []
        for pkg in raw:
            items = pkg.get("items") or pkg.get("lines") or []
            mapped = STATUS_MAP.get(pkg.get("status", ""), pkg.get("status", ""))
            # Müşteri adı: recipient.name veya customerName
            recipient = pkg.get("recipient") or {}
            customer = recipient.get("name", "") if isinstance(recipient, dict) else str(pkg.get("customerName", ""))
            # Toplam fiyat: {amount: ...} veya düz sayı
            total_raw = pkg.get("totalPrice", 0) or 0
            total = float(total_raw.get("amount", 0) if isinstance(total_raw, dict) else total_raw)
            rows.append({
                "order_id": str(pkg.get("orderNumber", "")),
                "package_id": str(pkg.get("packageNumber", pkg.get("id", ""))),
                "date": str(pkg.get("orderDate", pkg.get("createdDate", "")))[:16].replace("T", " "),
                "customer": customer,
                "city": (pkg.get("shippingAddress") or {}).get("city", ""),
                "status": mapped,
                "total": total,
                "cargo": {
                    "carrier": pkg.get("cargoCompany", ""),
                    "tracking": str(pkg.get("barcode", "") or pkg.get("trackingNumber", "")),
                    "url": pkg.get("trackingUrl", ""),
                },
                "items": [{"sku": i.get("merchantSku", ""), "title": i.get("productName", i.get("name", "")),
                           "qty": int(i.get("quantity", 1) or 1),
                           "price": float(i.get("price", {}).get("amount", 0) if isinstance(i.get("price"), dict) else i.get("price", 0) or 0)}
                          for i in items],
            })
        if status:
            rows = [r for r in rows if r["status"] == status]
        return self._tag(rows)

    # ---- İadeler ----

    def list_claims(self) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_claims("HB"))
        # OMS iade/talep servisi merchant bazında ayrıca açtırılır.
        raise B.NotSupported("Hepsiburada iade API'si hesabınızda aktif değilse entegrasyon ekibinden açtırmanız gerekir.")

    def review_claim(self, claim_id: str, approve: bool, reason: str = "") -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"İade {'onaylandı' if approve else 'reddedildi'} (DEMO)."}
        raise B.NotSupported("Hepsiburada iade onay/red için OMS iade servisi gerekir.")

    # ---- Sorular ----

    def list_questions(self, status: Optional[str] = None) -> list[dict]:
        if not self.configured:
            rows = B.demo_questions("HB")
            return self._tag([r for r in rows if not status or r["status"] == status])
        raise B.NotSupported("Hepsiburada soru-cevap API'si (MİM) ayrı başvuru gerektirir.")

    def answer_question(self, question_id: str, text: str) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "message": "Cevap gönderildi (DEMO)."}
        raise B.NotSupported("Hepsiburada soru-cevap API'si (MİM) ayrı başvuru gerektirir.")

    # ---- Finans ----

    def settlements(self, days: int = 30) -> list[dict]:
        if not self.configured:
            return B.demo_settlements()
        raise B.NotSupported("Hepsiburada finans raporları API üzerinden yayınlanmıyor — satıcı panelinden indirilebilir.")

    def buybox_info(self, sku_or_url: str) -> dict:
        if not self.configured:
            return B.demo_buybox(sku_or_url)
        raise B.NotSupported("Hepsiburada buybox verisi herkese açık API'de yok.")
