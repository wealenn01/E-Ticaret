"""Pazaryeri operasyon merkezi — sipariş, ürün/stok, soru-cevap, iade,
finans, buybox, dinamik fiyat ve webhook uçları (/api/mp/*).

Tüm uçlar kullanıcının bağlı mağazalarındaki verileri normalize edip birleştirir.
Bağlantı anahtarları girilmemişse istemciler DEMO veriyle çalışır; arayüz aynıdır.
"""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import ai_seller, db
from .marketplace_api import MarketplaceError, NotSupported, client_for, clients_for_user

router = APIRouter(prefix="/api/mp", tags=["marketplace-ops"])


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Oturum gerekli.")
    return user


def _clients(request: Request, platform: Optional[str] = None):
    clients = clients_for_user(_user(request)["id"])
    if platform:
        clients = [c for c in clients if c.platform == platform]
        if not clients:
            raise HTTPException(404, f"'{platform}' bağlantısı bulunamadı — önce Hesabım'dan mağazayı bağla.")
    return clients


def _collect(clients, fn_name: str, *args, **kwargs) -> dict:
    """Tüm istemcilerden veri toplar; desteklemeyen/hata veren platformları not eder."""
    rows, notes = [], []
    for c in clients:
        try:
            rows.extend(getattr(c, fn_name)(*args, **kwargs))
        except NotSupported as e:
            notes.append({"platform": c.platform, "detail": str(e)})
        except MarketplaceError as e:
            notes.append({"platform": c.platform, "detail": str(e)})
    return {"rows": rows, "notes": notes,
            "platforms": [{"platform": c.platform, "mode": c.mode} for c in clients]}


# ---- ayarlar (düşük stok eşiği vb.) ----

def _ops_settings_file():
    return ai_seller._data_dir() / "ops_settings.json"


def load_ops_settings() -> dict:
    f = _ops_settings_file()
    default = {"low_stock_threshold": 5}
    if f.exists():
        return {**default, **json.loads(f.read_text(encoding="utf-8"))}
    return default


@router.get("/settings")
def get_settings(request: Request):
    _user(request)
    return load_ops_settings()


class OpsSettingsRequest(BaseModel):
    low_stock_threshold: int = 5


@router.post("/settings")
def save_settings(req: OpsSettingsRequest, request: Request):
    _user(request)
    data = {"low_stock_threshold": max(0, req.low_stock_threshold)}
    _ops_settings_file().write_text(json.dumps(data), encoding="utf-8")
    return data


# ---- genel bakış (dashboard) ----

@router.get("/overview")
def overview(request: Request):
    clients = _clients(request)
    orders = _collect(clients, "list_orders", days=14)
    products = _collect(clients, "list_products")
    questions = _collect(clients, "list_questions")
    claims = _collect(clients, "list_claims")
    threshold = load_ops_settings()["low_stock_threshold"]

    by_status: dict[str, int] = {}
    revenue_14d = 0.0
    for o in orders["rows"]:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        if o["status"] not in ("Cancelled", "Returned"):
            revenue_14d += o.get("total", 0)
    low_stock = [p for p in products["rows"] if p["stock"] <= threshold and p["status"] == "active"]
    return {
        "platforms": orders["platforms"],
        "orders_by_status": by_status,
        "orders_total": len(orders["rows"]),
        "revenue_14d": round(revenue_14d, 2),
        "waiting_questions": sum(1 for q in questions["rows"] if q["status"] == "WAITING_FOR_ANSWER"),
        "open_claims": sum(1 for c in claims["rows"] if c["status"] in ("WaitingInAction", "Created", "Open")),
        "low_stock": low_stock[:10],
        "low_stock_count": len(low_stock),
        "threshold": threshold,
    }


# ---- siparişler ----

@router.get("/orders")
def orders(request: Request, status: Optional[str] = None, platform: Optional[str] = None, days: int = 14):
    result = _collect(_clients(request, platform), "list_orders", status=status, days=days)
    result["rows"].sort(key=lambda o: o.get("date", ""), reverse=True)
    return result


# ---- ürünler & stok ----

@router.get("/products")
def products(request: Request, platform: Optional[str] = None):
    result = _collect(_clients(request, platform), "list_products")
    threshold = load_ops_settings()["low_stock_threshold"]
    for p in result["rows"]:
        p["low_stock"] = p["stock"] <= threshold
    result["threshold"] = threshold
    return result


class PriceStockRequest(BaseModel):
    platform: str
    items: list[dict]  # [{sku|barcode, price?, list_price?, stock?}]


@router.post("/price-stock")
def price_stock(req: PriceStockRequest, request: Request):
    if not req.items:
        raise HTTPException(400, "Güncellenecek ürün yok.")
    client = _clients(request, req.platform)[0]
    try:
        return client.update_price_stock(req.items)
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


# ---- müşteri soruları ----

@router.get("/questions")
def questions(request: Request, status: Optional[str] = None, platform: Optional[str] = None):
    result = _collect(_clients(request, platform), "list_questions", status=status)
    result["rows"].sort(key=lambda q: (q["status"] != "WAITING_FOR_ANSWER", q.get("date", "")), reverse=False)
    return result


QA_SYSTEM = """Sen Türkçe konuşan bir e-ticaret mağazasının müşteri iletişim uzmanısın.
Müşterinin ürün sorusuna mağaza adına cevap yaz. Kurallar:
- Kibar, samimi ve profesyonel; 2-4 cümle.
- Bilmediğin teknik detayı uydurma; ürün adından çıkarılabilecek makul bilgiyi kullan.
- Pazaryeri kuralları gereği telefon/link/e-posta paylaşma.
Şu JSON formatında yanıt ver: {"answer": "cevap metni"}"""


class SuggestAnswerRequest(BaseModel):
    question: str
    product: str = ""


@router.post("/questions/suggest")
def suggest_answer(req: SuggestAnswerRequest, request: Request):
    from . import billing

    user = _user(request)
    if not req.question.strip():
        raise HTTPException(400, "Soru metni boş olamaz.")
    if not ai_seller.ai_available():
        raise HTTPException(503, "AI cevap önerisi için GEMINI_API_KEY gerekli.")
    billing.check_limit(user["id"], "ai_answer")
    result = ai_seller._chat_json(QA_SYSTEM, json.dumps(
        {"ürün": req.product, "müşteri_sorusu": req.question}, ensure_ascii=False))
    return {"answer": result.get("answer", "")}


class AnswerRequest(BaseModel):
    platform: str
    question_id: str
    text: str


@router.post("/questions/answer")
def answer(req: AnswerRequest, request: Request):
    if len(req.text.strip()) < 10:
        raise HTTPException(400, "Cevap en az 10 karakter olmalı.")
    client = _clients(request, req.platform)[0]
    try:
        return client.answer_question(req.question_id, req.text.strip())
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


# ---- iadeler & tazmin ----

@router.get("/claims")
def claims(request: Request, platform: Optional[str] = None):
    result = _collect(_clients(request, platform), "list_claims")
    result["rows"].sort(key=lambda c: c.get("date", ""), reverse=True)
    return result


class ClaimReviewRequest(BaseModel):
    platform: str
    claim_id: str
    approve: bool
    reason: str = ""


@router.post("/claims/review")
def review_claim(req: ClaimReviewRequest, request: Request):
    client = _clients(request, req.platform)[0]
    try:
        return client.review_claim(req.claim_id, req.approve, req.reason)
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


# ---- finans ----

@router.get("/finance")
def finance(request: Request, days: int = 30):
    from . import billing

    billing.check_feature(_user(request)["id"], "finance")
    clients = _clients(request)
    out, notes = [], []
    for c in clients:
        try:
            for row in c.settlements(days=days):
                row["platform"] = c.platform
                row["mode"] = c.mode
                out.append(row)
        except (NotSupported, MarketplaceError) as e:
            notes.append({"platform": c.platform, "detail": str(e)})
    totals = {
        "gross": round(sum(r["gross"] for r in out), 2),
        "commission": round(sum(r["commission"] for r in out), 2),
        "shipping": round(sum(r["shipping"] for r in out), 2),
        "refunds": round(sum(r["refunds"] for r in out), 2),
        "net": round(sum(r["net"] for r in out), 2),
    }
    return {"rows": out, "totals": totals, "notes": notes}


# ---- buybox takipçisi ----

def _buybox_file():
    return ai_seller._data_dir() / "buybox.json"


def _load_tracked() -> list[dict]:
    f = _buybox_file()
    if not f.exists():
        return []
    items = json.loads(f.read_text(encoding="utf-8"))
    changed = False
    for t in items:
        if not t.get("id"):
            t["id"] = db.new_id()
            changed = True
    if changed:
        _save_tracked(items)
    return items


def _save_tracked(items: list[dict]) -> None:
    _buybox_file().write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


@router.get("/buybox")
def buybox(request: Request):
    from . import billing

    user = _user(request)
    billing.check_feature(user["id"], "buybox")
    clients = {c.platform: c for c in _clients(request)}
    tracked = _load_tracked()
    rows, notes = [], []
    for t in tracked:
        client = clients.get(t["platform"])
        if client is None:
            client = client_for(t["platform"], {})
        try:
            info = client.buybox_info(t["sku_or_url"])
            rows.append({**t, **info, "mode": client.mode})
        except (NotSupported, MarketplaceError) as e:
            rows.append({**t, "error": str(e)})
    return {"rows": rows, "notes": notes}


class TrackRequest(BaseModel):
    platform: str
    sku_or_url: str
    label: str = ""


@router.post("/buybox")
def track_buybox(req: TrackRequest, request: Request):
    from . import billing

    billing.check_feature(_user(request)["id"], "buybox")
    tracked = _load_tracked()
    if any(t["sku_or_url"] == req.sku_or_url and t["platform"] == req.platform for t in tracked):
        raise HTTPException(409, "Bu ürün zaten takipte.")
    if len(tracked) >= 50:
        raise HTTPException(400, "En fazla 50 ürün takip edebilirsin.")
    tracked.append({
        "id": db.new_id(),
        "platform": req.platform,
        "sku_or_url": req.sku_or_url.strip(),
        "label": req.label.strip(),
    })
    _save_tracked(tracked)
    return {"ok": True, "count": len(tracked)}


@router.delete("/buybox/{track_id}")
def untrack_buybox(track_id: str, request: Request):
    from . import billing

    billing.check_feature(_user(request)["id"], "buybox")
    tracked = _load_tracked()
    idx = next((i for i, t in enumerate(tracked) if t.get("id") == track_id), -1)
    if idx < 0:
        raise HTTPException(404, "Takip kaydı bulunamadı.")
    tracked.pop(idx)
    _save_tracked(tracked)
    return {"ok": True}


# ---- dinamik fiyat önerisi ----

PRICING_SYSTEM = """Sen Türkçe konuşan bir e-ticaret fiyatlandırma uzmanısın.
Ürünün mevcut fiyatı, rakip fiyatları ve (varsa) maliyeti verilecek.
Buybox'ı kazanmak ile kâr marjını korumak arasında denge kuran bir fiyat öner.
Şu JSON formatında yanıt ver:
{"suggested_price": 123.45, "strategy": "1-2 cümle strateji", "warning": "marj riski varsa uyarı, yoksa boş string"}"""


class PricingRequest(BaseModel):
    platform: str
    sku_or_url: str
    cost: Optional[float] = None


@router.post("/pricing/suggest")
def pricing_suggest(req: PricingRequest, request: Request):
    from . import billing

    user = _user(request)
    billing.check_feature(user["id"], "dynamic_pricing")
    if not ai_seller.ai_available():
        raise HTTPException(503, "Dinamik fiyat önerisi için GEMINI_API_KEY gerekli.")
    client = _clients(request, req.platform)[0]
    try:
        info = client.buybox_info(req.sku_or_url)
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))
    billing.check_limit(user["id"], "ai_answer")
    payload = {"mevcut_fiyat": info["our_price"], "buybox_fiyatı": info["buybox_price"],
               "rakipler": info.get("sellers", []), "maliyet": req.cost}
    result = ai_seller._chat_json(PRICING_SYSTEM, json.dumps(payload, ensure_ascii=False))
    return {**result, "buybox": info}


# ---- webhook yönetimi ----

@router.get("/webhooks")
def webhooks(request: Request, platform: str = "trendyol"):
    client = _clients(request, platform)[0]
    try:
        return {"rows": client.list_webhooks(), "mode": client.mode}
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


class WebhookRequest(BaseModel):
    platform: str = "trendyol"
    url: str
    events: list[str] = ["CREATED"]


@router.post("/webhooks")
def add_webhook(req: WebhookRequest, request: Request):
    if not req.url.startswith("https://"):
        raise HTTPException(400, "Webhook URL'si https:// ile başlamalı.")
    client = _clients(request, req.platform)[0]
    try:
        return client.register_webhook(req.url.strip(), req.events)
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str, request: Request, platform: str = "trendyol"):
    client = _clients(request, platform)[0]
    try:
        return client.delete_webhook(webhook_id)
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))
