"""Gemini tabanlı analiz katmanı: fotoğraf analizi, fiyat araştırması,
kategori eşleştirme ve özellik doldurma.

Ücretsiz katman notu: varsayılan model gemini-2.5-flash (10 istek/dk, 1500 istek/gün).
GEMINI_MODEL env değişkeniyle daha yeni bir Flash modeline geçebilirsin.
Fiyat araştırması ücretsiz DuckDuckGo araması (ddgs) ile yapılır.

Görsel üretimi maliyet optimizasyonu:
- Hibrit mimari: ucuz metin modeli (TEXT_MODEL) prompt yazar, görsel modeli (IMAGE_MODEL) üretir.
- Context caching: referans fotoğraf oturum boyunca cache'lenir, 4 sahne için tekrar gönderilmez.
- Batch mod: GEMINI_IMAGE_BATCH=true ile %50 indirimli toplu üretim.
- Çözünürlük: Trendyol/HB standardı 1200×1800 (2:3), gereksiz yüksek çözünürlük üretilmez.
"""
import base64
import difflib
import io
import json
import os
import re
import time
from typing import Optional

from ddgs import DDGS
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field

from .models import AttributeFill, CategoryChoice, PriceSuggestion, ProductAnalysis, ScenePlan

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Ucuz metin modeli — sahne planlama ve prompt zenginleştirme (~$0.10/M token)
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash-lite")
# Görsel modeli — referans fotoğrafı koruyarak sahne üretir (Imagen text-only değil)
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
IMAGE_ASPECT_RATIO = os.getenv("GEMINI_IMAGE_ASPECT_RATIO", "2:3")  # Trendyol 1200×1800
IMAGE_OUTPUT_SIZE = (
    int(os.getenv("GEMINI_IMAGE_OUTPUT_WIDTH", "1200")),
    int(os.getenv("GEMINI_IMAGE_OUTPUT_HEIGHT", "1800")),
)
IMAGE_BATCH_ENABLED = os.getenv("GEMINI_IMAGE_BATCH", "true").lower() in ("1", "true", "yes")
IMAGE_CACHE_ENABLED = os.getenv("GEMINI_IMAGE_CACHE", "true").lower() in ("1", "true", "yes")
IMAGE_CACHE_TTL = os.getenv("GEMINI_IMAGE_CACHE_TTL", "3600s")
IMAGE_BATCH_POLL_SEC = float(os.getenv("GEMINI_IMAGE_BATCH_POLL_SEC", "5"))
IMAGE_BATCH_TIMEOUT_SEC = float(os.getenv("GEMINI_IMAGE_BATCH_TIMEOUT_SEC", "600"))
# JPEG: Trendyol için yeterli kalite, küçük dosya (~120–250 KB @ 1200×1800)
IMAGE_JPEG_QUALITY = int(os.getenv("GEMINI_IMAGE_JPEG_QUALITY", "72"))
IMAGE_DPI = int(os.getenv("GEMINI_IMAGE_DPI", "96"))
# Referans fotoğrafı API'ye göndermeden küçült — giriş token maliyetini düşürür
IMAGE_REF_MAX_PX = int(os.getenv("GEMINI_IMAGE_REF_MAX_PX", "768"))
# Ana model yoğun (503) ya da kotası dolu (429) ise sırayla denenecek yedekler.
# Her modelin ücretsiz kotası ayrı sayılır.
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash,gemini-2.0-flash").split(",")
    if m.strip()
]

_client: Optional[genai.Client] = None
# Oturum anahtarı → Gemini cached_content adı (referans fotoğraf tekrar gönderilmez)
_image_context_cache: dict[str, str] = {}

_IMAGE_GEN_SYSTEM = (
    "You are an e-commerce product photo editor for Turkish marketplaces (Trendyol, Hepsiburada). "
    "Keep the product in the reference photo EXACTLY unchanged: color, pattern, shape, logos, proportions. "
    "Create only the requested scene around it. Professional listing photo quality with realistic lighting. "
    "Portrait 2:3 framing. Do not add any text, watermark, or logo overlay."
)


def _get_client() -> genai.Client:
    # İstemci ilk kullanımda oluşturulur; böylece anahtar yokken (demo mod)
    # modülün import edilmesi hata vermez.
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    return _client


class _ObservedAttribute(BaseModel):
    name: str = Field(description="Özellik adı (Türkçe)")
    value: str = Field(description="Gözlemlenen değer")


class _GeminiProductAnalysis(ProductAnalysis):
    # Gemini'nin structured output şeması serbest dict (additionalProperties)
    # desteklemez; ad/değer listesi alıp ProductAnalysis'teki dict'e çeviriyoruz.
    observed_attributes: list[_ObservedAttribute] = Field(  # type: ignore[assignment]
        description="Fotoğraftan gözlemlenen diğer özellikler, ad/değer çiftleri."
    )


def _quota_retry_delay(e: Exception) -> Optional[float]:
    """429 kota hatasından Google'ın önerdiği bekleme süresini (saniye) çıkarır."""
    if getattr(e, "code", None) != 429:
        return None
    m = re.search(r"retryDelay'?\"?\s*:\s*'?\"?(\d+(?:\.\d+)?)s", str(e)) \
        or re.search(r"retry in (\d+(?:\.\d+)?)", str(e))
    return float(m.group(1)) if m else None


def _call_gemini(**kwargs):
    """Tek modele Gemini çağrısı; geçici hatalarda kendi içinde yeniden dener.

    - 429 (dakikalık kota): Google'ın önerdiği süre kısaysa bekleyip tekrar dener.
    - 500/503 (aşırı yük): artan bekleme ile 2 kez daha dener.
    """
    last: Exception = RuntimeError("Gemini çağrısı yapılamadı")
    for attempt in range(3):
        try:
            return _get_client().models.generate_content(**kwargs)
        except genai_errors.APIError as e:
            last = e
            code = getattr(e, "code", None)
            if code == 429 and attempt < 2:
                delay = _quota_retry_delay(e)
                if delay is not None and delay <= 35:
                    time.sleep(delay + 1)
                    continue
                raise
            if code in (500, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise last


def _generate(schema: type[BaseModel], contents, system: Optional[str] = None, model: Optional[str] = None):
    """Yapılandırılmış JSON çıktı ile Gemini çağrısı; gerekirse yedek modellere düşer."""
    primary = model or MODEL
    models_to_try = [primary] + [m for m in FALLBACK_MODELS if m != primary]
    response = None
    for i, model in enumerate(models_to_try):
        try:
            response = _call_gemini(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            break
        except genai_errors.APIError as e:
            # Yoğunluk/kota hatasında sıradaki modele geç; başka hataysa yükselt
            if getattr(e, "code", None) in (429, 500, 503) and i < len(models_to_try) - 1:
                continue
            raise
    if response.parsed is not None:
        return response.parsed
    return schema.model_validate_json(response.text)


def analyze_image(image_b64: str, media_type: str) -> ProductAnalysis:
    """Tek fotoğraf — geriye dönük uyumluluk."""
    return analyze_images([(image_b64, media_type)])


def analyze_images(images: list[tuple[str, str]]) -> ProductAnalysis:
    """Ürün fotoğraflarını (1–6) analiz edip yapılandırılmış ürün bilgisi çıkarır."""
    if not images:
        raise ValueError("En az bir fotoğraf gerekli.")
    parts: list = []
    for image_b64, media_type in images:
        parts.append(types.Part.from_bytes(data=base64.b64decode(image_b64), mime_type=media_type))
    count = len(images)
    parts.append(
        f"Bu ürün için {count} fotoğraf verildi. Tüm açıları birlikte incele — etiket, kutu, "
        "detay, renk ve ölçü ipuçlarını fotoğraflar arasında birleştir. "
        "Ne kadar çok açı varsa o kadar eksiksiz analiz et."
    )
    raw: _GeminiProductAnalysis = _generate(
        _GeminiProductAnalysis,
        contents=parts,
        system=(
            "Sen bir Trendyol e-ticaret listeleme uzmanısın. Ürün fotoğraflarını analiz edip "
            "satışa hazır, SEO uyumlu Türkçe ilan içeriği üretirsin. Kurallar:\n"
            "1. Tüm fotoğraflardaki metinleri oku (etiket, kutu, ekran, logo, model kodu).\n"
            "2. Ürün TİPİNE göre alıcının aradığı kritik özellikleri çıkar:\n"
            "   - Elektronik/TV: marka, model, ekran boyutu (inç), panel tipi (OLED/QLED/LED), çözünürlük, bağlantılar\n"
            "   - Giyim: kumaş, kesim, yaka tipi, desen, mevsim (beden fotoğraftan bilinemez, uydurma)\n"
            "   - Mobilya/dekor: malzeme, yaklaşık ölçüler, stil\n"
            "   - Kozmetik: hacim/gramaj, cilt tipi, içerik\n"
            "   Ürün tipine uymayan alanı EKLEME (tişört için inç yazma, TV için kumaş yazma).\n"
            "3. Ölçü/boyutu fotoğraftan ancak tahmin edebiliyorsan 'yaklaşık' diye belirt; "
            "emin olamadığın hiçbir özelliği uydurma.\n"
            "4. Başlık: Marka + model + ürün tipi + en ayırt edici 2-3 özellik (arama dostu).\n"
            "5. description_html SADECE şu etiketleri kullansın: <p>, <ul>, <li>, <strong>, <br>. "
            "Markdown, class, style KULLANMA. Yapı: kısa tanıtım paragrafı + 'Öne Çıkan Özellikler' "
            "listesi + (teknik ürünse) 'Teknik Özellikler' listesi.\n"
            "6. observed_attributes içine fotoğraflardan okunabilen TÜM teknik detayları ad/değer "
            "çifti olarak koy — bunlar Trendyol kategori özelliklerini doldurmak için kullanılacak."
        ),
    )
    data = raw.model_dump()
    data["observed_attributes"] = {a.name: a.value for a in raw.observed_attributes}
    return ProductAnalysis(**data)


def _search_price_snippets(analysis: ProductAnalysis) -> list[str]:
    """DuckDuckGo ile benzer ürünlerin fiyat bilgilerini toplar (ücretsiz, anahtarsız)."""
    queries = [f"{analysis.title} fiyat"]
    if analysis.brand_guess and analysis.brand_guess.lower() != "bilinmiyor":
        queries.append(f"{analysis.brand_guess} {analysis.product_type} fiyat")
    queries.append(f"{analysis.product_type} fiyat trendyol hepsiburada")

    snippets: list[str] = []
    seen: set[str] = set()
    for query in queries:
        try:
            results = DDGS().text(query, region="tr-tr", max_results=6)
        except Exception:
            continue  # arama servisi geçici olarak yanıt vermezse diğer sorguya geç
        for r in results or []:
            url = r.get("href", "")
            if url in seen:
                continue
            seen.add(url)
            snippets.append(f"- {r.get('title', '')} | {r.get('body', '')} ({url})")
    return snippets[:20]


def research_price(analysis: ProductAnalysis) -> PriceSuggestion:
    """Web araması ile benzer ürün fiyatlarını araştırıp fiyat önerir."""
    snippets = _search_price_snippets(analysis)
    if not snippets:
        # Web araması başarısız olursa fotoğraf analizindeki tahmine düş
        mid = (analysis.estimated_price_min_try + analysis.estimated_price_max_try) / 2
        return PriceSuggestion(
            suggested_sale_price=round(mid, 2),
            suggested_list_price=round(mid * 1.2, 2),
            rationale="Web araştırması sonuç vermedi; fotoğraf analizine dayalı tahmin.",
            competitor_examples=[],
        )

    prompt = (
        f"Şu ürün için Türkiye pazarında rekabetçi bir fiyat belirle:\n"
        f"Ürün: {analysis.title}\nTip: {analysis.product_type}\nMarka: {analysis.brand_guess}\n"
        f"Fotoğraf analizine göre tahmini aralık: "
        f"{analysis.estimated_price_min_try}-{analysis.estimated_price_max_try} TL\n\n"
        f"Aşağıda web aramasından gelen benzer ürün sonuçları var. Bunlardan fiyat içerenleri "
        f"değerlendir (kargo/aksesuar gibi alakasız sonuçları ele). Rekabetçi bir satış fiyatı ve "
        f"ondan %15-30 yüksek bir liste fiyatı öner. Gerekçeyi Türkçe yaz ve kullandığın "
        f"örnekleri 'ürün - fiyat' formatında listele.\n\n"
        f"Arama sonuçları:\n" + "\n".join(snippets)
    )
    return _generate(PriceSuggestion, contents=prompt)


def _shortlist_categories(analysis: ProductAnalysis, categories: list[dict], limit: int = 150) -> list[dict]:
    """Binlerce kategoriyi Gemini'ye göndermemek için anahtar kelimeyle ön eleme."""
    if len(categories) <= limit:
        return categories
    terms = [t.lower() for t in analysis.category_keywords + [analysis.product_type]]
    # Gözlemlenen özellik değerleri de kategori adında geçebilir (örn. "OLED", "Bluetooth")
    terms += [v.lower() for v in list(analysis.observed_attributes.values())[:10] if len(v) > 2]
    scored = []
    for cat in categories:
        name = cat["name"].lower()
        score = sum(1.0 for t in terms if t in name)
        score += max(difflib.SequenceMatcher(None, t, name).ratio() for t in terms)
        scored.append((score, cat))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]


def match_category(analysis: ProductAnalysis, categories: list[dict]) -> CategoryChoice:
    candidates = _shortlist_categories(analysis, categories)
    cat_list = "\n".join(f'{c["id"]}: {c["name"]}' for c in candidates)
    return _generate(
        CategoryChoice,
        contents=(
            f"Ürün: {analysis.title}\nTip: {analysis.product_type}\n"
            f"Marka: {analysis.brand_guess}\n"
            f"Anahtar kelimeler: {', '.join(analysis.category_keywords)}\n"
            f"Gözlemlenen özellikler: {json.dumps(analysis.observed_attributes, ensure_ascii=False)}\n\n"
            f"Aşağıdaki Trendyol kategorilerinden bu ürüne EN uygun olanı seç. Kurallar:\n"
            f"1. Sadece listedeki id'lerden birini kullan.\n"
            f"2. Ürünün KENDİSİNİN kategorisini seç; aksesuar/yedek parça/kılıf kategorileriyle "
            f"karıştırma (örn. televizyon ≠ televizyon aksesuarı, telefon ≠ telefon kılıfı).\n"
            f"3. Birden fazla aday varsa ürün tipiyle birebir örtüşen en SPESİFİK olanı seç.\n"
            f"4. Listede tam uyan kategori yoksa en yakınını seç ve confidence'ı 'low' yap.\n\n{cat_list}"
        ),
    )


def fill_attributes(analysis: ProductAnalysis, category_attributes: list[dict]) -> AttributeFill:
    """Kategori özelliklerini ürün analizine göre doldurur."""
    if not category_attributes:
        return AttributeFill(attributes=[])

    # Gemini'ye sade bir özet gönder (değer listeleri çok uzun olabilir)
    compact = []
    for ca in category_attributes:
        values = ca.get("attributeValues") or []
        compact.append({
            "attributeId": ca["attribute"]["id"],
            "name": ca["attribute"]["name"],
            "required": ca.get("required", False),
            "allowCustom": ca.get("allowCustom", False),
            "values": [{"id": v["id"], "name": v["name"]} for v in values[:120]],
        })

    return _generate(
        AttributeFill,
        contents=(
            f"Ürün bilgisi:\n"
            f"- Başlık: {analysis.title}\n"
            f"- Tip: {analysis.product_type}\n"
            f"- Renk: {analysis.color}\n- Materyal: {analysis.material}\n"
            f"- Gözlemlenen özellikler: {json.dumps(analysis.observed_attributes, ensure_ascii=False)}\n\n"
            f"Aşağıdaki Trendyol kategori özelliklerini doldur. Kurallar:\n"
            f"1. required=true olanları MUTLAKA doldur.\n"
            f"2. Değer listesi varsa listeden en uygun id'yi seç (attribute_value_id).\n"
            f"3. Liste boş ve allowCustom=true ise custom_value olarak serbest metin yaz.\n"
            f"4. Emin olmadığın opsiyonel özellikleri atla.\n"
            f"5. Ürün tipine mantıken uymayan değerleri seçme.\n\n"
            f"Özellikler:\n{json.dumps(compact, ensure_ascii=False)}"
        ),
    )


# ---- Satış görseli üretimi (maliyet optimize) ----

class _EnglishPrompt(BaseModel):
    prompt: str = Field(description="Detailed English image generation prompt for the scene")


def _analysis_summary(analysis: ProductAnalysis) -> str:
    attrs = json.dumps(analysis.observed_attributes, ensure_ascii=False)
    return (
        f"Product: {analysis.title}\nType: {analysis.product_type}\n"
        f"Brand: {analysis.brand_guess}\nColor: {analysis.color} | Material: {analysis.material}\n"
        f"Attributes: {attrs}"
    )


def _prepare_reference_image(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Referans fotoğrafı API/cache için küçültür — ürün tanınır, token maliyeti düşer."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        w, h = img.size
        if max(w, h) <= IMAGE_REF_MAX_PX:
            return image_bytes, media_type
        copy = img.copy()
        copy.thumbnail((IMAGE_REF_MAX_PX, IMAGE_REF_MAX_PX), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        copy.convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue(), "image/jpeg"


def _image_generation_config(cached_content: Optional[str] = None) -> types.GenerateContentConfig:
    cfg = types.GenerateContentConfig(
        system_instruction=_IMAGE_GEN_SYSTEM,
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=IMAGE_ASPECT_RATIO),
    )
    if cached_content:
        cfg.cached_content = cached_content
    return cfg


def _image_batch_config() -> types.GenerateContentConfig:
    """Batch API: media_resolution ve cached_content desteklenmez — ayrı config gerekir."""
    return types.GenerateContentConfig(
        system_instruction=_IMAGE_GEN_SYSTEM,
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=IMAGE_ASPECT_RATIO),
    )


def _extract_image_bytes(response) -> bytes:
    for cand in response.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
    raise RuntimeError("Model görsel döndürmedi: " + ((response.text or "").strip()[:200] or "boş yanıt"))


def _resize_to_marketplace(image_bytes: bytes) -> bytes:
    """Trendyol standardı: 1200×1800 px, 96 DPI, JPEG — pazaryeri + küçük dosya."""
    target_w, target_h = IMAGE_OUTPUT_SIZE
    with Image.open(io.BytesIO(image_bytes)) as img:
        rgb = img.convert("RGB")
        # Model 2K+ döndürürse önce küçült — gereksiz upscaling yapma
        if rgb.width > target_w or rgb.height > target_h:
            rgb.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        if rgb.size != (target_w, target_h):
            rgb = rgb.resize((target_w, target_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        rgb.save(
            buf,
            format="JPEG",
            quality=IMAGE_JPEG_QUALITY,
            optimize=True,
            progressive=True,
            subsampling=2,
            dpi=(IMAGE_DPI, IMAGE_DPI),
        )
        return buf.getvalue()


def _ensure_image_cache(session_key: str, image_bytes: bytes, media_type: str, analysis: ProductAnalysis) -> Optional[str]:
    """Referans fotoğraf + ürün bilgisini Gemini context cache'e yazar; sonraki sahnelerde tekrar kullanılır."""
    if not IMAGE_CACHE_ENABLED:
        return None
    if session_key in _image_context_cache:
        return _image_context_cache[session_key]
    ref_bytes, ref_mime = _prepare_reference_image(image_bytes, media_type)
    try:
        cache = _get_client().caches.create(
            model=IMAGE_MODEL,
            config=types.CreateCachedContentConfig(
                display_name=f"product-{session_key[:16]}",
                contents=[
                    types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime),
                    f"Reference product photo. {_analysis_summary(analysis)}",
                ],
                system_instruction=_IMAGE_GEN_SYSTEM,
                ttl=IMAGE_CACHE_TTL,
            ),
        )
        if cache.name:
            _image_context_cache[session_key] = cache.name
            return cache.name
    except genai_errors.APIError:
        pass  # cache desteklenmiyorsa normal modda devam et
    return None


def _refine_scene_prompt(analysis: ProductAnalysis, scene_label: str, scene_hint: str) -> str:
    """Ucuz metin modeliyle sahne tarifini görsel modele uygun İngilizce prompta çevirir."""
    result: _EnglishPrompt = _generate(
        _EnglishPrompt,
        model=TEXT_MODEL,
        contents=(
            f"{_analysis_summary(analysis)}\n\n"
            f"Scene label (Turkish): {scene_label}\nScene concept: {scene_hint}\n\n"
            "Write a detailed English image generation prompt for this e-commerce product photo scene. "
            "Include: composition, lighting, background, camera angle. If a person/hand is needed, describe pose. "
            "The reference product photo will be provided separately — do NOT describe changing the product itself. "
            "Keep prompt under 120 words."
        ),
        system="You write concise, precise English prompts for AI product photography. Output JSON only.",
    )
    return result.prompt.strip()


def plan_scenes(analysis: ProductAnalysis) -> ScenePlan:
    """Ürün tipine uygun 4 satış görseli sahnesi tasarlar (ucuz metin modeli, ~₺0.01)."""
    return _generate(
        ScenePlan,
        model=TEXT_MODEL,
        contents=(
            f"{_analysis_summary(analysis)}\n\n"
            "Design 4 DIFFERENT e-commerce product photo scenes. Rules:\n"
            "1. Scene 1: clean white/neutral studio — default listing image.\n"
            "2. Wearables: include model shot. Handheld: in-use shot. Large items: in room.\n"
            "3. Remaining: lifestyle, detail close-up, or alternate angle.\n"
            "4. Each 'label': 2-4 words Turkish. Each 'prompt': concise English (max 80 words)."
        ),
        system="You are an e-commerce visual merchandiser. Output JSON only.",
    )


def generate_marketing_image(
    image_bytes: bytes,
    media_type: str,
    scene_prompt: str,
    *,
    analysis: Optional[ProductAnalysis] = None,
    scene_label: str = "",
    session_key: Optional[str] = None,
) -> bytes:
    """Referans ürün fotoğrafından, verilen sahneye göre satış görseli üretir."""
    prompt = scene_prompt.strip()
    # Eski oturumlarda Türkçe kalmış promptları ucuz metin modeliyle İngilizceye çevir
    if analysis and _looks_turkish(prompt):
        prompt = _refine_scene_prompt(analysis, scene_label or "Sahne", prompt)

    cached = _ensure_image_cache(session_key, image_bytes, media_type, analysis) if session_key and analysis else None
    contents: list = [f"Scene: {prompt}"]  # kısa prompt — çıkış token'ı değişmez, giriş ucuz kalır
    if not cached:
        ref_bytes, ref_mime = _prepare_reference_image(image_bytes, media_type)
        contents = [types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime), *contents]

    response = _call_gemini(
        model=IMAGE_MODEL,
        contents=contents,
        config=_image_generation_config(cached),
    )
    return _resize_to_marketplace(_extract_image_bytes(response))


def _looks_turkish(text: str) -> bool:
    return bool(re.search(r"[çğıöşüÇĞİÖŞÜ]", text))


def _batch_wait(job: types.BatchJob) -> types.BatchJob:
    """Batch job tamamlanana kadar bekler."""
    deadline = time.time() + IMAGE_BATCH_TIMEOUT_SEC
    while not job.done:
        if time.time() > deadline:
            raise TimeoutError(f"Batch job zaman aşımı ({int(IMAGE_BATCH_TIMEOUT_SEC)} sn)")
        time.sleep(IMAGE_BATCH_POLL_SEC)
        job = _get_client().batches.get(name=job.name)
    if job.state and job.state.name in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED"):
        err = job.error.message if job.error else "Bilinmeyen batch hatası"
        raise RuntimeError(f"Batch job başarısız: {err}")
    return job


def _prepare_scene_prompt(analysis: ProductAnalysis, scene) -> str:
    prompt = scene.prompt.strip()
    if _looks_turkish(prompt):
        prompt = _refine_scene_prompt(analysis, scene.label, prompt)
    return prompt


def generate_marketing_images_batch(
    image_bytes: bytes,
    media_type: str,
    scenes: list,
    *,
    analysis: ProductAnalysis,
    session_key: str,
) -> list[bytes]:
    """Seçili sahneleri Batch API ile üretir; başarısız olanlar tek tek yeniden denenir."""
    ref_bytes, ref_mime = _prepare_reference_image(image_bytes, media_type)
    prepared = [(sc, _prepare_scene_prompt(analysis, sc)) for sc in scenes]

    if IMAGE_BATCH_ENABLED:
        try:
            requests = [
                types.InlinedRequest(
                    contents=[
                        types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime),
                        f"Scene: {prompt}",
                    ],
                    config=_image_batch_config(),
                )
                for _, prompt in prepared
            ]
            job = _get_client().batches.create(
                model=IMAGE_MODEL,
                src=types.BatchJobSource(inlined_requests=requests),
            )
            job = _batch_wait(job)
            dest = job.dest
            if dest and dest.inlined_responses:
                images: list[Optional[bytes]] = [None] * len(prepared)
                failed: list[int] = []
                for i, item in enumerate(dest.inlined_responses):
                    if item.error or not item.response:
                        failed.append(i)
                    else:
                        try:
                            images[i] = _resize_to_marketplace(_extract_image_bytes(item.response))
                        except RuntimeError:
                            failed.append(i)
                for i in failed:
                    sc, prompt = prepared[i]
                    images[i] = generate_marketing_image(
                        image_bytes, media_type, prompt,
                        analysis=analysis, scene_label=sc.label, session_key=session_key,
                    )
                return images  # type: ignore[return-value]
        except Exception:
            pass  # tamamen başarısızsa sırayla üret

    return [
        generate_marketing_image(
            image_bytes, media_type, prompt,
            analysis=analysis, scene_label=sc.label, session_key=session_key,
        )
        for sc, prompt in prepared
    ]


def image_batch_enabled() -> bool:
    return IMAGE_BATCH_ENABLED
