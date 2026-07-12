"""Pazaryeri API istemcileri — ortak taban sınıf ve normalize şemalar.

Her platform istemcisi aynı normalize sözlükleri döndürür, böylece panel tek
arayüzle tüm pazaryerlerini gösterir:

  product   {sku, barcode, title, platform, price, list_price, stock, status, image, url}
  order     {order_id, package_id, platform, date, customer, city, status, total,
             cargo: {carrier, tracking, url}, items: [{sku, title, qty, price}]}
  claim     {claim_id, platform, order_id, product, reason, date, status, type}
  question  {question_id, platform, product, question, date, status, answer}
  settlement{period, gross, commission, shipping, refunds, net, payout_date}
  buybox    {sku, our_price, buybox_price, buybox_seller, we_have_buybox, sellers}

Kimlik bilgileri eksikse istemci DEMO modda çalışır: gerçekçi örnek veri döner,
yazma işlemleri simüle edilir. Anahtarlar girildiğinde aynı kod gerçek API'ye gider.
"""
from datetime import datetime, timedelta
from typing import Any, Optional


class NotSupported(Exception):
    """Platform API'si bu özellik için herkese açık uç yayınlamıyor."""


class MarketplaceError(Exception):
    """Platform API'sinden dönen hata — kullanıcıya gösterilebilir mesaj taşır."""


class MarketplaceClient:
    platform = "base"
    platform_name = "Pazaryeri"

    def __init__(self, creds: Optional[dict] = None):
        self.creds = creds or {}

    @property
    def configured(self) -> bool:
        """Gerçek API çağrısı için gerekli anahtarlar tam mı?"""
        return False

    @property
    def mode(self) -> str:
        return "live" if self.configured else "demo"

    # Her istemci bunları override eder; configured değilse demo veri döndürmelidir.
    def list_products(self, page: int = 0, size: int = 50) -> list[dict]:
        raise NotSupported(f"{self.platform_name}: ürün listeleme desteklenmiyor")

    def update_price_stock(self, items: list[dict]) -> dict:
        """items: [{sku|barcode, price?, list_price?, stock?}]"""
        raise NotSupported(f"{self.platform_name}: fiyat/stok güncelleme desteklenmiyor")

    def list_orders(self, status: Optional[str] = None, days: int = 14) -> list[dict]:
        raise NotSupported(f"{self.platform_name}: sipariş listeleme desteklenmiyor")

    def list_claims(self) -> list[dict]:
        raise NotSupported(f"{self.platform_name}: iade listeleme desteklenmiyor")

    def review_claim(self, claim_id: str, approve: bool, reason: str = "",
                     claim_item_ids: Optional[list[str]] = None) -> dict:
        raise NotSupported(f"{self.platform_name}: iade onay/red desteklenmiyor")

    def list_questions(self, status: Optional[str] = None) -> list[dict]:
        raise NotSupported(f"{self.platform_name}: müşteri soruları desteklenmiyor")

    def answer_question(self, question_id: str, text: str) -> dict:
        raise NotSupported(f"{self.platform_name}: soru cevaplama desteklenmiyor")

    def settlements(self, days: int = 30) -> list[dict]:
        raise NotSupported(f"{self.platform_name}: finans/hakediş desteklenmiyor")

    def buybox_info(self, sku_or_url: str) -> dict:
        raise NotSupported(f"{self.platform_name}: buybox takibi desteklenmiyor")

    def register_webhook(self, url: str, events: list[str]) -> dict:
        raise NotSupported(f"{self.platform_name}: webhook desteklenmiyor")

    def list_webhooks(self) -> list[dict]:
        raise NotSupported(f"{self.platform_name}: webhook desteklenmiyor")

    def delete_webhook(self, webhook_id: str) -> dict:
        raise NotSupported(f"{self.platform_name}: webhook desteklenmiyor")

    # ---- ortak yardımcılar ----

    def _tag(self, rows: list[dict]) -> list[dict]:
        for r in rows:
            r["platform"] = self.platform
            r["mode"] = self.mode
        return rows


# ---------------------------------------------------------------- DEMO verisi

_DEMO_PRODUCTS = [
    {"sku": "SKU-KUPA-01", "barcode": "8680000000011", "title": "El Yapımı Seramik Kupa 300 ml — Mat Bej",
     "price": 259.90, "list_price": 329.90, "stock": 42, "status": "active", "image": "", "url": ""},
    {"sku": "SKU-KULAK-02", "barcode": "8680000000028", "title": "Bluetooth Kulaklık ANC Pro — Siyah",
     "price": 899.00, "list_price": 1099.00, "stock": 7, "status": "active", "image": "", "url": ""},
    {"sku": "SKU-TSHIRT-03", "barcode": "8680000000035", "title": "Pamuklu Oversize T-shirt — Siyah",
     "price": 189.90, "list_price": 249.90, "stock": 120, "status": "active", "image": "", "url": ""},
    {"sku": "SKU-VAZO-04", "barcode": "8680000000042", "title": "Seramik Vazo Mat Bej 20 cm",
     "price": 349.00, "list_price": 429.00, "stock": 3, "status": "active", "image": "", "url": ""},
    {"sku": "SKU-YOGA-05", "barcode": "8680000000059", "title": "Yoga Matı 6mm Kaymaz Taban — Lila",
     "price": 329.00, "list_price": 399.00, "stock": 18, "status": "active", "image": "", "url": ""},
    {"sku": "SKU-LAMBA-06", "barcode": "8680000000066", "title": "Ahşap Masa Lambası — Doğal Ceviz",
     "price": 549.00, "list_price": 649.00, "stock": 0, "status": "passive", "image": "", "url": ""},
]

_DEMO_CUSTOMERS = ["A** Y**", "M** K**", "E** D**", "S** Ç**", "B** T**", "Z** A**"]
_DEMO_CITIES = ["İstanbul", "Ankara", "İzmir", "Bursa", "Antalya", "Gaziantep"]
_DEMO_CARRIERS = ["Yurtiçi Kargo", "Aras Kargo", "MNG Kargo", "Trendyol Express", "PTT Kargo"]
_ORDER_STATUSES = ["Created", "Picking", "Shipped", "Shipped", "Delivered", "Delivered", "Delivered", "Cancelled"]


def _demo_date(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M")


def demo_products() -> list[dict]:
    return [dict(p) for p in _DEMO_PRODUCTS]


def demo_orders(prefix: str) -> list[dict]:
    orders = []
    for i in range(14):
        p = _DEMO_PRODUCTS[i % len(_DEMO_PRODUCTS)]
        qty = (i % 3) + 1
        status = _ORDER_STATUSES[i % len(_ORDER_STATUSES)]
        shipped = status in ("Shipped", "Delivered")
        orders.append({
            "order_id": f"{prefix}{70200 + i * 7}",
            "package_id": f"PKG-{90310 + i * 3}",
            "date": _demo_date(i),
            "customer": _DEMO_CUSTOMERS[i % len(_DEMO_CUSTOMERS)],
            "city": _DEMO_CITIES[i % len(_DEMO_CITIES)],
            "status": status,
            "total": round(p["price"] * qty, 2),
            "cargo": {
                "carrier": _DEMO_CARRIERS[i % len(_DEMO_CARRIERS)] if shipped else "",
                "tracking": f"7{300000000 + i * 991}" if shipped else "",
                "url": "",
            },
            "items": [{"sku": p["sku"], "title": p["title"], "qty": qty, "price": p["price"]}],
        })
    return orders


def demo_claims(prefix: str) -> list[dict]:
    reasons = [
        ("Ürün beklediğim gibi değil", "return"),
        ("Yanlış beden/model gönderildi", "return"),
        ("Ürün hasarlı ulaştı", "return"),
        ("Kargo teslim edilmedi — tazmin talebi", "compensation"),
        ("Ürün eksik geldi", "return"),
    ]
    claims = []
    for i, (reason, ctype) in enumerate(reasons):
        p = _DEMO_PRODUCTS[(i + 1) % len(_DEMO_PRODUCTS)]
        cid = f"CLM-{prefix}{4400 + i}"
        item_id = f"CI-{prefix}{4400 + i}"
        # İlk talep: sipariş ~45 gün önce — iade süresi aşımı senaryosu
        order_days_ago = 45 if i == 0 else (i + 3)
        claims.append({
            "claim_id": cid,
            "order_id": f"{prefix}{70200 + i * 7}",
            "product": p["title"],
            "sku": p["sku"],
            "reason": reason,
            "type": ctype,
            "date": _demo_date(i + 1),
            "order_date": _demo_date(order_days_ago),
            "status": "WaitingInAction" if i < 3 else ("Created" if i == 3 else "Accepted"),
            "claim_item_ids": [item_id],
        })
    return claims


def demo_questions(prefix: str) -> list[dict]:
    qs = [
        ("Bu kupanın kulpu ısınıyor mu? Sıcak içecek için almak istiyorum.", None),
        ("Kulaklığın şarj süresi ne kadar? Kutu ile kaç saat gidiyor?", None),
        ("Bu tişörtün kumaşı çekme yapar mı? XL bedeni var mı?", None),
        ("Vazo su sızdırıyor mu, gerçek çiçek koyabilir miyim?", "Merhaba, vazomuz sırlı seramiktir ve su geçirmez; gönül rahatlığıyla gerçek çiçek koyabilirsiniz. İyi günlerde kullanın! 🌸"),
        ("Yoga matının kokusu var mı? TPE mi?", None),
    ]
    out = []
    for i, (q, a) in enumerate(qs):
        p = _DEMO_PRODUCTS[i % len(_DEMO_PRODUCTS)]
        out.append({
            "question_id": f"Q-{prefix}{8800 + i}",
            "product": p["title"],
            "sku": p["sku"],
            "question": q,
            "date": _demo_date(i),
            "status": "ANSWERED" if a else "WAITING_FOR_ANSWER",
            "answer": a or "",
        })
    return out


def demo_settlements() -> list[dict]:
    rows = []
    for w in range(4):
        gross = 14250.0 - w * 1830
        commission = round(gross * 0.215, 2)
        shipping = round(gross * 0.045, 2)
        refunds = round(gross * 0.05, 2) if w % 2 else 0.0
        rows.append({
            "period": (datetime.now() - timedelta(weeks=w + 1)).strftime("%Y hafta %W"),
            "gross": gross,
            "commission": commission,
            "shipping": shipping,
            "refunds": refunds,
            "net": round(gross - commission - shipping - refunds, 2),
            "payout_date": (datetime.now() - timedelta(weeks=w)).strftime("%Y-%m-%d"),
        })
    return rows


def demo_buybox(sku: str) -> dict:
    our = next((p for p in _DEMO_PRODUCTS if p["sku"] == sku), _DEMO_PRODUCTS[0])
    competitors = [
        {"seller": "HızlıTicaret", "price": round(our["price"] * 0.97, 2), "rating": 8.9},
        {"seller": "MegaStore34", "price": round(our["price"] * 1.04, 2), "rating": 9.4},
        {"seller": "EvShopTR", "price": round(our["price"] * 1.11, 2), "rating": 8.1},
    ]
    best = min([c["price"] for c in competitors] + [our["price"]])
    return {
        "sku": our["sku"],
        "product": our["title"],
        "our_price": our["price"],
        "buybox_price": best,
        "buybox_seller": "Siz" if best == our["price"] else next(c["seller"] for c in competitors if c["price"] == best),
        "we_have_buybox": best == our["price"],
        "sellers": competitors,
    }
