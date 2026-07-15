"""Pazaryeri operasyon merkezi — sipariş, ürün/stok, soru-cevap, iade,
finans, buybox, dinamik fiyat ve webhook uçları (/api/mp/*).

Tüm uçlar kullanıcının bağlı mağazalarındaki verileri normalize edip birleştirir.
Bağlantı anahtarları girilmemişse istemciler DEMO veriyle çalışır; arayüz aynıdır.
"""
import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import ai_seller, db
from .marketplace_api import MarketplaceError, NotSupported, client_for, clients_for_user

router = APIRouter(prefix="/api/mp", tags=["marketplace-ops"])

# --- Pazaryeri okuma önbelleği ---
# Pazaryeri API çağrıları yavaştır (ör. Trendyol iade listesi ~1.8s). Görünümler
# arası gezinmede aynı veriyi tekrar tekrar çekmemek için kısa ömürlü TTL önbellek
# kullanılır. Yazma işlemleri (fiyat/stok, iade kararı, cevap) ilgili anahtarı boşaltır.
_MP_TTL = 45.0
_mp_cache: dict = {}
_mp_lock = threading.Lock()


def _cache_key(user_id: str, platform: str, fn_name: str, args, kwargs):
    return (user_id, platform, fn_name, repr(args), repr(sorted(kwargs.items())))


def _client_fetch(user_id: str, client, fn_name: str, *args, **kwargs):
    """Tek istemciden TTL önbellekli veri çeker. Hataları çağırana yükseltir."""
    key = _cache_key(user_id, client.platform, fn_name, args, kwargs)
    now = time.time()
    with _mp_lock:
        hit = _mp_cache.get(key)
        if hit and now - hit[0] < _MP_TTL:
            return hit[1]
    rows = getattr(client, fn_name)(*args, **kwargs)
    with _mp_lock:
        _mp_cache[key] = (now, rows)
    return rows


def invalidate_mp_cache(user_id: str, platform: Optional[str] = None, fn_name: Optional[str] = None) -> None:
    """Yazma sonrası bayat veriyi temizler."""
    with _mp_lock:
        for k in list(_mp_cache):
            if (k[0] == user_id
                    and (platform is None or k[1] == platform)
                    and (fn_name is None or k[2] == fn_name)):
                _mp_cache.pop(k, None)


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


def _collect(user_id: str, clients, fn_name: str, *args, **kwargs) -> dict:
    """Tüm istemcilerden (önbellekli) veri toplar; hata veren platformları not eder."""
    rows, notes = [], []
    for c in clients:
        try:
            rows.extend(_client_fetch(user_id, c, fn_name, *args, **kwargs))
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
    default = {"low_stock_threshold": 5, "active_platform": None}
    if f.exists():
        return {**default, **json.loads(f.read_text(encoding="utf-8"))}
    return default


def save_ops_settings(patch: dict) -> dict:
    data = load_ops_settings()
    data.update(patch)
    _ops_settings_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


@router.get("/settings")
def get_settings(request: Request):
    _user(request)
    return load_ops_settings()


class OpsSettingsRequest(BaseModel):
    low_stock_threshold: Optional[int] = None
    active_platform: Optional[str] = None


@router.post("/settings")
def save_settings(req: OpsSettingsRequest, request: Request):
    _user(request)
    patch: dict = {}
    if req.low_stock_threshold is not None:
        patch["low_stock_threshold"] = max(0, req.low_stock_threshold)
    if req.active_platform is not None:
        plat = req.active_platform.strip().lower() if req.active_platform else ""
        if plat and plat not in ("trendyol", "hepsiburada", "n11", "amazon"):
            raise HTTPException(400, "Geçersiz platform.")
        if plat:
            if not _clients(request, plat):
                raise HTTPException(404, f"'{plat}' bağlantısı bulunamadı.")
        patch["active_platform"] = plat or None
    return save_ops_settings(patch)


# ---- genel bakış (dashboard) ----

@router.get("/overview")
def overview(request: Request, platform: Optional[str] = None):
    uid = _user(request)["id"]
    clients = _clients(request, platform)
    days_window = 30
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_orders = ex.submit(_collect, uid, clients, "list_orders", days=days_window)
        f_products = ex.submit(_collect, uid, clients, "list_products")
        f_questions = ex.submit(_collect, uid, clients, "list_questions")
        f_claims = ex.submit(_collect, uid, clients, "list_claims")
        orders = f_orders.result()
        products = f_products.result()
        questions = f_questions.result()
        claims = f_claims.result()
    threshold = load_ops_settings()["low_stock_threshold"]
    orders_rows = orders["rows"]

    by_status: dict[str, int] = {}
    revenue_30d = 0.0
    sku_qty: dict[str, int] = defaultdict(int)
    sku_last_qty: dict[str, int] = {}
    for o in orders_rows:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
        if o["status"] not in ("Cancelled", "Returned"):
            revenue_30d += o.get("total", 0)
            for it in o.get("items") or []:
                sku = it.get("sku") or ""
                q = int(it.get("qty") or 1)
                sku_qty[sku] += q
                sku_last_qty[sku] = q

    today = datetime.now().date()
    day_keys = [(today - timedelta(days=i)).isoformat() for i in range(days_window - 1, -1, -1)]
    daily_count = {d: 0 for d in day_keys}
    daily_rev = {d: 0.0 for d in day_keys}
    for o in orders_rows:
        d = (o.get("date") or "")[:10]
        if d not in daily_count:
            continue
        daily_count[d] += 1
        if o["status"] not in ("Cancelled", "Returned"):
            daily_rev[d] += float(o.get("total") or 0)

    orders_daily = [{"date": d, "count": daily_count[d]} for d in day_keys]
    revenue_daily = [{"date": d, "revenue": round(daily_rev[d], 2)} for d in day_keys]
    last7_rev = sum(daily_rev[d] for d in day_keys[-7:])
    prev7_rev = sum(daily_rev[d] for d in day_keys[-14:-7])
    if prev7_rev > 0:
        revenue_change_pct = round((last7_rev - prev7_rev) / prev7_rev * 100, 1)
    else:
        revenue_change_pct = 0.0

    prod_rev: dict[str, float] = defaultdict(float)
    for o in orders_rows:
        if o["status"] in ("Cancelled", "Returned"):
            continue
        for it in o.get("items") or []:
            name = (it.get("title") or it.get("sku") or "?")[:60]
            prod_rev[name] += float(it.get("price") or 0) * int(it.get("qty") or 1)
    top_products = [
        {"name": k, "revenue": round(v, 2)}
        for k, v in sorted(prod_rev.items(), key=lambda x: -x[1])[:3]
    ]

    recent_questions = [
        {
            "product": q.get("product", ""),
            "question": (q.get("question") or "")[:90],
            "date": (q.get("date") or "")[:16],
        }
        for q in questions["rows"]
        if q.get("status") == "WAITING_FOR_ANSWER"
    ][:4]

    claim_reasons: dict[str, int] = defaultdict(int)
    for c in claims["rows"]:
        if c.get("status") in ("WaitingInAction", "Created", "Open"):
            reason = (c.get("reason") or "Diğer")[:50]
            claim_reasons[reason] += 1
    claim_reasons_list = [
        {"reason": k, "count": v}
        for k, v in sorted(claim_reasons.items(), key=lambda x: -x[1])
    ]

    low_stock = [p for p in products["rows"] if p["stock"] <= threshold and p["status"] == "active"]
    for p in low_stock:
        sku = p.get("sku") or ""
        sold = sku_qty.get(sku, 0)
        p["avg_daily_sales"] = round(sold / max(days_window, 1), 2)
        p["last_order_qty"] = sku_last_qty.get(sku, 0)

    recent_activity = []
    for o in sorted(orders_rows, key=lambda x: x.get("date") or "", reverse=True)[:6]:
        recent_activity.append({
            "type": "order",
            "id": o.get("order_id", ""),
            "status": o.get("status", ""),
            "platform": o.get("platform", "trendyol"),
            "date": (o.get("date") or "")[:16],
            "total": o.get("total", 0),
        })

    return {
        "platforms": orders["platforms"],
        "orders_by_status": by_status,
        "orders_total": len(orders_rows),
        "revenue_30d": round(revenue_30d, 2),
        "revenue_14d": round(revenue_30d, 2),
        "revenue_change_pct": revenue_change_pct,
        "waiting_questions": sum(1 for q in questions["rows"] if q["status"] == "WAITING_FOR_ANSWER"),
        "open_claims": sum(1 for c in claims["rows"] if c["status"] in ("WaitingInAction", "Created", "Open")),
        "low_stock": low_stock[:12],
        "low_stock_count": len(low_stock),
        "threshold": threshold,
        "orders_daily": orders_daily,
        "revenue_daily": revenue_daily,
        "top_products": top_products,
        "recent_questions": recent_questions,
        "claim_reasons": claim_reasons_list,
        "recent_activity": recent_activity,
    }


# ---- siparişler ----

@router.get("/orders")
def orders(request: Request, status: Optional[str] = None, platform: Optional[str] = None, days: int = 14):
    uid = _user(request)["id"]
    result = _collect(uid, _clients(request, platform), "list_orders", status=status, days=days)
    result["rows"].sort(key=lambda o: o.get("date", ""), reverse=True)
    return result


# ---- ürünler & stok ----

@router.get("/products")
def products(request: Request, platform: Optional[str] = None):
    uid = _user(request)["id"]
    result = _collect(uid, _clients(request, platform), "list_products")
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
    uid = _user(request)["id"]
    client = _clients(request, req.platform)[0]
    try:
        result = client.update_price_stock(req.items)
        invalidate_mp_cache(uid, req.platform, "list_products")
        return result
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


# ---- müşteri soruları ----

@router.get("/questions")
def questions(request: Request, status: Optional[str] = None, platform: Optional[str] = None):
    uid = _user(request)["id"]
    result = _collect(uid, _clients(request, platform), "list_questions", status=status)
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
    uid = _user(request)["id"]
    client = _clients(request, req.platform)[0]
    try:
        result = client.answer_question(req.question_id, req.text.strip())
        invalidate_mp_cache(uid, req.platform, "list_questions")
        return result
    except (NotSupported, MarketplaceError) as e:
        raise HTTPException(502, str(e))


# ---- iadeler & tazmin ----

DEFAULT_RETURN_POLICY = {
    "max_return_days": 30,
    "rules_text": (
        "İade süresi sipariş/teslimat tarihinden itibaren en fazla 30 gündür. "
        "Süre aşımında iade reddedilir. Yanlış ürün, hasarlı ürün veya eksik ürün "
        "talepleri süre içindeyken kabul edilebilir. Tazmin talepleri ayrı değerlendirilir."
    ),
}

CLAIM_AI_SYSTEM = """Sen Türkçe konuşan bir e-ticaret iade politikası uzmanısın.
Satıcının iade politikasına göre müşteri iade/tazmin talebini değerlendir.
Kurallar:
- Politikaya aykırıysa decision="reject", uygunsa decision="approve".
- reason: satıcıya ve müşteriye gösterilecek kısa, net Türkçe gerekçe (1-2 cümle).
- rationale: satıcıya özel kısa açıklama (neden bu karar).
- confidence: 0 ile 1 arası sayı.
Şu JSON formatında yanıt ver:
{"decision":"approve"|"reject","reason":"...","rationale":"...","confidence":0.0}"""


def _return_policy_from_settings() -> dict:
    s = load_ops_settings()
    days = int(s.get("max_return_days", DEFAULT_RETURN_POLICY["max_return_days"]))
    rules = (s.get("return_rules_text") or DEFAULT_RETURN_POLICY["rules_text"]).strip()
    return {
        "max_return_days": max(1, min(365, days)),
        "rules_text": rules or DEFAULT_RETURN_POLICY["rules_text"],
    }


def _claim_reviews_file():
    return ai_seller._data_dir() / "claim_ai_reviews.json"


def _load_claim_reviews() -> list[dict]:
    f = _claim_reviews_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_claim_reviews(items: list[dict]) -> None:
    _claim_reviews_file().write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _parse_claim_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for cand in (text[:16], text[:10], text):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(cand, fmt)
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _claim_age_days(claim: dict) -> int | None:
    """Sipariş/teslimattan iade talebine kadar geçen gün. Yoksa None."""
    claim_dt = _parse_claim_dt(claim.get("date")) or datetime.now()
    order_dt = _parse_claim_dt(claim.get("order_date") or claim.get("delivery_date"))
    if not order_dt:
        return None
    return max(0, (claim_dt.date() - order_dt.date()).days)


def _rule_based_claim_decision(claim: dict, policy: dict) -> dict | None:
    """Sert politika kuralları — AI yokken veya AI öncesi zorunlu red."""
    days = _claim_age_days(claim)
    max_days = policy["max_return_days"]
    if days is not None and days > max_days:
        return {
            "decision": "reject",
            "reason": (
                f"Bu iade talebi sipariş/teslimattan {days} gün sonra geldi. "
                f"İade politikamız en fazla {max_days} gündür; süre aşıldığı için iade kabul edilemez."
            ),
            "rationale": f"Süre aşımı kuralı: {days} > {max_days} gün.",
            "confidence": 0.98,
            "source": "policy_rule",
        }
    return None


def _ai_claim_decision(claim: dict, policy: dict) -> dict:
    forced = _rule_based_claim_decision(claim, policy)
    if forced and not ai_seller.ai_available():
        return forced
    if not ai_seller.ai_available():
        # Süre içinde / tarih yok → satıcı onayı için kabul öner
        return {
            "decision": "approve",
            "reason": (
                "Talep iade süresi içinde görünüyor veya sipariş tarihi net değil; "
                "politika açısından kabul önerilir. Lütfen kontrol edip onaylayın."
            ),
            "rationale": "AI anahtarı yok; varsayılan süre-içi kabul önerisi.",
            "confidence": 0.55,
            "source": "policy_fallback",
        }
    payload = {
        "politika": policy,
        "talep": {
            "claim_id": claim.get("claim_id"),
            "ürün": claim.get("product"),
            "müşteri_nedeni": claim.get("reason"),
            "tip": claim.get("type"),
            "iade_talep_tarihi": claim.get("date"),
            "sipariş_tarihi": claim.get("order_date") or claim.get("delivery_date") or "",
            "geçen_gün": _claim_age_days(claim),
            "durum": claim.get("status"),
        },
    }
    try:
        result = ai_seller._chat_json(
            CLAIM_AI_SYSTEM, json.dumps(payload, ensure_ascii=False)
        )
        decision = str(result.get("decision", "")).strip().lower()
        if decision not in ("approve", "reject"):
            decision = "reject" if forced else "approve"
        out = {
            "decision": decision,
            "reason": str(result.get("reason") or "").strip()
            or (forced["reason"] if forced else "Politika değerlendirmesi tamamlandı."),
            "rationale": str(result.get("rationale") or "").strip(),
            "confidence": float(result.get("confidence") or 0.7),
            "source": "ai",
        }
        # Sert kural AI'yı ezer (süre aşımı asla onay önerilmez)
        if forced and forced["decision"] == "reject":
            out["decision"] = "reject"
            out["reason"] = forced["reason"]
            out["rationale"] = forced["rationale"] + " (AI önerisi politika kuralıyla düzeltildi.)"
            out["source"] = "policy_rule+ai"
            out["confidence"] = max(out["confidence"], 0.95)
        return out
    except Exception:
        return forced or {
            "decision": "approve",
            "reason": "AI değerlendirmesi yapılamadı; süreyi kontrol edip manuel karar verin.",
            "rationale": "AI hatası — varsayılan kabul önerisi.",
            "confidence": 0.4,
            "source": "error_fallback",
        }


def _review_key(platform: str, claim_id: str) -> str:
    return f"{platform}:{claim_id}"


def _pending_reviews_map() -> dict[str, dict]:
    return {
        _review_key(r["platform"], r["claim_id"]): r
        for r in _load_claim_reviews()
        if r.get("status") == "pending_seller"
    }


def _ensure_ai_reviews_for_claims(rows: list[dict]) -> list[dict]:
    """Bekleyen iadeler için AI önerisi üret (Trendyol'a GÖNDERMEZ)."""
    policy = _return_policy_from_settings()
    reviews = _load_claim_reviews()
    already = {
        _review_key(r["platform"], r["claim_id"])
        for r in reviews
        if r.get("status") in ("pending_seller", "confirmed", "edited")
    }
    actionable = {"WaitingInAction", "Created", "Open"}
    new_ones: list[dict] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for claim in rows:
        if claim.get("status") not in actionable:
            continue
        key = _review_key(claim.get("platform", ""), claim.get("claim_id", ""))
        if key in already:
            continue
        decision = _ai_claim_decision(claim, policy)
        item = {
            "id": f"cair-{int(time.time() * 1000)}-{claim.get('claim_id', '')}",
            "platform": claim.get("platform", ""),
            "claim_id": claim.get("claim_id", ""),
            "order_id": claim.get("order_id", ""),
            "product": claim.get("product", ""),
            "customer_reason": claim.get("reason", ""),
            "proposed_decision": decision["decision"],
            "reason": decision["reason"],
            "rationale": decision.get("rationale", ""),
            "confidence": decision.get("confidence", 0.7),
            "source": decision.get("source", "ai"),
            "status": "pending_seller",
            "created_at": now,
            "claim_item_ids": claim.get("claim_item_ids") or [],
            "order_date": claim.get("order_date") or "",
            "claim_date": claim.get("date") or "",
        }
        reviews.append(item)
        already.add(key)
        new_ones.append(item)

    if new_ones:
        _save_claim_reviews(reviews)
    return new_ones


def _attach_ai_reviews(rows: list[dict]) -> None:
    pending = _pending_reviews_map()
    for claim in rows:
        key = _review_key(claim.get("platform", ""), claim.get("claim_id", ""))
        rev = pending.get(key)
        if rev:
            claim["ai_review"] = {
                "id": rev["id"],
                "proposed_decision": rev["proposed_decision"],
                "reason": rev["reason"],
                "rationale": rev.get("rationale", ""),
                "confidence": rev.get("confidence", 0),
                "source": rev.get("source", ""),
                "status": rev["status"],
                "created_at": rev.get("created_at", ""),
            }


@router.get("/return-policy")
def get_return_policy(request: Request):
    _user(request)
    return _return_policy_from_settings()


class ReturnPolicyRequest(BaseModel):
    max_return_days: int = 30
    rules_text: str = ""


@router.post("/return-policy")
def save_return_policy(req: ReturnPolicyRequest, request: Request):
    _user(request)
    data = load_ops_settings()
    data["max_return_days"] = max(1, min(365, int(req.max_return_days)))
    text = (req.rules_text or "").strip()
    data["return_rules_text"] = text or DEFAULT_RETURN_POLICY["rules_text"]
    _ops_settings_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return _return_policy_from_settings()


@router.get("/claims")
def claims(request: Request, platform: Optional[str] = None, analyze: bool = True):
    uid = _user(request)["id"]
    result = _collect(uid, _clients(request, platform), "list_claims")
    result["rows"].sort(key=lambda c: c.get("date", ""), reverse=True)
    new_reviews: list[dict] = []
    if analyze:
        new_reviews = _ensure_ai_reviews_for_claims(result["rows"])
    _attach_ai_reviews(result["rows"])
    pending_n = sum(1 for r in result["rows"] if r.get("ai_review"))
    result["ai_pending"] = pending_n
    result["ai_new"] = [
        {
            "claim_id": n["claim_id"],
            "product": n["product"],
            "proposed_decision": n["proposed_decision"],
            "reason": n["reason"],
        }
        for n in new_reviews
    ]
    result["return_policy"] = _return_policy_from_settings()
    return result


class ClaimReviewRequest(BaseModel):
    platform: str
    claim_id: str
    approve: bool
    reason: str = ""
    claim_item_ids: Optional[list[str]] = None
    ai_review_id: Optional[str] = None
    from_ai: bool = False


def _mark_ai_review_done(ai_review_id: str | None, claim_id: str, platform: str,
                         final_decision: str, mode: str) -> None:
    reviews = _load_claim_reviews()
    changed = False
    for r in reviews:
        match = (
            (ai_review_id and r.get("id") == ai_review_id)
            or (r.get("claim_id") == claim_id and r.get("platform") == platform
                and r.get("status") == "pending_seller")
        )
        if match:
            r["status"] = "confirmed" if mode == "confirm" else "edited"
            r["final_decision"] = final_decision
            r["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            changed = True
            break
    if changed:
        _save_claim_reviews(reviews)


@router.post("/claims/review")
def review_claim(req: ClaimReviewRequest, request: Request):
    uid = _user(request)["id"]
    client = _clients(request, req.platform)[0]
    try:
        result = client.review_claim(
            req.claim_id, req.approve, req.reason, claim_item_ids=req.claim_item_ids
        )
        invalidate_mp_cache(uid, req.platform, "list_claims")
        _mark_ai_review_done(
            req.ai_review_id,
            req.claim_id,
            req.platform,
            "approve" if req.approve else "reject",
            "confirm" if req.from_ai else "edited",
        )
        return result
    except NotSupported as e:
        raise HTTPException(400, str(e))
    except MarketplaceError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"İade işlemi tamamlanamadı: {str(e)[:200]}")


@router.get("/claims/ai-pending")
def claims_ai_pending(request: Request):
    _user(request)
    pending = [r for r in _load_claim_reviews() if r.get("status") == "pending_seller"]
    pending.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"rows": pending, "count": len(pending)}


# ---- finans ----

@router.get("/finance")
def finance(request: Request, days: int = 30, platform: Optional[str] = None):
    from . import billing

    billing.check_feature(_user(request)["id"], "finance")
    clients = _clients(request, platform)
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
def buybox(request: Request, platform: Optional[str] = None):
    from . import billing

    user = _user(request)
    billing.check_feature(user["id"], "buybox")
    clients = {c.platform: c for c in _clients(request, platform)}
    tracked = _load_tracked()
    if platform:
        tracked = [t for t in tracked if t.get("platform") == platform]
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
