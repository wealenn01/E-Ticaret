"""N11 entegrasyon istemcisi (yeni REST API).

Dokümantasyon: https://api.n11.com/ms (N11 Satıcı Ofisi > Entegrasyon)
Auth: appkey + appsecret header'ları.

Kapsanan uçlar:
  Ürünler     GET  /ms/product-query
  Fiyat/Stok  POST /ms/product/updateStockByStockSellerCode benzeri toplu uç
  Siparişler  GET  /ms/orders
Soru-cevap ve iade uçları REST API'de sınırlıdır; anahtar yoksa DEMO çalışır.
"""
from typing import Any, Optional

import httpx

from . import base as B

BASE = "https://api.n11.com/ms"

STATUS_MAP = {
    "Created": "Created", "Approved": "Picking", "Shipped": "Shipped",
    "Delivered": "Delivered", "Rejected": "Cancelled", "Returned": "Returned",
    "Completed": "Delivered", "Cancelled": "Cancelled",
}


class N11API(B.MarketplaceClient):
    platform = "n11"
    platform_name = "N11"

    def __init__(self, creds: Optional[dict] = None):
        super().__init__(creds)
        self.app_key = str(self.creds.get("api_key", "") or "")
        self.app_secret = str(self.creds.get("api_secret", "") or "")

    @property
    def configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def _headers(self) -> dict:
        return {"appkey": self.app_key, "appsecret": self.app_secret, "Content-Type": "application/json"}

    def _req(self, method: str, path: str, *, params: Optional[dict] = None, json: Any = None) -> Any:
        try:
            with httpx.Client(timeout=30) as c:
                r = c.request(method, f"{BASE}{path}", headers=self._headers(), params=params, json=json)
                r.raise_for_status()
                return r.json() if r.content else {}
        except httpx.HTTPStatusError as e:
            raise B.MarketplaceError(f"N11 API {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise B.MarketplaceError(f"N11 bağlantı hatası: {str(e)[:150]}") from e

    # ---- Ürünler ----

    def list_products(self, page: int = 0, size: int = 50) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_products())
        data = self._req("GET", "/product-query", params={"page": page, "size": size})
        rows = []
        for p in (data.get("content") or data.get("products") or []):
            rows.append({
                "sku": p.get("stockCode", "") or p.get("sellerStockCode", ""),
                "barcode": p.get("barcode", ""),
                "title": p.get("title", ""),
                "price": float(p.get("salePrice", p.get("displayPrice", 0)) or 0),
                "list_price": float(p.get("listPrice", p.get("price", 0)) or 0),
                "stock": int(p.get("quantity", p.get("stockCount", 0)) or 0),
                "status": "active" if str(p.get("status", "")).lower() in ("active", "1", "true") else "passive",
                "image": (p.get("imageUrls") or [""])[0] if isinstance(p.get("imageUrls"), list) else "",
                "url": p.get("productUrl", ""),
            })
        return self._tag(rows)

    def update_price_stock(self, items: list[dict]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"{len(items)} ürün güncellendi (DEMO — anahtar girilince gerçek mağazaya gider)."}
        payload = {"payload": {"integrator": "SelfIntegration", "skus": []}}
        for it in items:
            entry: dict = {"stockCode": it.get("sku") or it.get("barcode")}
            if it.get("stock") is not None:
                entry["quantity"] = int(it["stock"])
            if it.get("price") is not None:
                entry["salePrice"] = float(it["price"])
                entry["listPrice"] = float(it.get("list_price") or it["price"])
            payload["payload"]["skus"].append(entry)
        self._req("POST", "/product/price-stock-update", json=payload)
        return {"ok": True, "mode": "live", "message": f"{len(items)} ürün güncelleme N11 kuyruğuna alındı."}

    # ---- Siparişler ----

    def list_orders(self, status: Optional[str] = None, days: int = 14) -> list[dict]:
        if not self.configured:
            rows = B.demo_orders("N11-")
            return self._tag([r for r in rows if not status or r["status"] == status])
        data = self._req("GET", "/orders", params={"page": 0, "size": 100})
        rows = []
        for o in (data.get("content") or []):
            lines = o.get("lines") or o.get("orderItems") or []
            rows.append({
                "order_id": str(o.get("orderNumber", o.get("id", ""))),
                "package_id": str(o.get("id", "")),
                "date": str(o.get("createDate", ""))[:16].replace("T", " "),
                "customer": str(o.get("customerFullName", o.get("buyerName", ""))),
                "city": str((o.get("shipmentAddress") or {}).get("city", "")),
                "status": STATUS_MAP.get(str(o.get("status", "")), str(o.get("status", ""))),
                "total": float(o.get("totalAmount", 0) or 0),
                "cargo": {
                    "carrier": str(o.get("cargoProviderName", o.get("shipmentCompany", ""))),
                    "tracking": str(o.get("cargoTrackingNumber", "") or ""),
                    "url": "",
                },
                "items": [{"sku": l.get("stockCode", l.get("sellerStockCode", "")),
                           "title": l.get("productName", l.get("title", "")),
                           "qty": int(l.get("quantity", 1) or 1),
                           "price": float(l.get("price", l.get("sellerInvoiceAmount", 0)) or 0)} for l in lines],
            })
        if status:
            rows = [r for r in rows if r["status"] == status]
        return self._tag(rows)

    # ---- İadeler / sorular / finans — DEMO veya NotSupported ----

    def list_claims(self) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_claims("N11-"))
        raise B.NotSupported("N11 iade yönetimi REST API'de sınırlı — Satıcı Ofisi'nden yönetilir.")

    def review_claim(self, claim_id: str, approve: bool, reason: str = "") -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"İade {'onaylandı' if approve else 'reddedildi'} (DEMO)."}
        raise B.NotSupported("N11 iade onay/red REST API'de yayınlanmadı.")

    def list_questions(self, status: Optional[str] = None) -> list[dict]:
        if not self.configured:
            rows = B.demo_questions("N11-")
            return self._tag([r for r in rows if not status or r["status"] == status])
        raise B.NotSupported("N11 soru-cevap API'si Satıcı Ofisi'nden yönetilir.")

    def answer_question(self, question_id: str, text: str) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "message": "Cevap gönderildi (DEMO)."}
        raise B.NotSupported("N11 soru-cevap API'si Satıcı Ofisi'nden yönetilir.")

    def settlements(self, days: int = 30) -> list[dict]:
        if not self.configured:
            return B.demo_settlements()
        raise B.NotSupported("N11 finans raporları API üzerinden yayınlanmıyor.")

    def buybox_info(self, sku_or_url: str) -> dict:
        if not self.configured:
            return B.demo_buybox(sku_or_url)
        raise B.NotSupported("N11 buybox verisi herkese açık API'de yok.")
