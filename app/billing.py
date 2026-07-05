"""Paketler, abonelik ve ödeme (checkout).

Ödeme sağlayıcısı `PAYMENT_PROVIDER` env değişkeniyle seçilir:
  - demo (varsayılan): kart Luhn kontrolünden geçer, ödeme kaydı oluşur, plan aktive edilir.
    Gerçek para çekilmez — canlıya çıkarken iyzico/Stripe/PayTR entegrasyonu
    `_charge_card` fonksiyonuna eklenir (tek nokta).
"""
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import db

router = APIRouter(prefix="/api/billing", tags=["billing"])

PERIOD_SECONDS = 30 * 24 * 3600  # aylık dönem

# limit değeri -1 => sınırsız
PLANS: dict[str, dict] = {
    "free": {
        "name": "Ücretsiz",
        "price": 0,
        "tagline": "Denemek için — kart gerekmez",
        "limits": {"ai_listing": 3, "review_scrape": 30, "insight": 2, "image_gen": 4, "stores": 1, "ai_answer": 5},
        "features": {"returns": False, "shipments": False, "analytics": False, "qa": True,
                     "finance": False, "buybox": False, "dynamic_pricing": False},
        "bullets": [
            "Sipariş, ürün & stok yönetimi",
            "Ayda 3 AI ürün ilanı",
            "Ayda 5 AI soru cevabı",
            "Ayda 30 yorum çekme ve analiz",
            "1 pazaryeri bağlantısı",
        ],
    },
    "starter": {
        "name": "Başlangıç",
        "price": 299,
        "tagline": "Yeni büyüyen mağazalar için",
        "limits": {"ai_listing": 30, "review_scrape": 500, "insight": 20, "image_gen": 40, "stores": 2, "ai_answer": 100},
        "features": {"returns": True, "shipments": True, "analytics": False, "qa": True,
                     "finance": True, "buybox": False, "dynamic_pricing": False},
        "bullets": [
            "Sipariş, ürün & stok yönetimi + stok alarmı",
            "Ayda 30 AI ürün ilanı",
            "Ayda 100 AI soru cevabı",
            "Ayda 500 yorum çekme + 20 içgörü + 40 görsel",
            "Finans modülü (komisyon & hakediş)",
            "2 pazaryeri bağlantısı",
            "İade yönetimi ve kargo takibi",
        ],
    },
    "pro": {
        "name": "Profesyonel",
        "price": 699,
        "tagline": "En popüler — tam otomasyon",
        "popular": True,
        "limits": {"ai_listing": 200, "review_scrape": -1, "insight": -1, "image_gen": 200, "stores": 5, "ai_answer": -1},
        "features": {"returns": True, "shipments": True, "analytics": True, "qa": True,
                     "finance": True, "buybox": True, "dynamic_pricing": True},
        "bullets": [
            "Başlangıç'taki her şey",
            "Ayda 200 AI ürün ilanı + 200 satış görseli",
            "Sınırsız AI soru cevabı ve içgörü",
            "Buybox takipçisi + AI dinamik fiyatlandırma",
            "Satış & kârlılık analitiği (ciro, kâr, şehir, trend)",
            "5 pazaryeri bağlantısı",
        ],
    },
    "enterprise": {
        "name": "Kurumsal",
        "price": 1999,
        "tagline": "Yüksek hacimli satıcılar ve ajanslar",
        "limits": {"ai_listing": -1, "review_scrape": -1, "insight": -1, "image_gen": -1, "stores": -1, "ai_answer": -1},
        "features": {"returns": True, "shipments": True, "analytics": True, "qa": True,
                     "finance": True, "buybox": True, "dynamic_pricing": True},
        "bullets": [
            "Sınırsız AI ilan, görsel, içgörü ve cevap",
            "Sınırsız pazaryeri bağlantısı",
            "Tüm analitik + buybox + dinamik fiyat",
            "Öncelikli destek",
        ],
    },
}

METRIC_LABELS = {
    "ai_listing": "AI ürün ilanı",
    "review_scrape": "yorum çekme",
    "insight": "AI içgörü",
    "image_gen": "satış görseli üretimi",
    "stores": "pazaryeri bağlantısı",
    "ai_answer": "AI soru cevabı",
}


def get_subscription(user_id: str) -> dict:
    sub = db.query_one("SELECT * FROM subscriptions WHERE user_id=?", (user_id,))
    if not sub:
        db.execute(
            "INSERT INTO subscriptions (id, user_id, plan, status, started_at) VALUES (?,?,?,?,?)",
            (db.new_id(), user_id, "free", "active", db.now()),
        )
        sub = db.query_one("SELECT * FROM subscriptions WHERE user_id=?", (user_id,))
    # Süresi dolmuş ücretli plan -> ücretsiz plana düşür
    if sub["plan"] != "free" and sub.get("renews_at") and sub["renews_at"] < db.now():
        db.execute(
            "UPDATE subscriptions SET plan='free', status='expired', renews_at=NULL WHERE user_id=?",
            (user_id,),
        )
        sub = db.query_one("SELECT * FROM subscriptions WHERE user_id=?", (user_id,))
    return sub


def plan_of(user_id: str) -> dict:
    return PLANS[get_subscription(user_id)["plan"]]


def limit_for(user_id: str, metric: str) -> int:
    return plan_of(user_id)["limits"].get(metric, 0)


def check_limit(user_id: str, metric: str, amount: int = 1) -> None:
    """Limit aşılırsa 402 fırlatır; geçmesi durumunda sayıcıyı atomik olarak artırır.
    SQLite transaction içinde kontrol+artış yapılır — eşzamanlı isteklerde TOCTOU olmaz.
    """
    limit = limit_for(user_id, metric)
    if limit == -1:
        db.increment_usage(user_id, metric, amount)
        return
    allowed, current = db.check_and_increment_usage(user_id, metric, limit, amount)
    if not allowed:
        label = METRIC_LABELS.get(metric, metric)
        raise HTTPException(
            402,
            f"Bu ay için {label} limitine ulaştın ({current}/{limit}). "
            f"Daha yüksek bir pakete geçerek devam edebilirsin.",
        )


def check_feature(user_id: str, feature: str) -> None:
    if not plan_of(user_id)["features"].get(feature, False):
        # Özelliği içeren en ucuz planı bul, mesajda öner
        for key in ("starter", "pro", "enterprise"):
            if PLANS[key]["features"].get(feature, False):
                raise HTTPException(
                    402,
                    f"Bu özellik mevcut paketinde yok — {PLANS[key]['name']} veya üzeri pakete geçerek kullanabilirsin.",
                )
        raise HTTPException(402, "Bu özellik mevcut paketinde yok.")


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _charge_card(user_id: str, plan_key: str, card: "CardInfo") -> dict:
    """Ödemeyi sağlayıcıya iletir. Canlı PSP (iyzico/Stripe/PayTR) buraya bağlanır."""
    provider = os.getenv("PAYMENT_PROVIDER", "demo")
    number = card.number.replace(" ", "")
    if provider == "demo":
        if not _luhn_ok(number):
            raise HTTPException(400, "Kart numarası geçersiz görünüyor — kontrol edip tekrar dene.")
        if not card.holder.strip():
            raise HTTPException(400, "Kart üzerindeki isim boş olamaz.")
        if len(card.cvc.strip()) not in (3, 4):
            raise HTTPException(400, "CVC 3 veya 4 haneli olmalı.")
        return {"provider": "demo", "status": "paid", "last4": number[-4:]}
    raise HTTPException(500, f"Bilinmeyen ödeme sağlayıcısı: {provider}")


# ---- API şemaları ----

class CardInfo(BaseModel):
    number: str
    holder: str
    exp_month: int
    exp_year: int
    cvc: str


class CheckoutRequest(BaseModel):
    plan: str
    card: Optional[CardInfo] = None


def _require_user(request: Request) -> dict:
    from . import auth

    user = auth.get_session_user(request)
    if not user:
        raise HTTPException(401, "Bu işlem için giriş yapmalısın.")
    return user


@router.get("/plans")
def list_plans():
    # provider bilgisi gizlenir — sunucu yapılandırması anonim kullanıcılara açıklanmaz
    return {
        "plans": [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "bullets" or True}} for k, v in PLANS.items()],
    }


@router.get("/subscription")
def subscription(request: Request):
    user = _require_user(request)
    sub = get_subscription(user["id"])
    plan = PLANS[sub["plan"]]
    return {
        "subscription": sub,
        "plan": {"key": sub["plan"], **plan},
        "usage": db.usage_summary(user["id"]),
        "limits": plan["limits"],
    }


@router.post("/checkout")
def checkout(req: CheckoutRequest, request: Request):
    user = _require_user(request)
    if req.plan not in PLANS:
        raise HTTPException(400, "Geçersiz paket.")
    if req.plan == "free":
        db.execute(
            "UPDATE subscriptions SET plan='free', status='active', renews_at=NULL, canceled_at=NULL WHERE user_id=?",
            (user["id"],),
        )
        return {"ok": True, "plan": "free", "message": "Ücretsiz pakete geçildi."}
    if req.card is None:
        raise HTTPException(400, "Ödeme için kart bilgisi gerekli.")
    exp_ok = (req.card.exp_year, req.card.exp_month) >= (
        int(time.strftime("%Y")), int(time.strftime("%m")),
    ) and 1 <= req.card.exp_month <= 12
    if not exp_ok:
        raise HTTPException(400, "Kartın son kullanma tarihi geçmiş görünüyor.")

    result = _charge_card(user["id"], req.plan, req.card)
    price = PLANS[req.plan]["price"]
    db.execute(
        "INSERT INTO payments (id, user_id, plan, amount, currency, card_last4, provider, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (db.new_id(), user["id"], req.plan, price, "TRY", result["last4"], result["provider"], result["status"], db.now()),
    )
    db.execute(
        "UPDATE subscriptions SET plan=?, status='active', started_at=?, renews_at=?, canceled_at=NULL WHERE user_id=?",
        (req.plan, db.now(), db.now() + PERIOD_SECONDS, user["id"]),
    )
    demo_note = (
        " (DEMO ödeme — gerçek para çekilmedi; canlıda PAYMENT_PROVIDER ile PSP bağlanır)"
        if result["provider"] == "demo" else ""
    )
    return {
        "ok": True,
        "plan": req.plan,
        "message": f"{PLANS[req.plan]['name']} paketi aktif edildi.{demo_note}",
    }


@router.post("/cancel")
def cancel(request: Request):
    user = _require_user(request)
    sub = get_subscription(user["id"])
    if sub["plan"] == "free":
        raise HTTPException(400, "Zaten ücretsiz pakettesin.")
    db.execute(
        "UPDATE subscriptions SET status='canceled', canceled_at=? WHERE user_id=?",
        (db.now(), user["id"]),
    )
    return {
        "ok": True,
        "message": "Abonelik iptal edildi — dönem sonuna kadar paketin aktif kalır, sonra ücretsiz pakete geçer.",
    }


@router.get("/invoices")
def invoices(request: Request):
    user = _require_user(request)
    rows = db.query(
        "SELECT id, plan, amount, currency, card_last4, provider, status, created_at "
        "FROM payments WHERE user_id=? ORDER BY created_at DESC",
        (user["id"],),
    )
    for r in rows:
        r["plan_name"] = PLANS.get(r["plan"], {}).get("name", r["plan"])
    return rows
