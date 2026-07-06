"""N11 entegrasyon istemcisi (resmi REST API).

Dokümantasyon: https://magazadestek.n11.com (Satış Süreçleri > RestAPI)
Auth: appKey + appSecret header'ları (no-auth; kimlik header'da taşınır).

Kapsanan resmi REST uçları (Temmuz 2026):
  Ürünler     GET  https://api.n11.com/ms/product-query               (GetProductQuery)
  Fiyat/Stok  POST https://api.n11.com/ms/product/tasks/price-stock-update  (UpdateProductPriceAndStock)
  Siparişler  GET  https://api.n11.com/rest/delivery/v1/shipmentPackages    (GetShipmentPackages)

Desteklenmeyen (N11 REST API'de herkese açık uç yok — Satıcı Ofisi/SOAP'tan yönetilir):
  iade (ClaimService), müşteri soru-cevap, finans/hakediş raporu, buybox.
"""
from typing import Any, Optional

import httpx

from . import base as B

BASE = "https://api.n11.com"
INTEGRATOR = "SelfIntegration"

# GetShipmentPackages statüleri: Created, Picking, Shipped, Cancelled,
# Delivered, Unpacked, UnSupplied.
STATUS_MAP = {
    "Created": "Created", "Picking": "Picking", "Shipped": "Shipped",
    "Delivered": "Delivered", "Cancelled": "Cancelled", "Returned": "Returned",
    "Unpacked": "Created", "UnSupplied": "Cancelled", "Completed": "Delivered",
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
        # GetProductQuery — GET /ms/product-query (size max 250)
        data = self._req("GET", "/ms/product-query", params={"page": page, "size": min(size, 250)})
        rows = []
        for p in (data.get("content") or data.get("products") or []):
            # Fiyat/stok varyant (stockItems/skus) altında olabilir; ürün seviyesine de düşülür.
            variant = (p.get("stockItems") or p.get("skus") or [{}])[0] if isinstance(p.get("stockItems") or p.get("skus"), list) else {}
            sale = p.get("salePrice", variant.get("salePrice", 0))
            listp = p.get("listPrice", variant.get("listPrice", 0))
            qty = p.get("quantity", variant.get("quantity", p.get("stockCount", 0)))
            images = p.get("images") or p.get("imageUrls") or []
            image = ""
            if images:
                first = images[0]
                image = first.get("url", "") if isinstance(first, dict) else str(first)
            rows.append({
                "sku": p.get("stockCode", "") or variant.get("stockCode", ""),
                "barcode": p.get("barcode", "") or variant.get("barcode", ""),
                "title": p.get("title", ""),
                "price": float(sale or 0),
                "list_price": float(listp or 0),
                "stock": int(qty or 0),
                "status": "active" if str(p.get("saleStatus", p.get("status", ""))).lower() in ("on_sale", "active", "1", "true") else "passive",
                "image": image,
                "url": p.get("productUrl", ""),
            })
        return self._tag(rows)

    def update_price_stock(self, items: list[dict]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"{len(items)} ürün güncellendi (DEMO — anahtar girilince gerçek mağazaya gider)."}
        # UpdateProductPriceAndStock — POST /ms/product/tasks/price-stock-update
        # Kural: fiyat güncellenecekse listPrice + salePrice BİRLİKTE gönderilmeli,
        # listPrice >= salePrice olmalı; max 1000 sku.
        skus: list[dict] = []
        for it in items:
            entry: dict = {"stockCode": it.get("sku") or it.get("barcode")}
            if it.get("stock") is not None:
                entry["quantity"] = int(it["stock"])
            if it.get("price") is not None:
                sale = round(float(it["price"]), 2)
                listp = round(float(it.get("list_price") or it["price"]), 2)
                entry["salePrice"] = sale
                entry["listPrice"] = max(listp, sale)
                entry["currencyType"] = "TL"
            skus.append(entry)
        data = self._req("POST", "/ms/product/tasks/price-stock-update",
                         json={"payload": {"integrator": INTEGRATOR, "skus": skus}})
        return {"ok": True, "mode": "live", "task_id": str(data.get("id", "")),
                "message": f"{len(items)} ürün güncelleme N11 kuyruğuna alındı (task {data.get('id', '')})."}

    # ---- Siparişler ----

    def list_orders(self, status: Optional[str] = None, days: int = 14) -> list[dict]:
        if not self.configured:
            rows = B.demo_orders("N11-")
            return self._tag([r for r in rows if not status or r["status"] == status])
        # GetShipmentPackages — GET /rest/delivery/v1/shipmentPackages
        # startDate/endDate: milisaniye timestamp, GMT+3. size max 100.
        import time as _t
        end = int(_t.time() * 1000)
        params = {"startDate": end - days * 86400_000, "endDate": end,
                  "page": 0, "size": 100, "orderByField": "true", "orderByDirection": "DESC"}
        data = self._req("GET", "/rest/delivery/v1/shipmentPackages", params=params)
        rows = []
        for o in (data.get("content") or data.get("shipmentPackages") or []):
            lines = o.get("lines") or o.get("orderItems") or []
            ship = o.get("shippingAddress") or o.get("shipmentAddress") or {}
            rows.append({
                "order_id": str(o.get("orderNumber", o.get("id", ""))),
                "package_id": str(o.get("id", o.get("packageId", ""))),
                "date": str(o.get("createDate", o.get("orderDate", "")))[:16].replace("T", " "),
                "customer": str(o.get("customerFullName", o.get("fullName", o.get("buyerName", "")))),
                "city": str(ship.get("city", "")),
                "status": STATUS_MAP.get(str(o.get("status", "")), str(o.get("status", ""))),
                "total": float(o.get("totalAmount", o.get("totalPrice", 0)) or 0),
                "cargo": {
                    "carrier": str(o.get("cargoProviderName", o.get("shipmentCompany", ""))),
                    "tracking": str(o.get("cargoTrackingNumber", "") or ""),
                    "url": str(o.get("cargoTrackingLink", "") or ""),
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

    def review_claim(self, claim_id: str, approve: bool, reason: str = "",
                     claim_item_ids: Optional[list[str]] = None) -> dict:
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
