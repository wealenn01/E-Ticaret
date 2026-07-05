"""Trendyol Marketplace tam entegrasyon istemcisi (Satıcı API v2).

Dokümantasyon: https://developers.trendyol.com
Auth: Basic (apiKey:apiSecret), User-Agent: "{sellerId} - SelfIntegration"

Kapsanan uçlar:
  Ürünler        GET  /product/sellers/{sid}/products
  Fiyat/Stok     POST /inventory/sellers/{sid}/products/price-and-inventory
  Siparişler     GET  /order/sellers/{sid}/orders
  İadeler        GET  /order/sellers/{sid}/claims
  İade onay      PUT  /order/sellers/{sid}/claims/{claimId}/items/approve
  İade red       POST /order/sellers/{sid}/claims/{claimId}/issue
  Sorular        GET  /qna/sellers/{sid}/questions/filter
  Soru cevabı    POST /qna/sellers/{sid}/questions/{qid}/answers
  Hakediş        GET  /finance/che/sellers/{sid}/settlements
  Webhook        GET/POST/DELETE /webhook/sellers/{sid}/webhooks
"""
import base64
import time
from typing import Any, Optional

import httpx

from . import base as B

PROD_BASE = "https://apigw.trendyol.com/integration"
STAGE_BASE = "https://stageapigw.trendyol.com/integration"

STATUS_MAP = {
    "Created": "Created", "Picking": "Picking", "Invoiced": "Picking",
    "Shipped": "Shipped", "AtCollectionPoint": "Shipped", "Delivered": "Delivered",
    "Cancelled": "Cancelled", "UnDelivered": "UnDelivered", "Returned": "Returned",
}


class TrendyolAPI(B.MarketplaceClient):
    platform = "trendyol"
    platform_name = "Trendyol"

    def __init__(self, creds: Optional[dict] = None):
        super().__init__(creds)
        self.seller_id = str(self.creds.get("seller_id", "") or "")
        self.api_key = str(self.creds.get("api_key", "") or "")
        self.api_secret = str(self.creds.get("api_secret", "") or "")
        env = str(self.creds.get("env") or "prod")
        self.base = STAGE_BASE if env == "stage" else PROD_BASE

    @property
    def configured(self) -> bool:
        return bool(self.seller_id and self.api_key and self.api_secret)

    def _headers(self) -> dict:
        token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "User-Agent": f"{self.seller_id} - SelfIntegration",
            "Content-Type": "application/json",
        }

    def _req(self, method: str, path: str, *, params: Optional[dict] = None, json: Any = None) -> Any:
        try:
            with httpx.Client(timeout=30) as c:
                r = c.request(method, f"{self.base}{path}", headers=self._headers(), params=params, json=json)
                r.raise_for_status()
                return r.json() if r.content else {}
        except httpx.HTTPStatusError as e:
            raise B.MarketplaceError(
                f"Trendyol API {e.response.status_code}: {e.response.text[:200]}"
            ) from e
        except httpx.HTTPError as e:
            raise B.MarketplaceError(f"Trendyol bağlantı hatası: {str(e)[:150]}") from e

    # ---- Ürünler ----

    def list_products(self, page: int = 0, size: int = 50) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_products())
        data = self._req("GET", f"/product/sellers/{self.seller_id}/products",
                         params={"page": page, "size": size, "approved": "true"})
        rows = []
        for p in data.get("content", []):
            rows.append({
                "sku": p.get("stockCode") or p.get("productMainId", ""),
                "barcode": p.get("barcode", ""),
                "title": p.get("title", ""),
                "price": p.get("salePrice", 0),
                "list_price": p.get("listPrice", 0),
                "stock": p.get("quantity", 0),
                "status": "active" if p.get("onSale") else "passive",
                "image": (p.get("images") or [{}])[0].get("url", ""),
                "url": p.get("productUrl", ""),
            })
        return self._tag(rows)

    def update_price_stock(self, items: list[dict]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "message": f"{len(items)} ürün güncellendi (DEMO — anahtar girilince gerçek mağazaya gider)."}
        payload = {"items": []}
        for it in items:
            entry: dict = {"barcode": it.get("barcode") or it.get("sku")}
            if it.get("stock") is not None:
                entry["quantity"] = int(it["stock"])
            if it.get("price") is not None:
                entry["salePrice"] = float(it["price"])
                entry["listPrice"] = float(it.get("list_price") or it["price"])
            payload["items"].append(entry)
        data = self._req("POST", f"/inventory/sellers/{self.seller_id}/products/price-and-inventory", json=payload)
        return {"ok": True, "mode": "live", "batch_id": data.get("batchRequestId", ""),
                "message": f"{len(items)} ürün güncelleme kuyruğa alındı."}

    # ---- Siparişler ----

    def list_orders(self, status: Optional[str] = None, days: int = 14) -> list[dict]:
        if not self.configured:
            rows = B.demo_orders("TY")
            return self._tag([r for r in rows if not status or r["status"] == status])
        end = int(time.time() * 1000)
        start = end - days * 86400_000
        # status parametresi Trendyol'a gönderilmiyor; bizim normalize
        # ettiğimiz değer (ör. "Picking") Trendyol'daki birden fazla değere
        # ("Picking" + "Invoiced") karşılık geldiği için yerel filtreliyoruz.
        params = {"startDate": start, "endDate": end, "size": 200, "orderByDirection": "DESC"}
        data = self._req("GET", f"/order/sellers/{self.seller_id}/orders", params=params)
        rows = []
        for o in data.get("content", []):
            lines = o.get("lines", [])
            rows.append({
                "order_id": str(o.get("orderNumber", "")),
                "package_id": str(o.get("id", "")),
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime((o.get("orderDate") or 0) / 1000)),
                "customer": f"{o.get('customerFirstName', '')} {o.get('customerLastName', '')}".strip(),
                "city": (o.get("shipmentAddress") or {}).get("city", ""),
                "status": STATUS_MAP.get(o.get("shipmentPackageStatus", o.get("status", "")), o.get("status", "")),
                "total": o.get("totalPrice", 0),
                "cargo": {
                    "carrier": o.get("cargoProviderName", ""),
                    "tracking": str(o.get("cargoTrackingNumber") or ""),
                    "url": o.get("cargoTrackingLink", ""),
                },
                "items": [{"sku": l.get("merchantSku", ""), "title": l.get("productName", ""),
                           "qty": l.get("quantity", 1), "price": l.get("price", 0)} for l in lines],
            })
        if status:
            rows = [r for r in rows if r["status"] == status]
        return self._tag(rows)

    # ---- İadeler / tazmin ----

    def list_claims(self) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_claims("TY"))
        end = int(time.time() * 1000)
        data = self._req("GET", f"/order/sellers/{self.seller_id}/claims",
                         params={"startDate": end - 30 * 86400_000, "endDate": end, "size": 100})
        rows = []
        for cl in data.get("content", []):
            items = cl.get("items", [])
            first = (items[0] if items else {}).get("orderLine", {})
            reason = ((items[0] if items else {}).get("claimItems") or [{}])[0]
            rows.append({
                "claim_id": str(cl.get("id", "")),
                "order_id": str(cl.get("orderNumber", "")),
                "product": first.get("productName", ""),
                "sku": first.get("merchantSku", ""),
                "reason": (reason.get("claimItemReason") or {}).get("name", ""),
                "type": "return",
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime((cl.get("claimDate") or 0) / 1000)),
                "status": (cl.get("status") or {}).get("name", "") if isinstance(cl.get("status"), dict) else str(cl.get("status", "")),
            })
        return self._tag(rows)

    def review_claim(self, claim_id: str, approve: bool, reason: str = "") -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"İade {'onaylandı' if approve else 'reddedildi'} (DEMO)."}
        if approve:
            self._req("PUT", f"/order/sellers/{self.seller_id}/claims/{claim_id}/items/approve",
                      json={"claimLineItemIdList": [], "params": {}})
            return {"ok": True, "mode": "live", "message": "İade onaylandı."}
        # Trendyol docs: claimIssueReasonId request body'de gönderilmeli, query param değil
        self._req("POST", f"/order/sellers/{self.seller_id}/claims/{claim_id}/issue",
                  json={"claimIssueReasonId": 1, "description": reason or "İtiraz"})
        return {"ok": True, "mode": "live", "message": "İade itirazı (red) oluşturuldu."}

    # ---- Müşteri soruları ----

    def list_questions(self, status: Optional[str] = None) -> list[dict]:
        if not self.configured:
            rows = B.demo_questions("TY")
            return self._tag([r for r in rows if not status or r["status"] == status])
        params = {"size": 100, "orderByDirection": "DESC"}
        if status:
            params["status"] = status
        data = self._req("GET", f"/qna/sellers/{self.seller_id}/questions/filter", params=params)
        rows = []
        for q in data.get("content", []):
            rows.append({
                "question_id": str(q.get("id", "")),
                "product": q.get("productName", ""),
                "sku": str(q.get("productMainId", "")),
                "question": q.get("text", ""),
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime((q.get("creationDate") or 0) / 1000)),
                "status": q.get("status", ""),
                "answer": (q.get("answer") or {}).get("text", ""),
            })
        return self._tag(rows)

    def answer_question(self, question_id: str, text: str) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "message": "Cevap gönderildi (DEMO)."}
        self._req("POST", f"/qna/sellers/{self.seller_id}/questions/{question_id}/answers",
                  json={"text": text})
        return {"ok": True, "mode": "live", "message": "Cevap Trendyol'a gönderildi — moderasyon sonrası yayınlanır."}

    # ---- Finans ----

    def settlements(self, days: int = 30) -> list[dict]:
        if not self.configured:
            return B.demo_settlements()
        end = int(time.time() * 1000)
        rows_by_week: dict[str, dict] = {}
        for ttype in ("Sale", "Commission", "Return"):
            try:
                data = self._req("GET", f"/finance/che/sellers/{self.seller_id}/settlements",
                                 params={"transactionType": ttype,
                                         "startDate": end - days * 86400_000, "endDate": end, "size": 500})
            except B.MarketplaceError:
                continue
            for t in data.get("content", []):
                week = time.strftime("%Y hafta %W", time.localtime((t.get("transactionDate") or 0) / 1000))
                w = rows_by_week.setdefault(week, {"period": week, "gross": 0.0, "commission": 0.0,
                                                   "shipping": 0.0, "refunds": 0.0, "net": 0.0, "payout_date": ""})
                amt = float(t.get("debt", 0) or 0) - float(t.get("credit", 0) or 0)
                if ttype == "Sale":
                    w["gross"] += float(t.get("credit", 0) or 0)
                elif ttype == "Commission":
                    w["commission"] += abs(amt)
                else:
                    w["refunds"] += abs(amt)
        out = []
        for w in rows_by_week.values():
            w["net"] = round(w["gross"] - w["commission"] - w["refunds"], 2)
            for k in ("gross", "commission", "refunds"):
                w[k] = round(w[k], 2)
            out.append(w)
        return sorted(out, key=lambda x: x["period"], reverse=True)

    # ---- Buybox ----

    def buybox_info(self, sku_or_url: str) -> dict:
        if not self.configured:
            return B.demo_buybox(sku_or_url)
        # Trendyol satıcı API'sinde herkese açık buybox ucu yok; ürünün kendi
        # fiyatı çekilir, rakip satıcılar için ürün sayfası linki gösterilir.
        products = self.list_products(size=200)
        p = next((x for x in products if x["sku"] == sku_or_url or x["barcode"] == sku_or_url), None)
        if not p:
            raise B.MarketplaceError(f"'{sku_or_url}' mağaza ürünlerinde bulunamadı.")
        return {"sku": p["sku"], "product": p["title"], "our_price": p["price"],
                "buybox_price": p["price"], "buybox_seller": "—",
                "we_have_buybox": True, "sellers": [],
                "note": "Rakip satıcı fiyatları için ürün sayfasını takip listesine URL ile ekleyin."}

    # ---- Webhook ----

    def register_webhook(self, url: str, events: list[str]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "id": "demo-webhook-1",
                    "message": "Webhook kaydedildi (DEMO)."}
        data = self._req("POST", f"/webhook/sellers/{self.seller_id}/webhooks",
                         json={"url": url, "username": "", "password": "",
                               "authenticationType": "NONE", "subscribedStatuses": events or ["CREATED"]})
        return {"ok": True, "mode": "live", "id": str(data.get("id", "")), "message": "Webhook Trendyol'a kaydedildi."}

    def list_webhooks(self) -> list[dict]:
        if not self.configured:
            return []
        data = self._req("GET", f"/webhook/sellers/{self.seller_id}/webhooks")
        items = data if isinstance(data, list) else data.get("content", [])
        return [{"id": str(w.get("id", "")), "url": w.get("url", ""),
                 "events": w.get("subscribedStatuses", [])} for w in items]

    def delete_webhook(self, webhook_id: str) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo"}
        self._req("DELETE", f"/webhook/sellers/{self.seller_id}/webhooks/{webhook_id}")
        return {"ok": True, "mode": "live"}
