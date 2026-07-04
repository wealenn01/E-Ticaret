"""Gemini tabanlı analiz katmanı: fotoğraf analizi, fiyat araştırması,
kategori eşleştirme ve özellik doldurma.

Ücretsiz katman notu: varsayılan model gemini-2.5-flash (10 istek/dk, 1500 istek/gün).
GEMINI_MODEL env değişkeniyle daha yeni bir Flash modeline geçebilirsin.
Fiyat araştırması ücretsiz DuckDuckGo araması (ddgs) ile yapılır.
"""
import base64
import difflib
import json
import os
import re
import time
from typing import Optional

from ddgs import DDGS
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

from .models import AttributeFill, CategoryChoice, PriceSuggestion, ProductAnalysis, ScenePlan

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
# Ana model yoğun (503) ya da kotası dolu (429) ise sırayla denenecek yedekler.
# Her modelin ücretsiz kotası ayrı sayılır.
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash,gemini-2.0-flash").split(",")
    if m.strip()
]

_client: Optional[genai.Client] = None


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


def _generate(schema: type[BaseModel], contents, system: Optional[str] = None):
    """Yapılandırılmış JSON çıktı ile Gemini çağrısı; gerekirse yedek modellere düşer."""
    models_to_try = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
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
    """Ürün fotoğrafını analiz edip yapılandırılmış ürün bilgisi çıkarır."""
    raw: _GeminiProductAnalysis = _generate(
        _GeminiProductAnalysis,
        contents=[
            types.Part.from_bytes(data=base64.b64decode(image_b64), mime_type=media_type),
            "Bu ürün fotoğrafını analiz et ve Trendyol ilanı için gerekli tüm bilgileri, "
            "ürün tipine uygun en yüksek detay seviyesinde çıkar.",
        ],
        system=(
            "Sen bir Trendyol e-ticaret listeleme uzmanısın. Ürün fotoğraflarını analiz edip "
            "satışa hazır, SEO uyumlu Türkçe ilan içeriği üretirsin. Kurallar:\n"
            "1. Fotoğraftaki HER metni oku (etiket, kutu, ekran, logo, model kodu) ve bilgiye çevir.\n"
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
            "6. observed_attributes içine fotoğraftan okunabilen TÜM teknik detayları ad/değer "
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


# ---- Satış görseli üretimi ----

def plan_scenes(analysis: ProductAnalysis) -> ScenePlan:
    """Ürün tipine uygun 4 satış görseli sahnesi tasarlar."""
    return _generate(
        ScenePlan,
        contents=(
            f"Ürün: {analysis.title}\nTip: {analysis.product_type}\n"
            f"Renk: {analysis.color} | Materyal: {analysis.material}\n"
            f"Özellikler: {json.dumps(analysis.observed_attributes, ensure_ascii=False)}\n\n"
            "Bu ürünün SATIŞ amaçlı e-ticaret görselleri için 4 FARKLI sahne tasarla. Kurallar:\n"
            "1. Giyilebilir ürünse (tişört, elbise, ayakkabı, takı...): en az bir sahnede ürünü "
            "gerçek bir insan model/manken giysin.\n"
            "2. Elde kullanılan ürünse (kalem, telefon, mutfak gereci, alet...): bir sahnede "
            "ürünü kullanan bir el/insan görünsün.\n"
            "3. Büyük ürünse (TV, mobilya, beyaz eşya): bir sahnede ürün gerçek bir yaşam "
            "alanına yerleştirilmiş görünsün.\n"
            "4. Kalan sahneler çeşitlilik sağlasın: farklı açı, detay yakın çekim, temiz stüdyo "
            "çekimi, yaşam tarzı sahnesi gibi.\n"
            "5. Ürün tipine UYMAYAN sahne önerme (tişört için 'elde kullanım' saçma olur).\n"
            "6. Her prompt sahneyi ayrıntılı tarif etsin: kompozisyon, ışık, arka plan, "
            "insan varsa duruşu/kadrajı."
        ),
    )


def generate_marketing_image(image_bytes: bytes, media_type: str, scene_prompt: str) -> bytes:
    """Referans ürün fotoğrafından, verilen sahneye göre satış görseli üretir."""
    prompt = (
        "Fotoğraftaki ürünü BİREBİR koruyarak (renk, desen, form, logo, oran değişmeyecek) "
        f"şu sahneyi oluştur: {scene_prompt}\n"
        "Profesyonel e-ticaret satış fotoğrafı kalitesinde olsun: gerçekçi ışık, temiz "
        "kompozisyon, dikey 3:4 kadraj. Görsel üzerinde hiçbir yazı, filigran veya logo ekleme."
    )
    response = _call_gemini(
        model=IMAGE_MODEL,
        contents=[types.Part.from_bytes(data=image_bytes, mime_type=media_type), prompt],
    )
    for cand in response.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
    raise RuntimeError("Model görsel döndürmedi: " + ((response.text or "").strip()[:200] or "boş yanıt"))
