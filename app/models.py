"""Pydantic şemaları — hem Gemini structured output hem de API request/response için."""
from typing import Optional
from pydantic import BaseModel, Field


class ProductAnalysis(BaseModel):
    """Fotoğraftan çıkarılan ürün bilgisi (Gemini vision çıktısı)."""
    title: str = Field(description="Trendyol için ürün başlığı, en fazla 100 karakter. Marka + ürün tipi + ayırt edici özellikler formatında.")
    description_html: str = Field(description="Trendyol ilanı için HTML formatında zengin ürün açıklaması (<p>, <ul>, <li> kullan). Özellikler, kullanım alanları, malzeme bilgisi.")
    brand_guess: str = Field(description="Fotoğraftan tahmin edilen marka adı. Marka görünmüyorsa 'Bilinmiyor' yaz.")
    product_type: str = Field(description="Ürünün tipi, örn: 'kadın elbise', 'bluetooth kulaklık', 'seramik kupa'.")
    category_keywords: list[str] = Field(description="Kategori eşleştirme için 3-8 Türkçe anahtar kelime.")
    color: Optional[str] = Field(default=None, description="Ana renk (Türkçe), belirlenemiyorsa null.")
    material: Optional[str] = Field(default=None, description="Malzeme, belirlenemiyorsa null.")
    observed_attributes: dict[str, str] = Field(description="Fotoğraftan gözlemlenen diğer özellikler: {'özellik adı': 'değer'}.")
    estimated_price_min_try: float = Field(description="Tahmini pazar fiyatı alt sınırı (TL).")
    estimated_price_max_try: float = Field(description="Tahmini pazar fiyatı üst sınırı (TL).")


class PriceSuggestion(BaseModel):
    suggested_sale_price: float = Field(description="Önerilen satış fiyatı (TL)")
    suggested_list_price: float = Field(description="Önerilen liste (üstü çizili) fiyat (TL), satış fiyatından yüksek")
    rationale: str = Field(description="Fiyat gerekçesi, 1-2 cümle Türkçe")
    competitor_examples: list[str] = Field(default_factory=list, description="Bulunan benzer ürün/fiyat örnekleri")


class CategoryChoice(BaseModel):
    category_id: int = Field(description="Seçilen Trendyol kategori id'si (verilen listeden)")
    category_name: str = Field(description="Seçilen kategorinin tam adı")
    confidence: str = Field(description="high | medium | low")
    reasoning: str = Field(description="Kısa Türkçe gerekçe")


class AttributeValue(BaseModel):
    attribute_id: int = Field(description="Trendyol attributeId")
    attribute_name: str = Field(description="Özelliğin adı (görüntüleme için)")
    attribute_value_id: Optional[int] = Field(default=None, description="Listeden seçilen attributeValueId; özel değer girilecekse null")
    custom_value: Optional[str] = Field(default=None, description="allowCustom ise serbest metin değer; yoksa null")
    value_display: str = Field(description="Seçilen değerin okunabilir hali")


class AttributeFill(BaseModel):
    attributes: list[AttributeValue] = Field(description="Doldurulan kategori özellikleri")


class SceneSpec(BaseModel):
    label: str = Field(description="Sahnenin 2-4 kelimelik Türkçe etiketi, örn: 'Manken üzerinde', 'Elde kullanım'")
    prompt: str = Field(description="Görsel üretim modeline verilecek ayrıntılı Türkçe sahne tarifi: kompozisyon, ışık, arka plan, insan/el varsa duruşu")


class ScenePlan(BaseModel):
    scenes: list[SceneSpec] = Field(description="Ürün tipine uygun 4 farklı satış görseli sahnesi")


# ---- Web API şemaları ----

class DraftAttribute(BaseModel):
    attribute_id: int
    attribute_name: str
    attribute_value_id: Optional[int] = None
    custom_value: Optional[str] = None
    value_display: str = ""


class ProductDraft(BaseModel):
    """Analiz sonrası kullanıcının onayına sunulan taslak."""
    image_filename: str
    title: str
    description_html: str
    brand: str
    category_id: int
    category_name: str
    category_confidence: str = ""
    attributes: list[DraftAttribute] = []
    sale_price: float
    list_price: float
    price_rationale: str = ""
    quantity: int = 10
    vat_rate: int = 20
    barcode: str = ""
    stock_code: str = ""
    currency: str = "TRY"
    extra_image_filenames: list[str] = []


class RecategorizeRequest(BaseModel):
    image_filename: str
    category_id: int


class SceneRequest(BaseModel):
    image_filename: str


class GenerateImageRequest(BaseModel):
    image_filename: str
    scene_index: int


class PublishResult(BaseModel):
    ok: bool
    mode: str
    batch_request_id: Optional[str] = None
    batch_status: Optional[str] = None
    failure_reasons: list[str] = []
    message: str = ""
