"""FastAPI uygulaması — web arayüzü + analiz ve yayınlama uçları."""
import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel

load_dotenv()

from . import ai_agent  # noqa: E402  (load_dotenv önce çalışmalı)
from . import ai_seller  # noqa: E402
from .models import (  # noqa: E402
    GenerateImageRequest,
    ProductAnalysis,
    ProductDraft,
    PublishResult,
    RecategorizeRequest,
    SceneRequest,
    SceneSpec,
)
from .trendyol_client import TrendyolClient  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="TrendolAi — Trendyol Satış Paneli")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

trendyol = TrendyolClient(
    seller_id=os.getenv("TRENDYOL_SELLER_ID", ""),
    api_key=os.getenv("TRENDYOL_API_KEY", ""),
    api_secret=os.getenv("TRENDYOL_API_SECRET", ""),
    env=os.getenv("TRENDYOL_ENV", "stage"),
)

ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
SUFFIX_TO_MIME = {v: k for k, v in ALLOWED_TYPES.items()}

# Analiz sonuçları görsel üretimi / kategori değişikliği için kullanılır.
# Bellekte tutulur + diske yazılır (uploads/<foto>.meta.json), böylece sunucu
# yeniden başlasa bile (--reload) oturum kaybolmaz.
SESSIONS: dict[str, dict] = {}


def _safe_upload(filename: str) -> Path:
    """İstemciden gelen dosya adının uploads/ dışına çıkmasını engeller."""
    p = (UPLOAD_DIR / filename).resolve()
    if p.parent != UPLOAD_DIR.resolve():
        raise HTTPException(400, "Geçersiz dosya adı.")
    return p


def _meta_path(filename: str) -> Path:
    return _safe_upload(f"{filename}.meta.json")


def _save_session(filename: str) -> None:
    s = SESSIONS.get(filename)
    if not s:
        return
    data = {"analysis": s["analysis"].model_dump()}
    if "scenes" in s:
        data["scenes"] = [sc.model_dump() for sc in s["scenes"]]
    _meta_path(filename).write_text(json.dumps(data, ensure_ascii=False))


def _get_session(filename: str) -> Optional[dict]:
    """Önce bellekten, yoksa diskten (sunucu yeniden başlamışsa) oturumu getirir."""
    s = SESSIONS.get(filename)
    if s:
        return s
    meta = _meta_path(filename)
    if not meta.exists():
        return None
    data = json.loads(meta.read_text())
    s = {"analysis": ProductAnalysis(**data["analysis"])}
    if data.get("scenes"):
        s["scenes"] = [SceneSpec(**sc) for sc in data["scenes"]]
    SESSIONS[filename] = s
    return s


def _ai_available() -> bool:
    """Gemini API anahtarı var mı? Yoksa analiz adımı demo veriyle çalışır."""
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


@app.exception_handler(genai_errors.APIError)
async def gemini_error_handler(request: Request, exc: genai_errors.APIError):
    """Gemini hatalarını kullanıcı diline çevirir — arayüz her zaman JSON alır."""
    if getattr(exc, "code", None) == 429:
        msg = str(exc)
        detail = (
            "Gemini ücretsiz API kotası doldu. Dakikalık limite takıldıysan ~1 dakika sonra "
            "tekrar dene; günlük limit dolduysa kota ertesi gün sıfırlanır. "
            "Alternatif: .env dosyasında GEMINI_MODEL=gemini-2.5-flash-lite yazarak daha "
            "yüksek ücretsiz kotalı modele geçebilirsin."
        )
        if "PerDay" in msg:
            detail = "Gemini ücretsiz GÜNLÜK kotası doldu — kota ertesi gün sıfırlanır. " \
                     ".env'de GEMINI_MODEL=gemini-2.5-flash-lite ile daha yüksek kotalı modele geçebilirsin."
        return JSONResponse(status_code=429, content={"detail": detail})
    if getattr(exc, "code", None) == 503 or "UNAVAILABLE" in str(exc):
        return JSONResponse(status_code=503, content={
            "detail": "Gemini şu an aşırı yüklü — otomatik yeniden denemeler ve yedek modeller de "
                      "yanıt vermedi. Bu genelde geçicidir; 1-2 dakika sonra tekrar dene."
        })
    return JSONResponse(status_code=502, content={"detail": f"Gemini API hatası: {str(exc)[:300]}"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Beklenmedik hatalarda bile arayüze düz metin yerine JSON döndür."""
    return JSONResponse(
        status_code=500,
        content={"detail": f"Sunucu hatası: {type(exc).__name__}: {str(exc)[:300]}"},
    )


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/panel")
def panel():
    return FileResponse(BASE_DIR / "static" / "panel.html")


@app.get("/api/status")
def status():
    return {
        "trendyol_mode": trendyol.mode_name,
        "ai_mode": "live" if _ai_available() else "demo",
        "imgbb_configured": bool(os.getenv("IMGBB_API_KEY")),
    }


@app.post("/api/analyze")
async def analyze(file: UploadFile):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Sadece JPEG, PNG veya WebP fotoğraf yükleyebilirsin.")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(400, "Fotoğraf 8 MB'dan küçük olmalı.")

    filename = f"{uuid.uuid4().hex}{ALLOWED_TYPES[file.content_type]}"
    (UPLOAD_DIR / filename).write_bytes(raw)
    image_b64 = base64.standard_b64encode(raw).decode()

    if not _ai_available():
        return _demo_analysis(filename)

    # 1) Fotoğraf analizi
    analysis = ai_agent.analyze_image(image_b64, file.content_type)
    SESSIONS[filename] = {"analysis": analysis}
    _save_session(filename)
    # 2) Fiyat araştırması (web araması)
    price = ai_agent.research_price(analysis)
    # 3) Kategori eşleştirme
    categories = trendyol.get_leaf_categories()
    category = ai_agent.match_category(analysis, categories)
    # 4) Kategori özelliklerini doldur
    cat_attrs = trendyol.get_category_attributes(category.category_id)
    filled = ai_agent.fill_attributes(analysis, cat_attrs)

    draft = ProductDraft(
        image_filename=filename,
        title=analysis.title[:100],
        description_html=analysis.description_html,
        brand=analysis.brand_guess,
        category_id=category.category_id,
        category_name=category.category_name,
        category_confidence=category.confidence,
        attributes=[a.model_dump() for a in filled.attributes],
        sale_price=price.suggested_sale_price,
        list_price=max(price.suggested_list_price, price.suggested_sale_price),
        price_rationale=price.rationale,
        barcode=f"TYA{uuid.uuid4().hex[:10].upper()}",
        stock_code=f"STK-{uuid.uuid4().hex[:8].upper()}",
    )
    return {
        "draft": draft.model_dump(),
        "price_examples": price.competitor_examples,
        "category_reasoning": category.reasoning,
        "estimated_range": [analysis.estimated_price_min_try, analysis.estimated_price_max_try],
    }


@app.get("/api/categories")
def list_categories():
    """Arayüzdeki kategori arama kutusu için yaprak kategori listesi."""
    return trendyol.get_leaf_categories()


@app.post("/api/recategorize")
def recategorize(req: RecategorizeRequest):
    """Kullanıcı kategoriyi elle değiştirince yeni kategorinin özelliklerini doldurur."""
    categories = trendyol.get_leaf_categories()
    cat = next((c for c in categories if c["id"] == req.category_id), None)
    if cat is None:
        raise HTTPException(404, "Kategori bulunamadı.")

    cat_attrs = trendyol.get_category_attributes(req.category_id)
    session = _get_session(req.image_filename)
    if _ai_available() and session:
        filled = ai_agent.fill_attributes(session["analysis"], cat_attrs)
        attributes = [a.model_dump() for a in filled.attributes]
    else:
        # Demo mod: ilk değerlerle doldur
        attributes = []
        for ca in cat_attrs:
            values = ca.get("attributeValues") or []
            attributes.append({
                "attribute_id": ca["attribute"]["id"],
                "attribute_name": ca["attribute"]["name"],
                "attribute_value_id": values[0]["id"] if values else None,
                "custom_value": None,
                "value_display": values[0]["name"] if values else "",
            })
    return {"category_id": cat["id"], "category_name": cat["name"], "attributes": attributes}


@app.post("/api/plan-scenes")
def plan_scenes(req: SceneRequest):
    """Ürün tipine uygun 4 satış görseli sahnesi planlar (manken, elde kullanım, açı vb.)."""
    if not _ai_available():
        raise HTTPException(503, "Görsel üretimi için GEMINI_API_KEY gerekli (AI şu an demo modda).")
    session = _get_session(req.image_filename)
    if session is None:
        raise HTTPException(404, "Analiz oturumu bulunamadı — fotoğrafı yeniden analiz et.")
    if "scenes" not in session:
        session["scenes"] = ai_agent.plan_scenes(session["analysis"]).scenes
        _save_session(req.image_filename)
    return {"scenes": [s.model_dump() for s in session["scenes"]]}


@app.post("/api/generate-image")
def generate_image(req: GenerateImageRequest):
    """Planlanan sahnelerden birini üretir; aynı index ile tekrar çağrılırsa yeniden üretir."""
    if not _ai_available():
        raise HTTPException(503, "Görsel üretimi için GEMINI_API_KEY gerekli (AI şu an demo modda).")
    session = _get_session(req.image_filename)
    if session is None:
        raise HTTPException(404, "Analiz oturumu bulunamadı — fotoğrafı yeniden analiz et.")
    if "scenes" not in session:
        # Sahne planı yoksa (örn. sunucu yeniden başladı) otomatik yeniden planla
        session["scenes"] = ai_agent.plan_scenes(session["analysis"]).scenes
        _save_session(req.image_filename)
    scenes = session["scenes"]
    if not (0 <= req.scene_index < len(scenes)):
        raise HTTPException(400, "Geçersiz sahne index'i.")

    src = _safe_upload(req.image_filename)
    if not src.exists():
        raise HTTPException(404, "Kaynak fotoğraf bulunamadı.")
    mime = SUFFIX_TO_MIME.get(src.suffix, "image/jpeg")
    scene = scenes[req.scene_index]
    try:
        image_bytes = ai_agent.generate_marketing_image(src.read_bytes(), mime, scene.prompt)
    except Exception as e:
        raise HTTPException(502, f"Görsel üretilemedi: {str(e)[:300]}")

    filename = f"gen_{uuid.uuid4().hex}.png"
    (UPLOAD_DIR / filename).write_bytes(image_bytes)
    return {"filename": filename, "label": scene.label, "scene_index": req.scene_index}


def _demo_analysis(filename: str) -> dict:
    """GEMINI_API_KEY yokken akışı test etmek için örnek taslak üretir."""
    category = trendyol.get_leaf_categories()[6]  # Kupa & Bardak
    cat_attrs = trendyol.get_category_attributes(category["id"])
    attributes = []
    for ca in cat_attrs:
        values = ca.get("attributeValues") or []
        attributes.append({
            "attribute_id": ca["attribute"]["id"],
            "attribute_name": ca["attribute"]["name"],
            "attribute_value_id": values[0]["id"] if values else None,
            "custom_value": None if values else "Seramik",
            "value_display": values[0]["name"] if values else "Seramik",
        })
    draft = ProductDraft(
        image_filename=filename,
        title="[DEMO] El Yapımı Seramik Kupa 300 ml — Mat Bej",
        description_html=(
            "<p><strong>Bu bir DEMO taslağıdır</strong> — GEMINI_API_KEY eklenince bu içerik "
            "yüklediğin fotoğrafın gerçek AI analiziyle üretilir.</p>"
            "<ul><li>El yapımı seramik, 300 ml</li><li>Bulaşık makinesinde yıkanabilir</li>"
            "<li>Mat bej sır kaplama</li></ul>"
        ),
        brand="Bilinmiyor",
        category_id=category["id"],
        category_name=category["name"],
        category_confidence="demo",
        attributes=attributes,
        sale_price=249.90,
        list_price=329.90,
        price_rationale="DEMO fiyat — gerçek modda benzer ürünler web aramasıyla araştırılır.",
        barcode=f"TYA{uuid.uuid4().hex[:10].upper()}",
        stock_code=f"STK-{uuid.uuid4().hex[:8].upper()}",
    )
    return {
        "draft": draft.model_dump(),
        "price_examples": ["Örnek: benzer seramik kupa — 219 TL", "Örnek: el yapımı kupa — 285 TL"],
        "category_reasoning": "DEMO: Gemini anahtarı olmadığı için örnek kategori seçildi.",
        "estimated_range": [200, 350],
    }


# ---- Mağaza Asistanı (otomasyonv3 entegrasyonu) ----

class AskRequest(BaseModel):
    question: str
    sku: Optional[str] = None


class SettingsRequest(BaseModel):
    platform: Optional[str] = None
    seller_model: Optional[str] = None


class ScrapeRequest(BaseModel):
    url: str


class ReplyRequest(BaseModel):
    product_name: str = ""
    rating: int = 3
    comment_text: str = ""


@app.post("/api/assistant/import-reviews")
async def import_reviews(file: UploadFile):
    """Yorum CSV'si yükler: product_name,sku,rating,comment_text,review_date"""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Lütfen .csv uzantılı bir dosya yükleyin.")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "CSV 5 MB'dan küçük olmalı.")
    try:
        result = ai_seller.import_reviews_csv(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.delete("/api/assistant/reviews")
def delete_reviews():
    ai_seller.clear_reviews()
    return {"ok": True}


@app.get("/api/assistant/stats")
def assistant_stats(platform: Optional[str] = None):
    p = platform or ai_seller.load_settings().get("platform", "trendyol")
    return ai_seller.compute_stats(ai_seller.load_reviews(), p)


@app.get("/api/assistant/products")
def assistant_products(platform: Optional[str] = None):
    """Ürün özetleri — resim, yorum örnekleri (otomasyonv3 product_summary)."""
    return ai_seller.get_product_summaries(platform)


@app.get("/api/assistant/settings")
def get_assistant_settings():
    return ai_seller.load_settings()


@app.post("/api/assistant/settings")
def update_assistant_settings(req: SettingsRequest):
    data = {}
    if req.platform in ("trendyol", "hepsiburada", "n11", "amazon"):
        data["platform"] = req.platform
    if req.seller_model in ("manufacturer", "reseller", None):
        data["seller_model"] = req.seller_model
    return ai_seller.save_settings(data)


@app.delete("/api/assistant/products/{sku}")
def delete_assistant_product(sku: str):
    return ai_seller.delete_product(sku)


@app.get("/api/assistant/insights")
def assistant_insights(refresh: bool = False):
    """Toplu mağaza analizi — tüm yorumlar birlikte değerlendirilir."""
    return ai_seller.generate_store_insights(force=refresh)


@app.post("/api/assistant/ask")
def assistant_ask(req: AskRequest):
    """Mağaza verileri hakkında soru-cevap."""
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "Soru boş olamaz.")
    return ai_seller.answer_store_question(q, req.sku)


@app.post("/api/assistant/suggest-reply")
def assistant_suggest_reply(req: ReplyRequest):
    """Tek müşteri yorumuna mağaza cevabı önerir."""
    if not req.comment_text.strip():
        raise HTTPException(400, "Yorum metni boş olamaz.")
    return ai_seller.suggest_reply_for_review(req.model_dump())


@app.post("/api/assistant/scrape-reviews")
async def assistant_scrape_reviews(req: ScrapeRequest):
    """Ürün linkinden yorumları çeker (Trendyol / Hepsiburada / N11 / Amazon.com.tr)."""
    from .review_scraper import scrape_reviews_from_url

    try:
        ai_seller.scrape_url_matches_store(req.url.strip(), ai_seller.load_settings().get("platform", "trendyol"))
        rows, meta = await scrape_reviews_from_url(req.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Yorumlar çekilemedi: {str(e)[:200]}")
    result = ai_seller.add_scraped_reviews([
        {**r, "review_date": str(r.get("review_date") or "")} for r in rows
    ], meta={k: v for k, v in meta.items() if k != "html"})
    return {**result, "meta": {k: v for k, v in meta.items() if k != "html"}}


@app.get("/api/assistant/presets")
def assistant_presets(product_name: Optional[str] = None):
    """Danışman hazır soruları — gruplu (otomasyonv3 consultantPresets)."""
    return ai_seller.get_consultant_presets(product_name)


# --- İade analizi ---

@app.post("/api/assistant/import-returns")
async def import_returns(file: UploadFile):
    """İade CSV'si: product_name,sku,return_reason,return_date,quantity"""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Lütfen .csv uzantılı bir dosya yükleyin.")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "CSV 5 MB'dan küçük olmalı.")
    try:
        return ai_seller.import_returns_csv(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/assistant/returns")
def delete_returns():
    ai_seller.clear_returns()
    return {"ok": True}


@app.get("/api/assistant/returns-insights")
def returns_insights():
    """İade kayıtlarının AI analizi."""
    return ai_seller.generate_returns_insights()


# --- Kargo takip ---

@app.post("/api/assistant/import-shipments")
async def import_shipments(file: UploadFile):
    """Kargo CSV'si: order_id,product_name,sku,carrier,tracking_number,status,shipped_date,delivered_date"""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Lütfen .csv uzantılı bir dosya yükleyin.")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "CSV 5 MB'dan küçük olmalı.")
    try:
        return ai_seller.import_shipments_csv(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/assistant/shipments")
def delete_shipments():
    ai_seller.clear_shipments()
    return {"ok": True}


@app.get("/api/assistant/shipments")
def list_shipments(status: Optional[str] = None):
    """Kargo listesi + firma bazlı özet. status: shipped|in_transit|delivered|active"""
    return ai_seller.compute_shipments_stats(status)


def _host_image(filename: str) -> str:
    """Trendyol herkese açık HTTPS URL ister. IMGBB_API_KEY varsa oraya yükler."""
    imgbb_key = os.getenv("IMGBB_API_KEY")
    path = _safe_upload(filename)
    if imgbb_key:
        with httpx.Client(timeout=60) as c:
            r = c.post(
                "https://api.imgbb.com/1/upload",
                params={"key": imgbb_key},
                data={"image": base64.standard_b64encode(path.read_bytes()).decode()},
            )
            r.raise_for_status()
            return r.json()["data"]["url"]
    # Host yoksa yerel URL (yalnızca mock modda anlamlı)
    return f"https://localhost-demo/uploads/{filename}"


@app.post("/api/publish", response_model=PublishResult)
def publish(draft: ProductDraft):
    if draft.list_price < draft.sale_price:
        raise HTTPException(400, "Liste fiyatı satış fiyatından düşük olamaz.")
    if not trendyol.mock and not os.getenv("IMGBB_API_KEY"):
        raise HTTPException(
            400,
            "Gerçek modda görsellerin herkese açık HTTPS URL'de olması gerekir. "
            ".env dosyasına IMGBB_API_KEY ekle veya kendi görsel hostingini bağla.",
        )

    brand_id = trendyol.find_brand_id(draft.brand)
    if brand_id is None:
        raise HTTPException(
            400,
            f"'{draft.brand}' markası Trendyol'da bulunamadı. Marka adını düzeltip tekrar dene "
            f"(markanın Trendyol marka listesinde kayıtlı olması gerekir).",
        )

    image_urls = [_host_image(draft.image_filename)]
    for extra in draft.extra_image_filenames:
        if (UPLOAD_DIR / extra).exists():
            image_urls.append(_host_image(extra))

    attributes = []
    for a in draft.attributes:
        entry: dict = {"attributeId": a.attribute_id}
        if a.attribute_value_id is not None:
            entry["attributeValueId"] = a.attribute_value_id
        elif a.custom_value:
            entry["customAttributeValue"] = a.custom_value
        else:
            continue
        attributes.append(entry)

    item = {
        "barcode": draft.barcode,
        "title": draft.title[:100],
        "productMainId": draft.stock_code,
        "brandId": brand_id,
        "categoryId": draft.category_id,
        "quantity": draft.quantity,
        "stockCode": draft.stock_code,
        "description": draft.description_html[:30000],
        "currencyType": draft.currency,
        "listPrice": draft.list_price,
        "salePrice": draft.sale_price,
        "vatRate": draft.vat_rate,
        "images": [{"url": u} for u in image_urls],
        "attributes": attributes,
    }

    try:
        batch_id = trendyol.create_product(item)
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Trendyol API hatası ({e.response.status_code}): {e.response.text[:500]}")

    # Batch sonucunu kısa süre bekle
    batch_status, failures = "PROCESSING", []
    for _ in range(6):
        result = trendyol.get_batch_status(batch_id)
        batch_status = result.get("status", "PROCESSING")
        items = result.get("items", [])
        failures = [r for it in items for r in (it.get("failureReasons") or [])]
        if batch_status in ("COMPLETED", "FAILED"):
            break
        time.sleep(3)

    ok = batch_status == "COMPLETED" and not failures
    msg = (
        "Ürün Trendyol kuyruğuna alındı ve işlendi. Onay sürecinden sonra yayına girer."
        if ok else "Gönderim tamamlanamadı, hata detaylarına bak."
    )
    if trendyol.mock:
        msg = "DEMO MOD: Ürün gerçekten gönderilmedi. API anahtarlarını .env dosyasına ekleyince aynı akış gerçek Trendyol'a gider."
    return PublishResult(
        ok=ok, mode=trendyol.mode_name, batch_request_id=batch_id,
        batch_status=batch_status, failure_reasons=[str(f) for f in failures], message=msg,
    )
