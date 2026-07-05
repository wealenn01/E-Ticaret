"""Amazon TR entegrasyon istemcisi (Selling Partner API).

Dokümantasyon: https://developer-docs.amazon.com/sp-api
Auth: Login with Amazon (LWA) refresh token -> access token akışı.
Bölge: Avrupa (eu-west-1), pazar: A33AVAJ2PDY3EV (amazon.com.tr)

Gerekli kimlikler: refresh_token, lwa_client_id, lwa_client_secret, seller_id.
Kapsanan uçlar:
  Siparişler  GET /orders/v0/orders
  Ürünler     GET /listings/2021-08-01/items/{sellerId} (SKU listesi FBA raporundan)
  Fiyat/Stok  PATCH /listings/2021-08-01/items/{sellerId}/{sku}
"""
import time
from typing import Any, Optional

import httpx

from . import base as B

SP_BASE = "https://sellingpartnerapi-eu.amazon.com"
LWA_URL = "https://api.amazon.com/auth/o2/token"
MARKETPLACE_TR = "A33AVAJ2PDY3EV"

STATUS_MAP = {
    "Pending": "Created", "Unshipped": "Picking", "PartiallyShipped": "Shipped",
    "Shipped": "Shipped", "Delivered": "Delivered", "Canceled": "Cancelled",
}


class AmazonAPI(B.MarketplaceClient):
    platform = "amazon"
    platform_name = "Amazon TR"

    def __init__(self, creds: Optional[dict] = None):
        super().__init__(creds)
        self.seller_id = str(self.creds.get("seller_id", "") or "")
        self.refresh_token = str(self.creds.get("refresh_token", "") or "")
        self.client_id = str(self.creds.get("lwa_client_id", "") or "")
        self.client_secret = str(self.creds.get("lwa_client_secret", "") or "")
        self._access_token: Optional[str] = None
        self._token_expiry = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.refresh_token and self.client_id and self.client_secret)

    def _token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        try:
            with httpx.Client(timeout=30) as c:
                r = c.post(LWA_URL, data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                })
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            raise B.MarketplaceError(f"Amazon LWA token alınamadı: {str(e)[:150]}") from e
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _req(self, method: str, path: str, *, params: Optional[dict] = None, json: Any = None) -> Any:
        try:
            with httpx.Client(timeout=30) as c:
                r = c.request(method, f"{SP_BASE}{path}",
                              headers={"x-amz-access-token": self._token(), "Content-Type": "application/json"},
                              params=params, json=json)
                r.raise_for_status()
                return r.json() if r.content else {}
        except httpx.HTTPStatusError as e:
            raise B.MarketplaceError(f"Amazon SP-API {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.HTTPError as e:
            raise B.MarketplaceError(f"Amazon bağlantı hatası: {str(e)[:150]}") from e

    # ---- Siparişler ----

    def list_orders(self, status: Optional[str] = None, days: int = 14) -> list[dict]:
        if not self.configured:
            rows = B.demo_orders("AMZ-")
            return self._tag([r for r in rows if not status or r["status"] == status])
        created_after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))
        data = self._req("GET", "/orders/v0/orders",
                         params={"MarketplaceIds": MARKETPLACE_TR, "CreatedAfter": created_after})
        rows = []
        for o in (data.get("payload", {}).get("Orders") or []):
            oid = o.get("AmazonOrderId", "")
            items_data = self._req("GET", f"/orders/v0/orders/{oid}/orderItems")
            items = [{"sku": i.get("SellerSKU", ""), "title": i.get("Title", ""),
                      "qty": int(i.get("QuantityOrdered", 1) or 1),
                      "price": float((i.get("ItemPrice") or {}).get("Amount", 0) or 0)}
                     for i in items_data.get("payload", {}).get("OrderItems", [])]
            rows.append({
                "order_id": oid,
                "package_id": oid,
                "date": str(o.get("PurchaseDate", ""))[:16].replace("T", " "),
                "customer": "(Amazon gizler)",
                "city": (o.get("ShippingAddress") or {}).get("City", ""),
                "status": STATUS_MAP.get(o.get("OrderStatus", ""), o.get("OrderStatus", "")),
                "total": float((o.get("OrderTotal") or {}).get("Amount", 0) or 0),
                "cargo": {"carrier": "", "tracking": "", "url": ""},
                "items": items,
            })
        if status:
            rows = [r for r in rows if r["status"] == status]
        return self._tag(rows)

    # ---- Ürünler / fiyat-stok ----

    def list_products(self, page: int = 0, size: int = 50) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_products())
        # SP-API'de toplu ürün listesi Reports API ile alınır (GET_MERCHANT_LISTINGS_ALL_DATA).
        raise B.NotSupported("Amazon ürün listesi Reports API raporu gerektirir — SKU'ları tek tek fiyat/stok güncellenebilir.")

    def update_price_stock(self, items: list[dict]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"{len(items)} ürün güncellendi (DEMO — anahtar girilince gerçek mağazaya gider)."}
        if not self.seller_id:
            raise B.MarketplaceError("Amazon fiyat/stok güncellemesi için seller_id gerekli.")
        done = 0
        for it in items:
            sku = it.get("sku")
            patches = []
            if it.get("price") is not None:
                patches.append({"op": "replace", "path": "/attributes/purchasable_offer",
                                "value": [{"marketplace_id": MARKETPLACE_TR,
                                           "our_price": [{"schedule": [{"value_with_tax": float(it["price"])}]}]}]})
            if it.get("stock") is not None:
                patches.append({"op": "replace", "path": "/attributes/fulfillment_availability",
                                "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": int(it["stock"])}]})
            if not patches:
                continue
            self._req("PATCH", f"/listings/2021-08-01/items/{self.seller_id}/{sku}",
                      params={"marketplaceIds": MARKETPLACE_TR},
                      json={"productType": "PRODUCT", "patches": patches})
            done += 1
        return {"ok": True, "mode": "live", "message": f"{done} SKU Amazon'a güncellendi."}

    # ---- Diğerleri ----

    def list_claims(self) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_claims("AMZ-"))
        raise B.NotSupported("Amazon iadeleri FBA/MFN raporlarıyla yönetilir (Reports API).")

    def review_claim(self, claim_id: str, approve: bool, reason: str = "") -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"İade {'onaylandı' if approve else 'reddedildi'} (DEMO)."}
        raise B.NotSupported("Amazon iade kararları Seller Central'dan verilir.")

    def list_questions(self, status: Optional[str] = None) -> list[dict]:
        if not self.configured:
            rows = B.demo_questions("AMZ-")
            return self._tag([r for r in rows if not status or r["status"] == status])
        raise B.NotSupported("Amazon müşteri soruları API'ye açık değil.")

    def answer_question(self, question_id: str, text: str) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "message": "Cevap gönderildi (DEMO)."}
        raise B.NotSupported("Amazon müşteri soruları API'ye açık değil.")

    def settlements(self, days: int = 30) -> list[dict]:
        if not self.configured:
            return B.demo_settlements()
        # Finances API mevcut: GET /finances/v0/financialEvents
        data = self._req("GET", "/finances/v0/financialEvents",
                         params={"PostedAfter": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))})
        events = data.get("payload", {}).get("FinancialEvents", {})
        gross = commission = refunds = 0.0
        for ev in events.get("ShipmentEventList", []) or []:
            for item in ev.get("ShipmentItemList", []) or []:
                for ch in item.get("ItemChargeList", []) or []:
                    gross += float((ch.get("ChargeAmount") or {}).get("CurrencyAmount", 0) or 0)
                for fee in item.get("ItemFeeList", []) or []:
                    commission += abs(float((fee.get("FeeAmount") or {}).get("CurrencyAmount", 0) or 0))
        for ev in events.get("RefundEventList", []) or []:
            for item in ev.get("ShipmentItemAdjustmentList", []) or []:
                for ch in item.get("ItemChargeAdjustmentList", []) or []:
                    refunds += abs(float((ch.get("ChargeAmount") or {}).get("CurrencyAmount", 0) or 0))
        return [{
            "period": f"Son {days} gün",
            "gross": round(gross, 2), "commission": round(commission, 2),
            "shipping": 0.0, "refunds": round(refunds, 2),
            "net": round(gross - commission - refunds, 2), "payout_date": "",
        }]

    def buybox_info(self, sku_or_url: str) -> dict:
        if not self.configured:
            return B.demo_buybox(sku_or_url)
        # Product Pricing API: GET /products/pricing/v0/items/{Asin}/offers
        data = self._req("GET", f"/products/pricing/v0/items/{sku_or_url}/offers",
                         params={"MarketplaceId": MARKETPLACE_TR, "ItemCondition": "New"})
        offers = data.get("payload", {}).get("Offers", [])
        sellers = [{"seller": o.get("SellerId", "?"),
                    "price": float((o.get("ListingPrice") or {}).get("Amount", 0) or 0),
                    "rating": float((o.get("SellerFeedbackRating") or {}).get("SellerPositiveFeedbackRating", 0) or 0) / 10}
                   for o in offers]
        our = next((s for s in sellers if s["seller"] == self.seller_id), None)
        bb = next((o for o in offers if o.get("IsBuyBoxWinner")), None)
        bb_price = float((bb.get("ListingPrice") or {}).get("Amount", 0) or 0) if bb else 0
        return {
            "sku": sku_or_url, "product": sku_or_url,
            "our_price": our["price"] if our else 0,
            "buybox_price": bb_price,
            "buybox_seller": "Siz" if bb and bb.get("SellerId") == self.seller_id else (bb or {}).get("SellerId", "—"),
            "we_have_buybox": bool(bb and bb.get("SellerId") == self.seller_id),
            "sellers": sellers,
        }
