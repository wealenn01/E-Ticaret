"""Trendyol Marketplace tam entegrasyon istemcisi.

Tüm uçlar https://developers.trendyol.com resmi OpenAPI tanımlarına birebir
uygundur (Temmuz 2026). Base URL: https://apigw.trendyol.com/integration
Auth: Basic (apiKey:apiSecret). Header'da User-Agent + x-agentname zorunludur;
User-Agent gönderilmeyen istekler 403 alır.

Kapsanan resmi uçlar:
  Ürün listeleme   GET  /product/sellers/{sid}/products              (filterProducts)
  Fiyat/Stok       POST /inventory/sellers/{sid}/products/price-and-inventory
  Buybox           POST /product/sellers/{sid}/products/buybox-information
  Siparişler       GET  /order/sellers/{sid}/orders                  (getShipmentPackages)
  İadeler          GET  /order/sellers/{sid}/claims                  (getClaims)
  İade onay        PUT  /order/sellers/{sid}/claims/{claimId}/items/approve
  İade red         POST /order/sellers/{sid}/claims/{claimId}/issue  (multipart/form-data)
  Red sebepleri    GET  /order/claim-issue-reasons                   (getClaimIssueReasons)
  Sorular          GET  /qna/sellers/{sid}/questions/filter
  Soru cevabı      POST /qna/sellers/{sid}/questions/{id}/answers
  Hakediş          GET  /finance/che/sellers/{sid}/settlements       (transactionType zorunlu, max 15 gün)
  Webhook          POST/GET/DELETE /webhook/sellers/{sid}/webhooks
"""
import base64
import time
import uuid
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
        # Zorunlu HTTP header'ları: developers.trendyol.com/docs/zorunlu-http-header-alanları
        # User-Agent gönderilmezse 403 alınır; x-agentname/x-correlationid log takibi içindir.
        return {
            "Authorization": f"Basic {token}",
            "User-Agent": f"{self.seller_id} - SelfIntegration",
            "x-agentname": f"{self.seller_id} - SelfIntegration",
            "x-correlationid": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _req(self, method: str, path: str, *, params: Optional[dict] = None, json: Any = None,
             data: Any = None, files: Any = None) -> Any:
        try:
            headers = self._headers()
            if data is not None or files is not None:
                # multipart/form-data: Content-Type'ı httpx belirlesin
                headers.pop("Content-Type", None)
            with httpx.Client(timeout=30) as c:
                r = c.request(method, f"{self.base}{path}", headers=headers,
                              params=params, json=json, data=data, files=files)
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
        # filterProducts (V1). NOT: Product V1 servisleri 10 Ağustos 2026'da geçersiz olacak;
        # o tarihe kadar /product/sellers/{sid}/products/approved (V2, content+variants) yapısına
        # geçilmelidir. V1 düz alan (quantity/salePrice/listPrice) döndürdüğü için şu an bu kullanılır.
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

    def _fetch_claims(self, days: int = 30) -> list[dict]:
        """getClaims ham içeriğini döner (claim onay/red için claimItems id'leri gerekir)."""
        end = int(time.time() * 1000)
        data = self._req("GET", f"/order/sellers/{self.seller_id}/claims",
                         params={"startDate": end - days * 86400_000, "endDate": end, "size": 200})
        return data.get("content", [])

    def _claim_item_ids(self, claim_id: str) -> list[str]:
        """Bir iadenin onay/red için gereken claimItems[].id listesini bulur."""
        for cl in self._fetch_claims():
            if str(cl.get("id") or cl.get("claimId")) == str(claim_id):
                return [str(ci.get("id")) for it in cl.get("items", [])
                        for ci in (it.get("claimItems") or []) if ci.get("id")]
        return []

    def list_claims(self) -> list[dict]:
        if not self.configured:
            return self._tag(B.demo_claims("TY"))
        rows = []
        for cl in self._fetch_claims():
            items = cl.get("items", [])
            first_line = (items[0] if items else {}).get("orderLine", {})
            first_ci = ((items[0] if items else {}).get("claimItems") or [{}])[0]
            # Sebep: customerClaimItemReason.name (yoksa trendyolClaimItemReason.name)
            reason = ((first_ci.get("customerClaimItemReason") or {}).get("name")
                      or (first_ci.get("trendyolClaimItemReason") or {}).get("name") or "")
            # Statü: claimItems[].claimItemStatus.name (Created/WaitingInAction/Accepted/Rejected...)
            status = (first_ci.get("claimItemStatus") or {}).get("name", "")
            claim_item_ids = [str(ci.get("id")) for it in items
                              for ci in (it.get("claimItems") or []) if ci.get("id")]
            rows.append({
                "claim_id": str(cl.get("id") or cl.get("claimId") or ""),
                "order_id": str(cl.get("orderNumber", "")),
                "product": first_line.get("productName", ""),
                "sku": first_line.get("merchantSku", ""),
                "reason": reason,
                "type": "return",
                "date": time.strftime("%Y-%m-%d %H:%M", time.localtime((cl.get("claimDate") or 0) / 1000)),
                "status": status,
                "claim_item_ids": claim_item_ids,
            })
        return self._tag(rows)

    def review_claim(self, claim_id: str, approve: bool, reason: str = "") -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo",
                    "message": f"İade {'onaylandı' if approve else 'reddedildi'} (DEMO)."}
        item_ids = self._claim_item_ids(claim_id)
        if not item_ids:
            raise B.MarketplaceError(
                "İade kalemi bulunamadı; iade son 30 günde açılmış ve WaitingInAction statüsünde olmalı.")
        if approve:
            # approveClaimLineItems: claimLineItemIdList zorunludur
            self._req("PUT", f"/order/sellers/{self.seller_id}/claims/{claim_id}/items/approve",
                      json={"claimLineItemIdList": item_ids, "params": {}})
            return {"ok": True, "mode": "live", "message": f"{len(item_ids)} iade kalemi onaylandı."}
        # createClaimIssue: multipart/form-data — claimIssueReasonId + claimItemIdList + description
        # claimIssueReasonId=451 => "Diğer" (getClaimIssueReasons resmi listesinden).
        self._req("POST", f"/order/sellers/{self.seller_id}/claims/{claim_id}/issue",
                  data={"claimIssueReasonId": 451,
                        "claimItemIdList": ",".join(item_ids),
                        "description": (reason or "İtiraz")[:500]})
        return {"ok": True, "mode": "live", "message": "İade itirazı (red talebi) oluşturuldu."}

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
        # getSettlements resmi kuralları: transactionType ZORUNLU (tek istek = tek tip),
        # startDate/endDate ZORUNLU ve aralık en fazla 15 GÜN. Bu yüzden tarih aralığını
        # 15 günlük pencerelere böler, her tip için ayrı ayrı sorgularız.
        MS_DAY = 86400_000
        WINDOW = 15 * MS_DAY
        now = int(time.time() * 1000)
        window_start = now - days * MS_DAY
        # transactionType enum'undan finans özetine karşılık gelen tipler:
        #   Sale -> ciro (credit), CommissionNegative -> komisyon, Return -> iade,
        #   DeliveryFee -> kargo. (Resmi enum değerleri; "Commission" tek başına geçersizdir.)
        type_bucket = {"Sale": "gross", "CommissionNegative": "commission",
                       "Return": "refunds", "DeliveryFee": "shipping"}
        rows_by_week: dict[str, dict] = {}
        for ttype, bucket in type_bucket.items():
            start = window_start
            while start < now:
                end = min(start + WINDOW, now)
                try:
                    data = self._req("GET", f"/finance/che/sellers/{self.seller_id}/settlements",
                                     params={"transactionType": ttype, "startDate": start,
                                             "endDate": end, "size": 500})
                except B.MarketplaceError:
                    start = end
                    continue
                for t in data.get("content", []):
                    week = time.strftime("%Y hafta %W", time.localtime((t.get("transactionDate") or 0) / 1000))
                    w = rows_by_week.setdefault(week, {"period": week, "gross": 0.0, "commission": 0.0,
                                                       "shipping": 0.0, "refunds": 0.0, "net": 0.0, "payout_date": ""})
                    # credit = alacak (satıcı lehine), debt = borç (satıcı aleyhine)
                    amt = abs(float(t.get("credit", 0) or 0) - float(t.get("debt", 0) or 0))
                    w[bucket] += amt
                start = end
        out = []
        for w in rows_by_week.values():
            w["net"] = round(w["gross"] - w["commission"] - w["shipping"] - w["refunds"], 2)
            for k in ("gross", "commission", "shipping", "refunds"):
                w[k] = round(w[k], 2)
            out.append(w)
        return sorted(out, key=lambda x: x["period"], reverse=True)

    # ---- Buybox ----

    def buybox_info(self, sku_or_url: str) -> dict:
        if not self.configured:
            return B.demo_buybox(sku_or_url)
        # Resmi buybox ucu barkod ile çalışır (getBuyboxInformation, max 10 barkod).
        # Girdi SKU ise önce ürünlerden barkoda çevrilir.
        products = self.list_products(size=200)
        p = next((x for x in products if x["sku"] == sku_or_url or x["barcode"] == sku_or_url), None)
        barcode = (p or {}).get("barcode") or sku_or_url
        our_price = (p or {}).get("price", 0)
        data = self._req("POST", f"/product/sellers/{self.seller_id}/products/buybox-information",
                         json={"barcodes": [barcode]})
        info = next((b for b in data.get("buyboxInfo", []) if b.get("barcode") == barcode),
                    (data.get("buyboxInfo") or [{}])[0])
        bb_price = float(info.get("buyboxPrice", 0) or 0)
        bb_order = info.get("buyboxOrder")
        # buyboxOrder == 1 => buybox bizde. hasMultipleSeller => rakip var mı.
        we_have = bb_order == 1
        return {
            "sku": (p or {}).get("sku", sku_or_url),
            "product": (p or {}).get("title", sku_or_url),
            "our_price": our_price,
            "buybox_price": bb_price or our_price,
            "buybox_seller": "Siz" if we_have else "Rakip satıcı",
            "we_have_buybox": we_have,
            "buybox_order": bb_order,
            "has_multiple_seller": bool(info.get("hasMultipleSeller")),
            "sellers": [],
            "note": "" if info.get("hasMultipleSeller") else "Bu barkodda başka satıcı yok.",
        }

    # ---- Webhook ----

    def register_webhook(self, url: str, events: list[str]) -> dict:
        if not self.configured:
            return {"ok": True, "mode": "demo", "id": "demo-webhook-1",
                    "message": "Webhook kaydedildi (DEMO)."}
        # createWebhook: authenticationType zorunlu (API_KEY | BASIC_AUTHENTICATION).
        # NONE desteklenmez; kendi ucumuzu doğrulamak için rastgele apiKey üretilir.
        api_key = uuid.uuid4().hex
        data = self._req("POST", f"/webhook/sellers/{self.seller_id}/webhooks",
                         json={"url": url, "authenticationType": "API_KEY", "apiKey": api_key,
                               "subscribedStatuses": [e.upper() for e in (events or ["CREATED"])]})
        return {"ok": True, "mode": "live", "id": str(data.get("id", "")), "api_key": api_key,
                "message": "Webhook Trendyol'a kaydedildi. Gelen isteklerde bu apiKey ile doğrulama yapın."}

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
