"""Satış & kârlılık analitiği — sipariş CSV importu, kırılımlar, AI içgörüleri.

CSV kolonları: order_id, product_name, sku, quantity, unit_price, cost,
commission_rate, order_date (YYYY-AA-GG), city, status, platform
(status: delivered | shipped | returned | canceled — boşsa delivered sayılır)
"""
import csv
import io
import json
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile

from . import ai_seller

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

REQUIRED_COLS = {"order_id", "product_name", "quantity", "unit_price", "order_date"}


def _orders_file():
    return ai_seller._data_dir() / "orders.json"


def _orders_insights_file():
    return ai_seller._data_dir() / "orders_insights.json"


def load_orders() -> list[dict]:
    f = _orders_file()
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return []


def save_orders(orders: list[dict]) -> None:
    _orders_file().write_text(json.dumps(orders, ensure_ascii=False, indent=1), encoding="utf-8")
    ins = _orders_insights_file()
    if ins.exists():
        ins.unlink()


def import_orders_csv(raw: bytes) -> dict:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV başlık satırı okunamadı.")
    cols = {c.strip().lower() for c in reader.fieldnames}
    missing = REQUIRED_COLS - cols
    if missing:
        raise ValueError(
            "CSV'de eksik kolonlar var: " + ", ".join(sorted(missing)) +
            ". Beklenen kolonlar: order_id, product_name, sku, quantity, unit_price, "
            "cost, commission_rate, order_date, city, status, platform"
        )
    orders = load_orders()
    seen = {(o["order_id"], o.get("sku", ""), o.get("product_name", "")) for o in orders}
    added = skipped = 0
    for row in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not r.get("order_id") or not r.get("product_name"):
            skipped += 1
            continue
        key = (r["order_id"], r.get("sku", ""), r["product_name"])
        if key in seen:
            skipped += 1
            continue
        try:
            qty = int(float(r.get("quantity") or 1))
            price = float(r.get("unit_price") or 0)
            cost = float(r.get("cost") or 0)
            commission = float(r.get("commission_rate") or 0)
        except ValueError:
            skipped += 1
            continue
        orders.append({
            "order_id": r["order_id"],
            "product_name": r["product_name"],
            "sku": r.get("sku", ""),
            "quantity": max(qty, 1),
            "unit_price": price,
            "cost": cost,
            "commission_rate": commission,
            "order_date": r.get("order_date", "")[:10],
            "city": r.get("city", ""),
            "status": (r.get("status") or "delivered").lower(),
            "platform": (r.get("platform") or "").lower(),
        })
        seen.add(key)
        added += 1
    save_orders(orders)
    return {"added": added, "skipped": skipped, "total": len(orders)}


def clear_orders() -> None:
    for f in (_orders_file(), _orders_insights_file()):
        if f.exists():
            f.unlink()


def compute_stats() -> dict:
    orders = load_orders()
    if not orders:
        return {"has_data": False, "total_orders": 0}

    sold = [o for o in orders if o["status"] not in ("canceled", "returned")]
    returned = [o for o in orders if o["status"] == "returned"]

    def line_revenue(o):
        return o["quantity"] * o["unit_price"]

    def line_profit(o):
        rate = o["commission_rate"]
        if rate <= 0:
            fraction = 0
        elif rate < 1:
            fraction = rate
        else:
            fraction = rate / 100
        commission = line_revenue(o) * fraction
        return line_revenue(o) - commission - o["quantity"] * o["cost"]

    revenue = sum(line_revenue(o) for o in sold)
    profit = sum(line_profit(o) for o in sold)
    units = sum(o["quantity"] for o in sold)
    order_ids = {o["order_id"] for o in sold}
    has_cost = any(o["cost"] > 0 or o["commission_rate"] > 0 for o in sold)

    daily: dict[str, dict] = defaultdict(lambda: {"revenue": 0.0, "orders": set(), "units": 0})
    for o in sold:
        d = daily[o["order_date"] or "?"]
        d["revenue"] += line_revenue(o)
        d["orders"].add(o["order_id"])
        d["units"] += o["quantity"]
    timeseries = [
        {"date": k, "revenue": round(v["revenue"], 2), "orders": len(v["orders"]), "units": v["units"]}
        for k, v in sorted(daily.items()) if k != "?"
    ]

    by_product: dict[str, dict] = defaultdict(lambda: {"revenue": 0.0, "profit": 0.0, "units": 0, "returns": 0})
    for o in sold:
        p = by_product[o["product_name"]]
        p["revenue"] += line_revenue(o)
        p["profit"] += line_profit(o)
        p["units"] += o["quantity"]
        p["sku"] = o.get("sku", "")
    for o in returned:
        by_product[o["product_name"]]["returns"] += o["quantity"]
    top_products = sorted(
        [{"product_name": k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}}
         for k, v in by_product.items()],
        key=lambda x: -x["revenue"],
    )[:15]

    by_city: dict[str, float] = defaultdict(float)
    for o in sold:
        if o["city"]:
            by_city[o["city"].title()] += line_revenue(o)
    top_cities = sorted(
        [{"city": k, "revenue": round(v, 2)} for k, v in by_city.items()],
        key=lambda x: -x["revenue"],
    )[:10]

    by_platform: dict[str, float] = defaultdict(float)
    for o in sold:
        by_platform[o["platform"] or "bilinmiyor"] += line_revenue(o)

    return {
        "has_data": True,
        "total_orders": len(order_ids),
        "total_units": units,
        "revenue": round(revenue, 2),
        "profit": round(profit, 2) if has_cost else None,
        "margin_pct": round(profit / revenue * 100, 1) if has_cost and revenue else None,
        "avg_order_value": round(revenue / len(order_ids), 2) if order_ids else 0,
        "returned_units": sum(o["quantity"] for o in returned),
        "return_rate_pct": round(
            sum(o["quantity"] for o in returned) / (units + sum(o["quantity"] for o in returned)) * 100, 1
        ) if (units + sum(o["quantity"] for o in returned)) else 0,
        "timeseries": timeseries,
        "top_products": top_products,
        "top_cities": top_cities,
        "by_platform": [{"platform": k, "revenue": round(v, 2)} for k, v in sorted(by_platform.items(), key=lambda x: -x[1])],
    }


ORDERS_INSIGHTS_SYSTEM = """Sen Türkçe konuşan bir e-ticaret satış danışmanısın.
Sana bir mağazanın sipariş istatistikleri verilecek. Şu JSON formatında yanıt ver:
{
 "summary": "2-3 cümlelik genel değerlendirme",
 "highlights": ["öne çıkan 3-5 olumlu bulgu"],
 "risks": ["dikkat edilmesi gereken 2-4 risk"],
 "actions": ["satışları/kârı artırmak için 3-6 somut aksiyon önerisi"]
}
Sayılara dayan, genel geçer laf üretme. Kâr marjı düşükse veya iade oranı yüksekse mutlaka değin."""


def generate_orders_insights(force: bool = False) -> dict:
    ins_file = _orders_insights_file()
    if ins_file.exists() and not force:
        return json.loads(ins_file.read_text(encoding="utf-8"))
    stats = compute_stats()
    if not stats.get("has_data"):
        raise HTTPException(400, "Önce sipariş verisi yükle (CSV).")
    if not ai_seller.ai_available():
        raise HTTPException(503, "AI içgörüleri için GEMINI_API_KEY gerekli.")
    compact = {k: v for k, v in stats.items() if k != "timeseries"}
    compact["last_30_days"] = stats["timeseries"][-30:]
    result = ai_seller._chat_json(ORDERS_INSIGHTS_SYSTEM, json.dumps(compact, ensure_ascii=False))
    result["generated_from"] = {"orders": stats["total_orders"], "revenue": stats["revenue"]}
    ins_file.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ---- API uçları ----

def _require_feature(request: Request) -> dict:
    from . import auth, billing

    user = auth.get_session_user(request)
    if not user:
        raise HTTPException(401, "Bu işlem için giriş yapmalısın.")
    billing.check_feature(user["id"], "analytics")
    return user


@router.post("/import-orders")
async def import_orders(file: UploadFile, request: Request):
    _require_feature(request)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Lütfen .csv uzantılı bir dosya yükleyin.")
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "CSV 10 MB'dan küçük olmalı.")
    try:
        return import_orders_csv(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/stats")
def stats(request: Request):
    _require_feature(request)
    return compute_stats()


@router.get("/insights")
def insights(request: Request, refresh: bool = False):
    _require_feature(request)
    return generate_orders_insights(force=refresh)


@router.delete("/orders")
def delete_orders(request: Request):
    _require_feature(request)
    clear_orders()
    return {"ok": True}
