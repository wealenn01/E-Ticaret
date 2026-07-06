"""Mağaza asistanı — otomasyonv3'teki OpenAI eğitiminin Gemini'ye taşınmış hali.

Taşınan katmanlar:
- Yorum sınıflandırma (kural tabanlı + Gemini CLASSIFY_SYSTEM)
- Toplu mağaza analizi (INSIGHTS_SYSTEM)
- Soru-cevap asistanı (ASK_SYSTEM)
- Müşteri yorumuna cevap önerisi (REPLY_SYSTEM)
- Kargo / pazar bilgi bankası (kural tabanlı yol gösterici)
"""
from __future__ import annotations
import csv
import io
import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from google.genai import types

from . import ai_agent

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Aktif kullanıcının veri dizini — auth middleware her istekte set eder.
# Set edilmemişse (testler, eski akış) ortak data/ dizini kullanılır.
_USER_DIR: ContextVar[Optional[Path]] = ContextVar("assistant_user_dir", default=None)


def set_user_dir(path: Optional[Path]) -> None:
    _USER_DIR.set(path)


def _data_dir() -> Path:
    d = _USER_DIR.get() or DATA_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _reviews_file() -> Path:
    return _data_dir() / "reviews.json"


def _insights_file() -> Path:
    return _data_dir() / "insights.json"


def _returns_file() -> Path:
    return _data_dir() / "returns.json"


def _shipments_file() -> Path:
    return _data_dir() / "shipments.json"


def _listing_meta_file() -> Path:
    return _data_dir() / "listing_meta.json"


def _settings_file() -> Path:
    return _data_dir() / "assistant_settings.json"

GENERIC_CATEGORIES = frozenset({"diğer", "genel_memnuniyetsizlik"})


# ---------------------------------------------------------------- Gemini JSON

def _chat_json(system: str, user: str) -> dict:
    """OpenAI chat_json muadili: Gemini'den serbest JSON çıktı alır."""
    models = [ai_agent.MODEL] + [m for m in ai_agent.FALLBACK_MODELS if m != ai_agent.MODEL]
    last_err: Exception = RuntimeError("Gemini çağrısı yapılamadı")
    for model in models:
        try:
            response = ai_agent._call_gemini(
                model=model,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            text = (response.text or "").strip()
            return json.loads(text)
        except Exception as e:  # kota/yoğunluk: sıradaki modele geç
            last_err = e
            continue
    raise last_err


def ai_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))


# --------------------------------------------- Kural tabanlı yorum sınıflandırma
# (otomasyonv3 review_analysis.py'den birebir taşındı)

KEYWORDS: list[tuple[str, list[str]]] = [
    ("yanlis_urun", [
        "yanlış ürün", "yanlis urun", "farklı ürün", "farkli urun", "başka ürün",
        "baska urun", "yanlış geldi", "yanlis geldi", "farklı geldi", "farkli geldi",
        "yanlış gönder", "yanlis gonder", "model farklı", "model farkli",
        "sipariş etmediğim", "siparis etmedigim",
    ]),
    ("eksik_urun", [
        "eksik", "parça yok", "parca yok", "parçasız", "parcasiz", "eksik geldi",
        "yarım geldi", "yarim geldi", "set eksik", "adet eksik", "içi boş", "ici bos",
    ]),
    ("beden", [
        "beden", "numara", "küçük geldi", "kucuk geldi", "büyük geldi", "buyuk geldi",
        "oturmad", "dar geldi", "geniş geldi", "genis geldi", "uyumsuz",
        "beden uyuş", "beden uyus", "ölçü tutm", "olcu tutm",
    ]),
    ("kargo", [
        "kargo", "gecik", "teslimat", "dağıtım", "dagitim", "kurye", "geç geldi",
        "gec geldi", "ulaşmad", "ulasmad", "paket yırt", "paket yirt",
    ]),
    ("paketleme", [
        "ambalaj", "özensiz paket", "ozensiz paket", "ezilmiş kutu", "ezilmis kutu",
        "hasarlı kutu", "hasarli kutu",
    ]),
    ("kalite", [
        "kalite", "kumaş", "kumas", "dikiş", "dikis", "defo", "bozuk", "arıza",
        "ariza", "kırık", "kirik", "çalışmıyor", "calismiyor", "dayanıksız",
        "dayaniksiz", "tüylen", "tuylen",
    ]),
    ("urun_bilgisi", [
        "üretim yılı", "uretim yili", "etiket", "son kullanma", "skt", "tarih yaz",
        "yanlış bilgi", "yanlis bilgi", "fotoğraftaki", "fotograftaki",
    ]),
    ("iade", ["iade", "geri gönder", "geri gonder", "değişim", "degisim"]),
    ("fiyat", ["fiyat", "pahalı", "pahali", "beklentime göre", "beklentime gore", "değmez"]),
]

TOPIC_LABELS = {
    "kargo": "Kargo / teslimat", "iade": "İade", "beden": "Beden / numara",
    "kalite": "Kalite", "paketleme": "Paketleme", "yanlis_urun": "Yanlış ürün",
    "eksik_urun": "Eksik ürün", "urun_bilgisi": "Ürün bilgisi", "fiyat": "Fiyat",
    "diğer": "Diğer",
}


def detect_complaint_topic(comment_text: str) -> str:
    text = (comment_text or "").lower()
    if not text.strip():
        return "diğer"
    for name, words in KEYWORDS:
        if any(w in text for w in words):
            return name
    return "diğer"


def classify_review(rating: int, comment_text: str) -> tuple[str, str]:
    sentiment = "negative" if rating <= 3 else "positive"
    return sentiment, detect_complaint_topic(comment_text)


# ------------------------------------------------------- Kargo / pazar rehberi
# (otomasyonv3 cargo_knowledge.py'den Trendyol'a uyarlandı)

TRENDYOL_CARRIERS = [
    {"name": "Trendyol Express", "strength": "Aynı gün / ertesi gün seçenekleri", "consider_when": "Hız şikayeti varsa"},
    {"name": "Sürat Kargo", "strength": "Trendyol anlaşmalı, şehir içi hızlı", "consider_when": "Standart gönderiler"},
    {"name": "Yurtiçi Kargo", "strength": "Geniş dağıtım ağı", "consider_when": "Anadolu ağırlıklı satış"},
    {"name": "Aras Kargo", "strength": "Yüksek hacimde deneyimli", "consider_when": "Günlük çok paket çıkıyorsa hacim indirimi"},
]


def build_cargo_guide(stats: dict) -> list[str]:
    breakdown = stats.get("complaint_breakdown") or {}
    kargo_n = breakdown.get("kargo", 0)
    paket_n = breakdown.get("paketleme", 0)
    if kargo_n == 0 and paket_n == 0:
        return []
    guide = [
        "Trendyol satıcı panelinden anlaşmalı kargo firmalarınızı ve bölgesel SLA raporlarını karşılaştırın.",
    ]
    if kargo_n > 0:
        guide.append(f"Yorumlarda {kargo_n} kargo şikayeti var; tek firmaya bağlı kalmayın, alternatif için pilot bölge seçin.")
    for c in TRENDYOL_CARRIERS[:3]:
        guide.append(f"{c['name']}: {c['strength']}. Ne zaman: {c['consider_when']}.")
    guide.append("Paketleme standardını (çift karton, kırılgan etiket) sabitleyin; hasar şikayeti azalır.")
    return guide[:6]


def build_market_guide(stats: dict) -> list[str]:
    total = stats.get("total_reviews", 0)
    if total == 0:
        return []
    negative = stats.get("negative_reviews", 0)
    neg_pct = round(100 * negative / total) if total else 0
    avg = stats.get("average_rating", 0)
    guide: list[str] = []
    if avg < 4 or neg_pct >= 35:
        guide.append("Puan ortalamanız düşükken kampanya/reklam bütçesini artırmayın; önce şikayet kök nedenini çözün.")
    guide.append("En çok yorum alan ürününüzün rakip listelerini haftada bir inceleyin; fiyat ve açıklama farklarını not edin.")
    if total < 30:
        guide.append("Yorum hacmi henüz düşük — teslimat sonrası kısa bir değerlendirme daveti hacmi artırır.")
    return guide[:5]


# --------------------------------------------------------- Sistem promptları
# (otomasyonv3'teki OpenAI eğitimi — Gemini'ye birebir taşındı)

CLASSIFY_SYSTEM = """Sen Türkçe e-ticaret müşteri yorumlarını sınıflandıran bir asistansın.

Her yorum için:
- sentiment: positive | negative | neutral (yorum metni ve puanı birlikte değerlendir)
- complaint_category: kargo | beden | kalite | paketleme | yanlis_urun | eksik_urun | urun_bilgisi | iade | fiyat | diğer

Somut konu seç: kargo gecikmesi→kargo, beden küçük/büyük→beden, yanlış ürün geldi→yanlis_urun, eksik parça→eksik_urun, üretim yılı/etiket uyumsuzluğu→urun_bilgisi.
Belirsiz olumsuz yorumlarda diğer kullan.

Yalnızca şu JSON formatında cevap ver:
{"results": [{"id": 0, "sentiment": "negative", "complaint_category": "kargo"}, ...]}

id alanları girdi ile birebir aynı olmalı."""

INSIGHTS_SYSTEM = """Sen Türkçe konuşan bir e-ticaret danışmanısın.

Sana bir mağazanın yorum listesi, ürün istatistikleri ve özet metrikler verilir.
TÜM yorumları birlikte değerlendir; tek tek müşteri cevabı YAZMA.

Yüzde değişimleri ve sayıları kullan: "yorumların %X'i şunu söylüyor" gibi.

Yalnızca şu JSON formatında cevap ver:
{
  "general_summary": "2-4 cümle: tüm yorumlara dayalı genel müşteri algısı",
  "main_complaint_topic": "en baskın şikayet konusu (kısa başlık)",
  "main_complaint_detail": "1-2 cümle: neden öne çıktığı, sayı/yüzde kullan",
  "product_highlights": ["ürün A: ... somut gözlem ..."],
  "cargo_guide": ["kargo şikayeti varsa: anlaşmalı firmaları karşılaştır, Trendyol Express/Sürat/Yurtiçi önerisi ..."],
  "market_guide": ["piyasa/fiyat: rakip incele, kampanya önerisi ..."],
  "recommendations": ["satıcının yapması gereken 1", "yapması gereken 2"],
  "avoid_actions": ["yapmaması gereken 1", "yapmaması gereken 2"]
}

cargo_guide: kargo veya paketleme şikayeti varsa somut yol göster (firma adı, SLA, pilot bölge). Yoksa boş dizi.
market_guide: fiyat, kampanya, rakip, stok stratejisi. Yorum azsa veri toplama öner.
product_highlights 1-4 madde; recommendations ve avoid_actions en az 2, en fazla 5 madde.
Somut ürün adı/SKU ve yüzde kullan. avoid_actions: riskli veya verimsiz hamleleri belirt."""

ASK_SYSTEM = """Sen samimi, doğal konuşan bir e-ticaret danışmanısın. Türkçe cevap ver.

SADECE satıcının yüklediği verilere (yorumlar, iade kayıtları, kargo kayıtları) dayan — uydurma yapma.
İade soruları için returns verisini, kargo/teslimat soruları için shipments verisini ve kargo kategorili yorumları kullan.
Veride olmayan ürün sorulursa nazikçe "bu ürünü satmıyor olabilirsiniz" de ve mevcut ürünleri örnek ver.
"kargo", "iade", "beden" gibi KONU soruları ürün adı değildir — şikayet kategorisi ve veriyle analiz et.

Olasılıksal ve kanıtlı konuş: "yorumların %X'i şunu söylüyor", "büyük ihtimalle", "veriye göre en olası senaryo" gibi ifadeler kullan.
Genel tavsiye verme — her öneriyi sayı, yüzde veya örnek yorumla destekle.
Samimi ol; satıcıya "siz şu an ... yapmışsınız, şöyle deneseniz daha doğru olabilir" tonunda yaklaş.

Anlaşılmayan kısa mesajlarda "Anlaşılmadı, sorunuzu tekrar alabilir miyim?" de.
Selamlaşma / hal hatır (nasılsın, merhaba): kısa samimi cevap ver, istatistik dökme.

Kullanıcı sadece olumsuz yorum / şikayet isterse:
- Sadece olumsuz yorumları listele ve kısa analiz yap
- Gereksiz genel istatistik, kargo veya iade bilgisi ekleme
- recommendations en fazla 2 kısa madde veya boş dizi

Normal sorularda samimi ol; "yüklediğiniz verilere göre" ifadesini kullan.

Yalnızca JSON döndür:
{
  "answer": "2-5 cümle, kanıtlı ve olasılıksal — madde madde gerekirse kısa",
  "recommendations": ["en fazla 3 kanıtlı öneri — öncelik sırasına göre"],
  "evidence": ["sadece gerekirse 1-3 kanıt — yüzde veya örnek yorum"],
  "sorted_complaints": ["olumsuz yorum/şikayet listesi — istenmişse doldur"]
}
"""

REPLY_SYSTEM = """Sen Türkçe konuşan profesyonel bir e-ticaret satıcısısın.
Müşteri yorumuna kısa, samimi ve çözüm odaklı bir mağaza cevabı yaz.

Kurallar:
- 2-4 cümle, resmi ama sıcak ton
- Olumsuz yorumlarda özür ve somut çözüm (değişim, iade, iletişim)
- Olumlu yorumlarda teşekkür
- Marka adı uydurma, indirim vaat etme

Yalnızca şu JSON formatında cevap ver:
{"suggested_reply": "..."}"""

_CATEGORY_REPLIES: dict[str, str] = {
    "kargo": "Değerli müşterimiz, yaşadığınız kargo gecikmesi için özür dileriz. Siparişinizin durumunu kontrol edip en kısa sürede size bilgi vereceğiz. Geri bildiriminiz için teşekkür ederiz.",
    "beden": "Merhaba, beden uyumsuzluğu yaşamanıza üzüldük. Ürün sayfasındaki ölçü tablosunu güncelliyoruz; değişim veya iade için müşteri hizmetlerimizle iletişime geçebilirsiniz.",
    "kalite": "Ürün kalitesinden memnun kalmamanızı üzücü buluyoruz. Kalite kontrol süreçlerimizi gözden geçiriyoruz; iade veya değişim konusunda size yardımcı olmaktan memnuniyet duyarız.",
    "paketleme": "Paketleme konusundaki geri bildiriminiz için teşekkür ederiz. Ürünlerin hasarsız ulaşması için ambalaj standartlarımızı sıkılaştırıyoruz.",
    "yanlis_urun": "Yanlış ürün ulaştığı için özür dileriz. Doğru ürünü en kısa sürede göndermek veya iade sürecinizi başlatmak için müşteri hizmetlerimizle iletişime geçmenizi rica ederiz.",
    "eksik_urun": "Eksik parça veya ürün ulaştığı için üzgünüz. Eksik kalemi tamamlamak için hemen sizinle iletişime geçeceğiz.",
    "iade": "İade talebinizle ilgili size yardımcı olmak isteriz. Müşteri hizmetlerimiz sürecinizi hızlıca tamamlamanız için hazır.",
    "fiyat": "Fiyat ve beklenti konusundaki geri bildiriminizi not aldık. Ürün değerini artırmak için açıklama ve kalite süreçlerimizi gözden geçiriyoruz.",
    "diğer": "Geri bildiriminiz bizim için değerli. Yaşadığınız sorunu çözmek için ekibimiz sizinle iletişime geçecektir. İlginiz için teşekkür ederiz.",
}


# ---------------------------------------------------------------- Veri katmanı

def load_reviews() -> list[dict]:
    if _reviews_file().exists():
        return json.loads(_reviews_file().read_text(encoding="utf-8"))
    return []


def save_reviews(reviews: list[dict]) -> None:
    _reviews_file().write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    # İstatistik değişti — eski analiz geçersiz
    if _insights_file().exists():
        _insights_file().unlink()


def import_reviews_csv(raw: bytes) -> dict:
    """CSV formatı: product_name,sku,rating,comment_text,review_date"""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"product_name", "rating", "comment_text"}
    if not reader.fieldnames or not required.issubset({(f or "").strip() for f in reader.fieldnames}):
        raise ValueError(
            "CSV başlıkları eksik. Beklenen sütunlar: product_name, sku, rating, comment_text, review_date"
        )
    reviews = load_reviews()
    added = 0
    skipped = 0
    default_platform = load_settings().get("platform", "trendyol")
    existing = {
        (r.get("sku", ""), r.get("review_date", ""), r.get("comment_text", ""))
        for r in reviews
    }
    for row in reader:
        try:
            rating = int(float(str(row.get("rating", "")).strip()))
        except ValueError:
            continue
        comment = (row.get("comment_text") or "").strip()
        sku = (row.get("sku") or "").strip()
        review_date = (row.get("review_date") or "").strip()
        key = (sku, review_date, comment)
        if key in existing:
            skipped += 1
            continue
        sentiment, category = classify_review(rating, comment)
        reviews.append({
            "product_name": (row.get("product_name") or "").strip() or "Bilinmeyen ürün",
            "sku": sku,
            "rating": max(1, min(5, rating)),
            "comment_text": comment,
            "review_date": review_date,
            "sentiment": sentiment,
            "complaint_category": category,
            "platform": default_platform,
        })
        existing.add(key)
        added += 1
    if added == 0 and skipped == 0:
        raise ValueError("CSV'den hiç yorum okunamadı — dosya formatını kontrol edin.")
    save_reviews(reviews)
    settings = load_settings()
    if settings.get("data_cleared"):
        save_settings({**settings, "data_cleared": False})
    return {"added": added, "skipped": skipped, "total": len(reviews)}


def clear_reviews() -> None:
    if _reviews_file().exists():
        _reviews_file().unlink()
    if _insights_file().exists():
        _insights_file().unlink()
    if _listing_meta_file().exists():
        _listing_meta_file().unlink()
    save_settings({**load_settings(), "data_cleared": True})


def _sample_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "sample-data"


def seed_sample_data(*, include_orders: bool = False) -> dict:
    """sample-data/ örnek dosyalarını aktif kullanıcı dizinine yükler."""
    sample_dir = _sample_data_dir()
    out: dict = {"reviews": 0, "returns": 0, "shipments": 0, "orders": 0}

    reviews_csv = sample_dir / "yorumlar_ornek.csv"
    if reviews_csv.exists():
        try:
            r = import_reviews_csv(reviews_csv.read_bytes())
            out["reviews"] = r.get("total", 0)
        except Exception:
            logger.exception("Örnek yorum verisi yüklenemedi")

    if not load_returns():
        returns_csv = sample_dir / "iadeler_ornek.csv"
        if returns_csv.exists():
            try:
                r = import_returns_csv(returns_csv.read_bytes())
                out["returns"] = r.get("total", 0)
            except Exception:
                logger.exception("Örnek iade verisi yüklenemedi")

    if not load_shipments():
        shipments_csv = sample_dir / "kargolar_ornek.csv"
        if shipments_csv.exists():
            try:
                r = import_shipments_csv(shipments_csv.read_bytes())
                out["shipments"] = r.get("total", 0)
            except Exception:
                logger.exception("Örnek kargo verisi yüklenemedi")

    if include_orders:
        orders_csv = sample_dir / "siparisler_ornek.csv"
        if orders_csv.exists():
            try:
                from . import analytics as _analytics
                r = _analytics.import_orders_csv(orders_csv.read_bytes())
                out["orders"] = r.get("total", r.get("imported", 0))
            except Exception:
                logger.exception("Örnek sipariş verisi yüklenemedi")

    settings = load_settings()
    save_settings({**settings, "starter_seeded": True, "data_cleared": False})
    return out


def seed_store_demo(trendyol_creds: Optional[dict] = None) -> dict:
    """Bağlı mağaza ürünlerine göre örnek yorum + sipariş/iade verisi yükler."""
    from datetime import date, timedelta

    out = seed_sample_data(include_orders=True)
    products: list[dict] = []
    if trendyol_creds:
        try:
            from .marketplace_api.trendyol_api import TrendyolAPI
            api = TrendyolAPI(trendyol_creds)
            if api.configured:
                products = api.list_products(size=12)
        except Exception:
            logger.exception("Mağaza ürünleri alınamadı")

    if not products and trendyol_creds:
        # API erişilemezse stage test mağazasındaki bilinen ürünlerle doldur
        products = [
            {"title": "Beşiktaşlı Sağır Rıfat Usta A+ Kalite Abanoz Tesbih", "sku": "TSB-2026-JSD0JZ"},
            {"title": "Atlas Bot Kahverengi", "sku": "TY-C12-LUMBER"},
            {"title": "Colourist Şalyaka Bornoz - Antrasit S/M", "sku": "ymkcolourist15"},
            {"title": "Colourist Şalyaka Bornoz Havlu 50x90 - Antrasit L", "sku": "TYBV76VW4FR5QHZP76"},
            {"title": "Ali Eriş Usta A+ Kalite Damla Kehribar Tesbih 7x11", "sku": "TSB-2026-TG8JO8"},
        ]

    templates = [
        (5, "Ürün beklediğim gibi geldi, memnun kaldım.", "positive", "kalite"),
        (4, "Kaliteli ürün, kargo hızlıydı.", "positive", "kargo"),
        (3, "Fotoğraftaki renk biraz farklı ama kullanılabilir.", "neutral", "urun_bilgisi"),
        (2, "Paketleme zayıftı, kutu ezilmişti.", "negative", "paketleme"),
        (1, "Beden tam oturmadı, iade düşünüyorum.", "negative", "beden"),
    ]
    base = date.today()
    reviews: list[dict] = []
    for pi, p in enumerate(products):
        name = (p.get("title") or p.get("product") or "Ürün").strip()
        sku = (p.get("sku") or f"SKU-{pi}").strip()
        for ti, (rating, text, sent, cat) in enumerate(templates[: 3 + (pi % 3)]):
            reviews.append({
                "product_name": name,
                "sku": sku,
                "rating": rating,
                "comment_text": text,
                "review_date": (base - timedelta(days=pi * 2 + ti + 1)).isoformat(),
                "sentiment": sent,
                "complaint_category": cat,
                "platform": "trendyol",
            })
    save_reviews(reviews)
    out["reviews"] = len(reviews)
    out["store_products"] = len(products)

    return_reasons = ["Beden uymadı", "Vazgeçtim", "Ürün hasarlı", "Yanlış ürün gönderildi", "Kargo gecikmesi"]
    returns = load_returns()
    existing = {(r.get("sku"), r.get("return_reason"), r.get("return_date")) for r in returns}
    for pi, p in enumerate(products):
        name = (p.get("title") or p.get("product") or "Ürün").strip()
        sku = (p.get("sku") or f"SKU-{pi}").strip()
        reason = return_reasons[pi % len(return_reasons)]
        rdate = (base - timedelta(days=pi * 3 + 2)).isoformat()
        key = (sku, reason, rdate)
        if key in existing:
            continue
        returns.append({
            "product_name": name,
            "sku": sku,
            "return_reason": reason,
            "return_date": rdate,
            "quantity": 1,
        })
        existing.add(key)
    if returns:
        _returns_file().write_text(json.dumps(returns, ensure_ascii=False, indent=1), encoding="utf-8")
        out["returns"] = len(returns)

    return out


def ensure_starter_data(*, force: bool = False) -> dict:
    """Asistan verisi yoksa örnek mağaza verisini otomatik yükler (tüm kullanıcılar)."""
    if not force:
        if load_reviews():
            return {"seeded": False, "reason": "has_data"}
        if load_settings().get("data_cleared"):
            return {"seeded": False, "reason": "cleared_by_user"}
    result = seed_sample_data()
    seeded = result.get("reviews", 0) > 0
    return {"seeded": seeded, **result}


def load_settings() -> dict:
    default = {"platform": "trendyol", "seller_model": None}
    if _settings_file().exists():
        try:
            return {**default, **json.loads(_settings_file().read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            pass
    return default


def save_settings(data: dict) -> dict:
    current = load_settings()
    current.update({k: v for k, v in data.items() if k in ("platform", "seller_model")})
    _settings_file().write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
    return current


def load_listing_meta() -> dict:
    if _listing_meta_file().exists():
        return json.loads(_listing_meta_file().read_text(encoding="utf-8"))
    return {}


def upsert_listing_meta(sku: str, *, image_url: str | None = None, source_url: str | None = None, product_name: str | None = None) -> None:
    if not sku:
        return
    meta = load_listing_meta()
    key = sku.strip().upper()
    entry = meta.get(key, {})
    if image_url:
        entry["image_url"] = image_url
    if source_url:
        entry["source_url"] = source_url
    if product_name:
        entry["product_name"] = product_name
    meta[key] = entry
    _listing_meta_file().write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def backfill_missing_listing_images() -> None:
    """source_url var ama image_url yoksa ürün sayfasından og:image çeker."""
    from .review_scraper import _product_image_from_html, detect_platform, normalize_product_page_url

    meta = load_listing_meta()
    changed = False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "tr-TR,tr;q=0.9",
    }
    try:
        import httpx
    except ImportError:
        return
    for key, entry in list(meta.items()):
        if entry.get("image_url") or not entry.get("source_url"):
            continue
        try:
            url = entry["source_url"]
            platform = detect_platform(url)
            page_url = normalize_product_page_url(url, platform)
            with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
                r = client.get(page_url)
                r.raise_for_status()
                img = _product_image_from_html(r.text)
            if img:
                entry["image_url"] = img
                meta[key] = entry
                changed = True
        except Exception:
            continue
    if changed:
        _listing_meta_file().write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def infer_product_platform(sku: str, source_url: str | None = None) -> str | None:
    url = (source_url or "").lower()
    if "trendyol.com" in url:
        return "trendyol"
    if "hepsiburada.com" in url:
        return "hepsiburada"
    if "n11.com" in url:
        return "n11"
    if "amazon.com.tr" in url:
        return "amazon"
    sku_upper = (sku or "").upper()
    if sku_upper.startswith("TY-"):
        return "trendyol"
    if sku_upper.startswith("N11-"):
        return "n11"
    if sku_upper.startswith("AMZ-"):
        return "amazon"
    if re.match(r"^(HB[A-Z0-9]{4,}|OTM[A-Z0-9]+|HBCV[A-Z0-9]+)$", sku_upper):
        return "hepsiburada"
    return None


def product_belongs_to_platform(
    sku: str, source_url: str | None, platform: str, stored_platform: str | None = None,
) -> bool:
    if stored_platform:
        return stored_platform == platform
    inferred = infer_product_platform(sku, source_url)
    if inferred is None:
        return True
    return inferred == platform


def _review_matches_platform(review: dict, platform: str | None) -> bool:
    if not platform:
        return True
    return product_belongs_to_platform(
        review.get("sku", ""),
        review.get("source_url"),
        platform,
        review.get("platform"),
    )


def scrape_url_matches_store(url: str, store_platform: str) -> None:
    """URL pazaryeri mağaza platformuyla uyuşmuyorsa ValueError."""
    from .review_scraper import detect_platform

    url_platform = detect_platform(url)
    if url_platform == "unknown":
        raise ValueError(
            "Desteklenmeyen link. Hepsiburada, Trendyol, N11 veya Amazon.com.tr ürün sayfası girin."
        )
    labels = {
        "hepsiburada": "Hepsiburada",
        "trendyol": "Trendyol",
        "n11": "N11",
        "amazon": "Amazon",
    }
    if store_platform in labels and url_platform != store_platform:
        raise ValueError(
            f"Bu mağaza {labels.get(store_platform, store_platform)} için. "
            f"{labels.get(url_platform, url_platform)} ürün linki buraya eklenemez — "
            f"doğru platformda mağaza açın veya linki değiştirin."
        )


def add_scraped_reviews(rows: list[dict], meta: dict | None = None) -> dict:
    """Linkten çekilen yorumları ekler; aynı SKU varsa eski yorumları değiştirir (otomasyonv3)."""
    reviews = load_reviews()
    sku = str((meta or {}).get("sku") or (rows[0].get("sku") if rows else "") or "").strip()
    if sku:
        reviews = [r for r in reviews if r.get("sku") != sku]
    added = 0
    platform = load_settings().get("platform", "trendyol")
    if meta and meta.get("source_url"):
        inferred = infer_product_platform(sku, meta.get("source_url"))
        if inferred:
            platform = inferred
    for row in rows:
        sentiment, category = classify_review(row["rating"], row["comment_text"])
        reviews.append({
            "product_name": row["product_name"],
            "sku": row.get("sku", sku),
            "rating": row["rating"],
            "comment_text": row["comment_text"],
            "review_date": str(row.get("review_date") or ""),
            "sentiment": sentiment,
            "complaint_category": category,
            "platform": platform,
            "source_url": (meta or {}).get("source_url"),
        })
        added += 1
    save_reviews(reviews)
    if sku and meta:
        upsert_listing_meta(
            sku,
            image_url=meta.get("image_url"),
            source_url=meta.get("source_url"),
            product_name=meta.get("product_name") or (rows[0]["product_name"] if rows else None),
        )
    if _insights_file().exists():
        _insights_file().unlink()
    return {"added": added, "skipped": 0, "total": len(reviews), "sku": sku}


def delete_product(sku: str) -> dict:
    sku = sku.strip()
    reviews_before = len(load_reviews())
    reviews = [r for r in load_reviews() if r.get("sku") != sku]
    reviews_deleted = reviews_before - len(reviews)
    save_reviews(reviews)
    returns_before = len(load_returns())
    returns = [r for r in load_returns() if r.get("sku") != sku]
    returns_deleted = returns_before - len(returns)
    _returns_file().write_text(json.dumps(returns, ensure_ascii=False, indent=1), encoding="utf-8")
    meta = load_listing_meta()
    meta.pop(sku.upper(), None)
    _listing_meta_file().write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if _insights_file().exists():
        _insights_file().unlink()
    return {"sku": sku, "reviews_deleted": reviews_deleted, "returns_deleted": returns_deleted}


# ------------------------------------------------------------- İade (returns)

def load_returns() -> list[dict]:
    if _returns_file().exists():
        return json.loads(_returns_file().read_text(encoding="utf-8"))
    return []


def import_returns_csv(raw: bytes) -> dict:
    """CSV formatı: product_name,sku,return_reason,return_date,quantity"""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"product_name", "return_reason"}
    if not reader.fieldnames or not required.issubset({(f or "").strip() for f in reader.fieldnames}):
        raise ValueError(
            "CSV başlıkları eksik. Beklenen sütunlar: product_name, sku, return_reason, return_date, quantity"
        )
    returns = load_returns()
    added = 0
    skipped = 0
    existing = {
        (r.get("sku", ""), r.get("return_reason", ""), r.get("return_date", ""))
        for r in returns
    }
    for row in reader:
        reason = (row.get("return_reason") or "").strip()
        if not reason:
            continue
        sku = (row.get("sku") or "").strip()
        return_date = (row.get("return_date") or "").strip()
        key = (sku, reason, return_date)
        if key in existing:
            skipped += 1
            continue
        try:
            qty = max(1, int(float(str(row.get("quantity") or "1").strip())))
        except ValueError:
            qty = 1
        returns.append({
            "product_name": (row.get("product_name") or "").strip() or "Bilinmeyen ürün",
            "sku": sku,
            "return_reason": reason,
            "return_date": return_date,
            "quantity": qty,
        })
        existing.add(key)
        added += 1
    if added == 0 and skipped == 0:
        raise ValueError("CSV'den hiç iade kaydı okunamadı — dosya formatını kontrol edin.")
    _returns_file().write_text(json.dumps(returns, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"added": added, "skipped": skipped, "total": len(returns)}


def clear_returns() -> None:
    if _returns_file().exists():
        _returns_file().unlink()


def compute_returns_stats() -> dict:
    returns = load_returns()
    total = len(returns)
    qty = sum(r["quantity"] for r in returns)
    reasons: dict[str, int] = {}
    by_product: dict[str, dict] = {}
    for r in returns:
        reasons[r["return_reason"]] = reasons.get(r["return_reason"], 0) + r["quantity"]
        key = r.get("sku") or r["product_name"]
        p = by_product.setdefault(key, {"product_name": r["product_name"], "sku": r.get("sku", ""), "count": 0, "quantity": 0, "reasons": {}})
        p["count"] += 1
        p["quantity"] += r["quantity"]
        p["reasons"][r["return_reason"]] = p["reasons"].get(r["return_reason"], 0) + r["quantity"]
    products = sorted(by_product.values(), key=lambda x: -x["quantity"])
    for p in products:
        p["top_reason"] = max(p["reasons"], key=p["reasons"].get) if p["reasons"] else None
    return {
        "total_returns": total,
        "return_quantity": qty,
        "reason_breakdown": dict(sorted(reasons.items(), key=lambda x: -x[1])),
        "products": products,
    }


def list_returns_panel() -> dict:
    """İade paneli — özet metrikler + son kayıtlar."""
    stats = compute_returns_stats()
    returns = sorted(load_returns(), key=lambda r: r.get("return_date") or "", reverse=True)
    shipments_n = len(load_shipments())
    reviews_n = len(load_reviews())
    denom = max(shipments_n, reviews_n // 3, stats["total_returns"], 1)
    return_rate = round(100 * stats["total_returns"] / denom, 1) if stats["total_returns"] else 0.0
    reasons = stats.get("reason_breakdown") or {}
    top_reason = max(reasons, key=reasons.get) if reasons else None
    top_reason_pct = round(100 * reasons[top_reason] / max(stats["return_quantity"], 1)) if top_reason else 0
    avg_days = 0.0
    if returns:
        ages = []
        today = date.today()
        for r in returns:
            rd = _parse_row_date(r.get("return_date") or "")
            if rd:
                ages.append(max(0, (today - rd).days))
        avg_days = round(sum(ages) / len(ages), 1) if ages else 3.2
    return {
        **stats,
        "returns": returns[:100],
        "return_rate_pct": return_rate,
        "top_reason": top_reason,
        "top_reason_pct": top_reason_pct,
        "avg_processing_days": avg_days or 3.2,
        "processing_count": stats["total_returns"],
    }


_PERIOD_DAYS = {7, 15, 30, 90}
_PERIOD_LABELS = {7: "Son 7 gün", 15: "Son 15 gün", 30: "Son 1 ay", 90: "Son 3 ay"}


def _parse_row_date(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            chunk = s[:10] if fmt == "%Y-%m-%d" else s
            return datetime.strptime(chunk, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _row_in_period(row_date: date | None, start: date, end: date) -> bool:
    if row_date is None:
        return True
    return start <= row_date <= end


def _breakdown_to_series(counts: dict[str, int], label_fn) -> list[dict]:
    items = sorted(counts.items(), key=lambda x: -x[1])[:8]
    return [{"key": k, "label": label_fn(k), "count": v} for k, v in items if v > 0]


def _build_period_summary(
    days: int,
    review_stats: dict,
    return_stats: dict,
    start: date,
    end: date,
) -> dict:
    total_r = review_stats["total"]
    neg_r = review_stats["negative"]
    pos_r = review_stats["positive"]
    avg = review_stats["average_rating"]
    ret_qty = return_stats["quantity"]

    highlights: list[str] = []
    actions: list[str] = []

    if total_r == 0 and ret_qty == 0:
        return {
            "title": f"{_PERIOD_LABELS.get(days, f'Son {days} gün')} ({start.isoformat()} — {end.isoformat()})",
            "text": "Bu dönemde yorum veya iade kaydı bulunamadı. Yorumlar ve İade sekmelerinden veri ekleyin.",
            "highlights": [],
            "actions": ["Yorumlar sekmesinden ürün linki veya CSV ile geri bildirim ekleyin.", "İade CSV yükleyerek iade grafiğini doldurun."],
        }

    if total_r > 0:
        neg_pct = round(100 * neg_r / total_r)
        highlights.append(f"{total_r} değerlendirme · ortalama {avg}★ · %{neg_pct} olumsuz (1–3★)")
        top_topics = review_stats.get("topics") or []
        if top_topics:
            t = top_topics[0]
            highlights.append(f"En sık şikayet konusu: {t['label']} ({t['count']} yorum)")
        top_prod = review_stats.get("top_problem_products") or []
        if top_prod:
            p = top_prod[0]
            highlights.append(f"En problemli ürün: {p['product_name']} ({p['negative_count']} olumsuz)")
        if neg_pct >= 20:
            actions.append("Olumsuz yorum oranı yüksek — ürün sayfası ve kalite kontrolünü gözden geçirin.")
        elif avg >= 4.2:
            actions.append("Genel memnuniyet iyi — olumlu yorumları ürün sayfasında öne çıkarabilirsiniz.")

    if ret_qty > 0:
        highlights.append(f"{return_stats['total']} iade kaydı · toplam {ret_qty} adet")
        ret_topics = return_stats.get("reasons") or []
        if ret_topics:
            highlights.append(f"Önde gelen iade sebebi: {ret_topics[0]['label']} ({ret_topics[0]['count']} adet)")
        actions.append("İade sebeplerini ürün açıklaması ve beden tablosuyla karşılaştırın.")
    elif total_r > 0:
        actions.append("İade verisi yok — İade sekmesinden CSV yükleyerek iade grafiğini aktifleştirin.")

    if not actions:
        actions.append("Dönem verilerini haftalık takip ederek trend değişimlerini izleyin.")

    text_parts = []
    if total_r:
        text_parts.append(
            f"Son {days} günde {total_r} müşteri geri bildirimi incelendi; "
            f"ortalama puan {avg}, olumsuz oran %{round(100 * neg_r / total_r) if total_r else 0}."
        )
    if ret_qty:
        text_parts.append(f"Aynı dönemde {ret_qty} adet iade kaydı var.")
    if not text_parts:
        text_parts.append("Dönem içinde kayıtlı veri sınırlı.")

    return {
        "title": f"{_PERIOD_LABELS.get(days, f'Son {days} gün')} ({start.isoformat()} — {end.isoformat()})",
        "text": " ".join(text_parts),
        "highlights": highlights[:5],
        "actions": actions[:4],
    }


def compute_period_report(days: int = 7, platform: str | None = None) -> dict:
    """Dönemsel yorum (geri bildirim) ve iade raporu — grafikler için özet seriler."""
    days = days if days in _PERIOD_DAYS else 7
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)

    reviews = load_reviews()
    if platform and platform != "all":
        reviews = [r for r in reviews if _review_matches_platform(r, platform)]
    period_reviews = [
        r for r in reviews
        if _row_in_period(_parse_row_date(r.get("review_date", "")), start, end)
    ]

    returns = load_returns()
    period_returns = [
        r for r in returns
        if _row_in_period(_parse_row_date(r.get("return_date", "")), start, end)
    ]

    total_r = len(period_reviews)
    neg_r = sum(1 for r in period_reviews if r.get("sentiment") == "negative")
    pos_r = sum(1 for r in period_reviews if r.get("sentiment") == "positive")
    neu_r = max(0, total_r - pos_r - neg_r)
    avg = round(sum(r["rating"] for r in period_reviews) / total_r, 2) if total_r else 0.0

    topic_counts: dict[str, int] = {}
    for r in period_reviews:
        if r.get("sentiment") == "negative":
            cat = r.get("complaint_category") or "diğer"
            topic_counts[cat] = topic_counts.get(cat, 0) + 1

    by_product: dict[str, dict] = {}
    for r in period_reviews:
        if r.get("sentiment") != "negative":
            continue
        key = r.get("sku") or r["product_name"]
        p = by_product.setdefault(key, {"product_name": r["product_name"], "sku": r.get("sku", ""), "negative_count": 0})
        p["negative_count"] += 1
    top_problem = sorted(by_product.values(), key=lambda x: -x["negative_count"])[:5]

    reason_counts: dict[str, int] = {}
    ret_rows = 0
    ret_qty = 0
    for r in period_returns:
        ret_rows += 1
        q = r.get("quantity", 1)
        ret_qty += q
        reason = (r.get("return_reason") or "Belirtilmemiş").strip()
        reason_counts[reason] = reason_counts.get(reason, 0) + q

    review_stats = {
        "total": total_r,
        "positive": pos_r,
        "negative": neg_r,
        "neutral": neu_r,
        "average_rating": avg,
        "topics": _breakdown_to_series(topic_counts, lambda k: TOPIC_LABELS.get(k, k.replace("_", " ").title())),
        "sentiment_series": [
            {"key": "positive", "label": "Olumlu", "count": pos_r},
            {"key": "negative", "label": "Olumsuz", "count": neg_r},
            {"key": "neutral", "label": "Nötr", "count": neu_r},
        ],
        "top_problem_products": top_problem,
    }

    return_stats = {
        "total": ret_rows,
        "quantity": ret_qty,
        "reasons": _breakdown_to_series(reason_counts, lambda k: k),
    }

    summary = _build_period_summary(days, review_stats, return_stats, start, end)
    daily = _build_daily_series(start, end, period_reviews, period_returns)
    recent = _build_recent_feedback(period_reviews)
    metrics = _build_report_metrics(review_stats, return_stats, daily)

    period_shipments = [
        s for s in load_shipments()
        if _row_in_period(_parse_row_date(s.get("shipped_date", "")), start, end)
    ]
    ship_delivered = sum(1 for s in period_shipments if s.get("status") == "delivered")
    ship_transit = sum(1 for s in period_shipments if s.get("status") in ("shipped", "in_transit"))
    shipment_stats = {
        "total": len(period_shipments),
        "delivered": ship_delivered,
        "in_transit": ship_transit,
        "other": max(0, len(period_shipments) - ship_delivered - ship_transit),
    }

    return {
        "period_days": days,
        "period_label": _PERIOD_LABELS.get(days, f"Son {days} gün"),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "platform": platform or load_settings().get("platform", "trendyol"),
        "reviews": review_stats,
        "returns": return_stats,
        "shipments": shipment_stats,
        "summary": summary,
        "daily_series": daily,
        "recent_feedback": recent,
        "metrics": metrics,
    }


def _build_daily_series(
    start: date,
    end: date,
    period_reviews: list[dict],
    period_returns: list[dict],
) -> list[dict]:
    buckets: dict[str, dict] = {}
    cur = start
    while cur <= end:
        buckets[cur.isoformat()] = {
            "reviews": 0, "returns": 0, "negative": 0, "rating_sum": 0, "rating_n": 0,
        }
        cur += timedelta(days=1)

    for r in period_reviews:
        d = _parse_row_date(r.get("review_date", "")) or end
        if d < start or d > end:
            continue
        b = buckets[d.isoformat()]
        b["reviews"] += 1
        if r.get("sentiment") == "negative":
            b["negative"] += 1
        b["rating_sum"] += int(r.get("rating") or 0)
        b["rating_n"] += 1

    for r in period_returns:
        d = _parse_row_date(r.get("return_date", "")) or end
        if d < start or d > end:
            continue
        buckets[d.isoformat()]["returns"] += int(r.get("quantity") or 1)

    out: list[dict] = []
    cur = start
    while cur <= end:
        k = cur.isoformat()
        b = buckets[k]
        out.append({
            "date": k,
            "label": cur.strftime("%d.%m"),
            "reviews": b["reviews"],
            "returns": b["returns"],
            "negative": b["negative"],
            "avg_rating": round(b["rating_sum"] / b["rating_n"], 1) if b["rating_n"] else None,
        })
        cur += timedelta(days=1)
    return out


def _build_recent_feedback(period_reviews: list[dict], limit: int = 8) -> list[dict]:
    def sort_key(r: dict) -> date:
        return _parse_row_date(r.get("review_date", "")) or date.min

    rows = sorted(period_reviews, key=sort_key, reverse=True)[:limit]
    out: list[dict] = []
    for r in rows:
        text = (r.get("comment_text") or "").strip()
        name = (r.get("product_name") or "Ürün").strip()
        out.append({
            "product_name": name,
            "rating": int(r.get("rating") or 0),
            "sentiment": r.get("sentiment") or "neutral",
            "text": text[:180] + ("…" if len(text) > 180 else ""),
            "review_date": r.get("review_date") or "",
            "initial": name[0].upper() if name else "?",
            "topic": TOPIC_LABELS.get(r.get("complaint_category") or "", ""),
        })
    return out


def _trend_pct(series: list[int]) -> float | None:
    if len(series) < 4:
        return None
    mid = len(series) // 2
    prev = sum(series[:mid]) or 0
    curr = sum(series[mid:]) or 0
    if prev == 0:
        return 100.0 if curr > 0 else 0.0
    return round(100 * (curr - prev) / prev, 1)


def _build_report_metrics(review_stats: dict, return_stats: dict, daily: list[dict]) -> dict:
    neg_series = [d["negative"] for d in daily]
    rev_series = [d["reviews"] for d in daily]
    ret_series = [d["returns"] for d in daily]
    rating_series = [d["avg_rating"] or 0 for d in daily if d["avg_rating"] is not None]
    total = review_stats.get("total") or 0
    pos = review_stats.get("positive") or 0
    return {
        "complaint_count": review_stats.get("negative") or 0,
        "complaint_trend_pct": _trend_pct(neg_series),
        "complaint_sparkline": neg_series,
        "review_count": total,
        "review_sparkline": rev_series,
        "return_quantity": return_stats.get("quantity") or 0,
        "return_sparkline": ret_series,
        "average_rating": review_stats.get("average_rating") or 0,
        "rating_sparkline": rating_series or rev_series,
        "positive_pct": round(100 * pos / total) if total else 0,
    }


RETURNS_INSIGHTS_SYSTEM = """Sen Türkçe konuşan bir e-ticaret iade analisti danışmanısın.

Sana bir mağazanın iade kayıtları (ürün, sebep, adet) ve varsa ilgili müşteri yorumları verilir.
Tüm iadeleri birlikte değerlendir; satıcıya iadeyi AZALTMAK için somut yol göster.

Sayı ve yüzde kullan: "iadelerin %X'i şu sebepten" gibi.

Yalnızca şu JSON formatında cevap ver:
{
  "summary": "2-3 cümle: iade tablosunun genel özeti, en kritik bulgu",
  "top_reason": "en sık iade sebebi (kısa)",
  "top_reason_detail": "1-2 cümle: bu sebebin kök nedeni ne olabilir",
  "product_actions": ["ürün bazlı aksiyon 1", "aksiyon 2"],
  "recommendations": ["iadeyi azaltacak somut öneri 1", "öneri 2"],
  "avoid_actions": ["yapmaması gereken 1"]
}

product_actions: en çok iade alan ürünler için özel adımlar (ölçü tablosu, görsel, açıklama, kalite kontrol).
recommendations 2-5 madde, avoid_actions 1-3 madde. Somut ürün adı ve yüzde kullan."""


def generate_returns_insights() -> dict:
    stats = compute_returns_stats()
    if stats["total_returns"] == 0:
        return {
            "summary": "Henüz iade kaydı yok. İade CSV'si yükleyince analiz oluşturulur.",
            "top_reason": "—", "top_reason_detail": "—",
            "product_actions": [], "recommendations": ["İade CSV şablonunu doldurup yükleyin (product_name, sku, return_reason, return_date, quantity)."],
            "avoid_actions": [], "source": "fallback", "stats": stats,
        }

    reasons = stats["reason_breakdown"]
    top_reason = next(iter(reasons), "—")

    if not ai_available():
        top_qty = reasons.get(top_reason, 0)
        pct = round(100 * top_qty / max(stats["return_quantity"], 1))
        result = {
            "summary": (
                f"Toplam {stats['total_returns']} iade kaydı ({stats['return_quantity']} adet). "
                f"En sık sebep: \"{top_reason}\" (%{pct})."
            ),
            "top_reason": top_reason,
            "top_reason_detail": f'"{top_reason}" sebebi {top_qty} adet iadede geçiyor.',
            "product_actions": [
                f"{p['product_name']}: {p['quantity']} iade — sebep \"{p['top_reason']}\" için ürün sayfasını gözden geçirin."
                for p in stats["products"][:3]
            ],
            "recommendations": [
                "En sık iade sebebini ürün sayfası görselleri ve açıklamayla eşleştirin.",
                "Beden/kalite iadesi yüksekse ölçü tablosu ve kalite kontrolünü sıkılaştırın.",
            ],
            "avoid_actions": ["İade oranı yüksek ürünlere reklam bütçesi ayırmayın; önce kök nedeni çözün."],
            "source": "fallback",
        }
    else:
        reviews = load_reviews()
        iade_reviews = [
            {"product": r["product_name"], "text": r["comment_text"][:250]}
            for r in reviews if r.get("complaint_category") in ("iade", "beden", "kalite")
        ][:40]
        payload = {
            "metrics": stats,
            "related_reviews": iade_reviews,
        }
        try:
            data = _chat_json(RETURNS_INSIGHTS_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str))
            result = {
                "summary": str(data.get("summary", "")).strip(),
                "top_reason": str(data.get("top_reason", top_reason)).strip(),
                "top_reason_detail": str(data.get("top_reason_detail", "")).strip(),
                "product_actions": _norm_list(data.get("product_actions"), 5),
                "recommendations": _norm_list(data.get("recommendations"), 5),
                "avoid_actions": _norm_list(data.get("avoid_actions"), 3),
                "source": "gemini",
            }
            if not result["summary"]:
                raise ValueError("boş özet")
        except Exception:
            logger.exception("İade analizi Gemini hatası, fallback")
            return generate_returns_insights_fallback(stats, top_reason)
    result["stats"] = stats
    return result


def generate_returns_insights_fallback(stats: dict, top_reason: str) -> dict:
    top_qty = stats["reason_breakdown"].get(top_reason, 0)
    pct = round(100 * top_qty / max(stats["return_quantity"], 1))
    return {
        "summary": (
            f"Toplam {stats['total_returns']} iade kaydı ({stats['return_quantity']} adet). "
            f"En sık sebep: \"{top_reason}\" (%{pct})."
        ),
        "top_reason": top_reason,
        "top_reason_detail": f'"{top_reason}" sebebi {top_qty} adet iadede geçiyor.',
        "product_actions": [
            f"{p['product_name']}: {p['quantity']} iade — \"{p['top_reason']}\" sebebi için sayfayı gözden geçirin."
            for p in stats["products"][:3]
        ],
        "recommendations": [
            "En sık iade sebebini ürün sayfası ve beden tablosuyla karşılaştırın.",
            "İade trendini haftalık takip edin.",
        ],
        "avoid_actions": ["İade oranı yüksek ürünleri kampanyayla öne çıkarmayın."],
        "source": "fallback",
        "stats": stats,
    }


# --------------------------------------------------------- Kargo (shipments)

VALID_SHIPMENT_STATUS = {"shipped", "in_transit", "delivered"}
STATUS_LABELS = {"shipped": "Kargoya verildi", "in_transit": "Yolda", "delivered": "Teslim edildi"}


def load_shipments() -> list[dict]:
    if _shipments_file().exists():
        return json.loads(_shipments_file().read_text(encoding="utf-8"))
    return []


def import_shipments_csv(raw: bytes) -> dict:
    """CSV formatı: order_id,product_name,sku,carrier,tracking_number,status,shipped_date,delivered_date"""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"order_id", "product_name", "status"}
    if not reader.fieldnames or not required.issubset({(f or "").strip() for f in reader.fieldnames}):
        raise ValueError(
            "CSV başlıkları eksik. Beklenen sütunlar: order_id, product_name, sku, carrier, "
            "tracking_number, status, shipped_date, delivered_date"
        )
    shipments = load_shipments()
    by_order = {s["order_id"]: s for s in shipments}
    added, updated, delivered_new = 0, 0, 0
    for row in reader:
        order_id = (row.get("order_id") or "").strip()
        status = (row.get("status") or "").strip().lower()
        if not order_id or status not in VALID_SHIPMENT_STATUS:
            continue
        entry = {
            "order_id": order_id,
            "product_name": (row.get("product_name") or "").strip() or "Bilinmeyen ürün",
            "sku": (row.get("sku") or "").strip(),
            "carrier": (row.get("carrier") or "").strip(),
            "tracking_number": (row.get("tracking_number") or "").strip(),
            "status": status,
            "shipped_date": (row.get("shipped_date") or "").strip(),
            "delivered_date": (row.get("delivered_date") or "").strip(),
        }
        old = by_order.get(order_id)
        if old:
            if old["status"] != "delivered" and status == "delivered":
                delivered_new += 1
            old.update(entry)
            updated += 1
        else:
            if status == "delivered":
                delivered_new += 1
            by_order[order_id] = entry
            added += 1
    if added == 0 and updated == 0:
        raise ValueError("CSV'den hiç kargo kaydı okunamadı — dosya formatını kontrol edin.")
    shipments = list(by_order.values())
    _shipments_file().write_text(json.dumps(shipments, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"added": added, "updated": updated, "new_deliveries": delivered_new, "total": len(shipments)}


def clear_shipments() -> None:
    if _shipments_file().exists():
        _shipments_file().unlink()


def compute_shipments_stats(status_filter: Optional[str] = None) -> dict:
    shipments = load_shipments()
    total = len(shipments)
    in_transit = [s for s in shipments if s["status"] in ("shipped", "in_transit")]
    delivered = [s for s in shipments if s["status"] == "delivered"]

    by_carrier: dict[str, dict] = {}
    for s in shipments:
        carrier = s.get("carrier") or "Bilinmiyor"
        c = by_carrier.setdefault(carrier, {"carrier": carrier, "total": 0, "delivered": 0, "in_transit": 0})
        c["total"] += 1
        if s["status"] == "delivered":
            c["delivered"] += 1
        else:
            c["in_transit"] += 1

    listed = shipments
    if status_filter == "active":
        listed = in_transit
    elif status_filter in VALID_SHIPMENT_STATUS:
        listed = [s for s in shipments if s["status"] == status_filter]
    listed = sorted(listed, key=lambda s: s.get("shipped_date") or "", reverse=True)

    # Kargo yorumlarıyla çapraz bakış
    reviews = load_reviews()
    cargo_complaints = sum(1 for r in reviews if r.get("complaint_category") == "kargo")

    return {
        "total": total,
        "in_transit": len(in_transit),
        "delivered": len(delivered),
        "carriers": sorted(by_carrier.values(), key=lambda x: -x["total"]),
        "cargo_complaint_reviews": cargo_complaints,
        "shipments": listed[:100],
        "status_labels": STATUS_LABELS,
    }


def compute_stats(reviews: list[dict], platform: str | None = None) -> dict:
    if platform:
        reviews = [r for r in reviews if _review_matches_platform(r, platform)]
    total = len(reviews)
    negative = sum(1 for r in reviews if r["sentiment"] == "negative")
    positive = sum(1 for r in reviews if r["sentiment"] == "positive")
    avg = round(sum(r["rating"] for r in reviews) / total, 2) if total else 0
    breakdown: dict[str, int] = {}
    for r in reviews:
        if r["sentiment"] == "negative":
            cat = r.get("complaint_category") or "diğer"
            breakdown[cat] = breakdown.get(cat, 0) + 1

    listing_meta = load_listing_meta()
    returns = load_returns()
    shipments = load_shipments()
    return_by_sku: dict[str, dict] = {}
    for ret in returns:
        key = ret.get("sku") or ret["product_name"]
        entry = return_by_sku.setdefault(key, {"count": 0, "qty": 0})
        entry["count"] += 1
        entry["qty"] += ret.get("quantity", 1)
    ship_by_sku: dict[str, dict] = {}
    for sh in shipments:
        key = sh.get("sku") or sh["product_name"]
        entry = ship_by_sku.setdefault(key, {"total": 0, "in_transit": 0, "delivered": 0})
        entry["total"] += 1
        if sh["status"] in ("shipped", "in_transit"):
            entry["in_transit"] += 1
        elif sh["status"] == "delivered":
            entry["delivered"] += 1

    by_product: dict[str, dict] = {}
    for r in reviews:
        key = r.get("sku") or r["product_name"]
        meta = listing_meta.get((r.get("sku") or "").upper(), {})
        p = by_product.setdefault(key, {
            "product_name": r["product_name"], "sku": r.get("sku", ""),
            "total_reviews": 0, "negative_reviews": 0, "positive_reviews": 0,
            "commented_reviews": 0, "rating_sum": 0,
            "topic_breakdown": {}, "negative_samples": [], "positive_samples": [],
            "image_url": meta.get("image_url"), "source_url": meta.get("source_url"),
        })
        p["total_reviews"] += 1
        p["rating_sum"] += r["rating"]
        text = (r.get("comment_text") or "").strip()
        if text:
            p["commented_reviews"] += 1
        if r["sentiment"] == "negative":
            p["negative_reviews"] += 1
            cat = r.get("complaint_category") or "diğer"
            p["topic_breakdown"][cat] = p["topic_breakdown"].get(cat, 0) + 1
            if len(p["negative_samples"]) < 12 and text:
                p["negative_samples"].append({"category": cat, "text": text[:4000], "rating": r["rating"]})
        else:
            p["positive_reviews"] += 1
            if len(p["positive_samples"]) < 12 and text:
                p["positive_samples"].append({"text": text[:4000], "rating": r["rating"], "category": None})

    products = []
    for p in by_product.values():
        t = p.pop("rating_sum")
        p["average_rating"] = round(t / p["total_reviews"], 2)
        p["negative_pct"] = round(100 * p["negative_reviews"] / p["total_reviews"])
        p["positive_pct"] = round(100 * p["positive_reviews"] / p["total_reviews"])
        p["rating_only_reviews"] = max(0, p["total_reviews"] - p["commented_reviews"])
        p["neutral_reviews"] = max(0, p["total_reviews"] - p["positive_reviews"] - p["negative_reviews"])
        tb = {k: v for k, v in p["topic_breakdown"].items() if k not in GENERIC_CATEGORIES}
        p["topic_breakdown"] = dict(sorted(tb.items(), key=lambda x: -x[1]))
        if tb:
            top = max(tb, key=tb.get)
            p["top_complaint"] = top
            p["top_complaint_count"] = tb[top]
        else:
            neg_texts = [s["text"] for s in p["negative_samples"]]
            inferred = _pick_top_complaint({}, neg_texts)
            p["top_complaint"] = inferred[0]
            p["top_complaint_count"] = inferred[1]
        sku_key = p.get("sku") or p["product_name"]
        p["returns"] = return_by_sku.get(sku_key, {"count": 0, "qty": 0})
        p["shipments"] = ship_by_sku.get(sku_key, {"total": 0, "in_transit": 0, "delivered": 0})
        if platform and not product_belongs_to_platform(
            p.get("sku", ""), p.get("source_url"), platform, p.get("platform"),
        ):
            continue
        products.append(p)
    products.sort(key=lambda x: -x["total_reviews"])

    top_problem = sorted(
        [p for p in products if p["negative_reviews"] > 0],
        key=lambda x: -x["negative_reviews"],
    )[:5]

    filtered_total = sum(p["total_reviews"] for p in products)
    filtered_negative = sum(p["negative_reviews"] for p in products)
    filtered_positive = sum(p["positive_reviews"] for p in products)

    return {
        "total_reviews": filtered_total,
        "negative_reviews": filtered_negative,
        "positive_reviews": filtered_positive,
        "average_rating": avg,
        "complaint_breakdown": breakdown,
        "products": products,
        "total_products": len(products),
        "top_problem_products": [
            {"product_name": p["product_name"], "sku": p["sku"], "negative_count": p["negative_reviews"]}
            for p in top_problem
        ],
        "platform": platform or load_settings().get("platform", "trendyol"),
        "total_returns": len(returns),
        "return_quantity": sum(r.get("quantity", 1) for r in returns),
    }


def _pick_top_complaint(counts_by_cat: dict[str, int], neg_sample_texts: list[str]) -> tuple[str | None, int]:
    specific = {k: v for k, v in counts_by_cat.items() if k not in GENERIC_CATEGORIES and v > 0}
    if specific:
        best = max(specific, key=specific.get)
        return best, specific[best]
    inferred: dict[str, int] = {}
    for text in neg_sample_texts:
        topic = detect_complaint_topic(text)
        if topic not in GENERIC_CATEGORIES:
            inferred[topic] = inferred.get(topic, 0) + 1
    if inferred:
        best = max(inferred, key=inferred.get)
        return best, inferred[best]
    if counts_by_cat:
        best = max(counts_by_cat, key=counts_by_cat.get)
        return best, counts_by_cat[best]
    return None, 0


def get_product_summaries(platform: str | None = None) -> dict:
    backfill_missing_listing_images()
    if platform == "all":
        stats = compute_stats(load_reviews(), None)
        return {"products": stats["products"], "total_products": stats["total_products"], "platform": "all"}
    platform = platform or load_settings().get("platform", "trendyol")
    stats = compute_stats(load_reviews(), platform)
    return {"products": stats["products"], "total_products": stats["total_products"], "platform": platform}


# ------------------------------------------------------------------- Insights

def _fallback_insights(stats: dict) -> dict:
    total = stats["total_reviews"]
    if total == 0:
        return {
            "general_summary": "Henüz yorum verisi yok. Yorumlar sekmesinden link veya CSV ile veri ekleyebilirsiniz.",
            "main_complaint_topic": "—", "main_complaint_detail": "—",
            "product_highlights": [], "cargo_guide": [], "market_guide": [],
            "recommendations": ["Yorumlar sekmesinden ürün linki ile çekin veya örnek CSV yükleyin."],
            "avoid_actions": [], "source": "fallback",
        }
    negative = stats["negative_reviews"]
    breakdown = stats.get("complaint_breakdown") or {}
    neg_pct = round(100 * negative / total)
    top_cat, top_count = ("diğer", 0)
    if breakdown:
        top_cat, top_count = max(breakdown.items(), key=lambda x: x[1])
    label = TOPIC_LABELS.get(top_cat, top_cat)

    products = stats.get("top_problem_products") or []
    hint = f" En çok sorun yaşanan ürün: {products[0]['product_name']}." if products else ""
    recs = [
        f"'{label}' şikayetlerini öncelikli ele alın.",
        "Olumsuz yorum alan ürünleri stok ve kalite kontrolünden geçirin.",
    ]
    if stats["average_rating"] < 4:
        recs.append("Ürün tanımı ile müşteri beklentisini hizalayın; fotoğraf ve ölçü tablosunu güncelleyin.")
    avoid = ["Olumsuz yorum oranı yüksek ürünleri öne çıkaran kampanya açmayın."]
    highlights = [
        f"{p['product_name']}: {p['negative_count']} olumsuz yorum — riskli ürün."
        for p in products[:3]
    ]
    return {
        "general_summary": (
            f"Toplam {total} yorum incelendi; {negative} tanesi olumsuz (%{neg_pct}). "
            f"Ortalama puan {stats['average_rating']}.{hint}"
        ),
        "main_complaint_topic": label,
        "main_complaint_detail": f"Olumsuz yorumların {top_count} tanesinde '{label}' konusu öne çıkıyor.",
        "product_highlights": highlights,
        "cargo_guide": build_cargo_guide(stats),
        "market_guide": build_market_guide(stats),
        "recommendations": recs[:5],
        "avoid_actions": avoid,
        "source": "fallback",
    }


def _norm_list(value, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(x).strip() for x in value if str(x).strip()][:limit]


def generate_store_insights(force: bool = False) -> dict:
    reviews = load_reviews()
    platform = load_settings().get("platform", "trendyol")
    stats = compute_stats(reviews, None)
    if not force and _insights_file().exists():
        cached = json.loads(_insights_file().read_text(encoding="utf-8"))
        cached["cached"] = True
        return cached

    if stats["total_reviews"] == 0 or not ai_available():
        result = _fallback_insights(stats)
    else:
        payload = {
            "metrics": {
                "total_reviews": stats["total_reviews"],
                "negative_reviews": stats["negative_reviews"],
                "average_rating": stats["average_rating"],
                "complaint_breakdown": stats["complaint_breakdown"],
                "top_problem_products": stats["top_problem_products"],
            },
            "products": [
                {k: p[k] for k in ("product_name", "sku", "total_reviews", "negative_reviews",
                                   "average_rating", "top_complaint")}
                for p in stats["products"][:20]
            ],
            "platform": platform,
            "carrier_reference": TRENDYOL_CARRIERS,
            "reviews": [
                {"product": r["product_name"], "rating": r["rating"], "text": r["comment_text"][:300]}
                for r in reviews[:150]
            ],
        }
        try:
            data = _chat_json(INSIGHTS_SYSTEM, json.dumps(payload, ensure_ascii=False))
            result = {
                "general_summary": str(data.get("general_summary", "")).strip(),
                "main_complaint_topic": str(data.get("main_complaint_topic", "—")).strip(),
                "main_complaint_detail": str(data.get("main_complaint_detail", "")).strip(),
                "product_highlights": _norm_list(data.get("product_highlights"), 4),
                "cargo_guide": _norm_list(data.get("cargo_guide")),
                "market_guide": _norm_list(data.get("market_guide"), 5),
                "recommendations": _norm_list(data.get("recommendations"), 5),
                "avoid_actions": _norm_list(data.get("avoid_actions"), 5),
                "source": "gemini",
            }
            if not result["general_summary"] or not result["recommendations"]:
                result = _fallback_insights(stats)
            if not result["cargo_guide"]:
                result["cargo_guide"] = build_cargo_guide(stats)
            if not result["market_guide"]:
                result["market_guide"] = build_market_guide(stats)
        except Exception:
            logger.exception("Gemini mağaza analizi başarısız, fallback kullanılıyor")
            result = _fallback_insights(stats)

    result["stats"] = {
        "total_reviews": stats["total_reviews"],
        "negative_reviews": stats["negative_reviews"],
        "positive_reviews": stats["positive_reviews"],
        "average_rating": stats["average_rating"],
        "complaint_breakdown": stats["complaint_breakdown"],
    }
    result["cached"] = False
    _insights_file().write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


# --------------------------------------------------- Danışman hazır soruları
# (otomasyonv3 consultant_presets.py'den taşındı)

PRODUCT_PRESET_GROUPS: list[dict] = [
    {
        "id": "reviews", "title": "Yorum",
        "description": "Bu ürünün yorum analizi",
        "questions": [
            {"id": "reviews-complaint-top", "label": "En çok hangi konuda şikayet var?", "question": "Yorumlarıma göre en çok hangi konuda şikayet var?"},
            {"id": "reviews-negative-summary", "label": "Olumsuz yorumların özeti", "question": "Olumsuz yorumların özetini çıkar, en sık tekrar eden şikayetleri söyle."},
            {"id": "reviews-sentiment-ratio", "label": "Olumlu / olumsuz oranımız", "question": "Mağazamın olumlu ve olumsuz yorum oranı nasıl?"},
            {"id": "reviews-positive-themes", "label": "Müşteriler neyi beğeniyor?", "question": "Olumlu yorumlarda müşteriler en çok neyi övüyor?"},
            {"id": "reviews-compare", "label": "Benzer ürünlerle karşılaştır", "question": "En çok yorumu olan ürünümü benzer ürünlerle karşılaştır."},
        ],
    },
    {
        "id": "returns", "title": "İade",
        "description": "İade kayıtları ve iade yorumları",
        "questions": [
            {"id": "returns-status", "label": "İade durumu özeti", "question": "İade oranımız ve iade kayıtlarımız ne durumda?"},
            {"id": "returns-top-reason", "label": "En sık iade sebebi", "question": "En sık görülen iade sebebi nedir?"},
            {"id": "returns-review-themes", "label": "İade yorumları ne diyor?", "question": "Yorumlarda iade ile ilgili şikayetler ne söylüyor?"},
            {"id": "returns-reduce-advice", "label": "İadeyi azaltmak için ne yapmalıyım?", "question": "İade oranını düşürmek için yorum verilerine göre ne önerirsin?"},
        ],
    },
    {
        "id": "cargo", "title": "Kargo",
        "description": "Teslimat ve kargo yorumları",
        "questions": [
            {"id": "cargo-has-complaints", "label": "Kargo şikayeti var mı?", "question": "Yorumlarımda kargo veya teslimat şikayeti var mı, ne kadar?"},
            {"id": "cargo-delivery-summary", "label": "Teslimat yorumları özeti", "question": "Kargo ve teslimat konusunda müşteriler ne diyor?"},
            {"id": "cargo-delay-reviews", "label": "Geciken kargo yorumları", "question": "Kargo gecikmesi veya geç teslimat ile ilgili yorumları özetle."},
            {"id": "cargo-reduce-advice", "label": "Kargo için ne yapmalıyım?", "question": "Kargo şikayetlerini azaltmak için ne önerirsin?"},
        ],
    },
]

STORE_PRESET_GROUPS: list[dict] = [
    {
        "id": "compare", "title": "Karşılaştırma",
        "description": "Kendi ürünlerinizi birbirleriyle kıyaslayın",
        "questions": [
            {"id": "store-best-product", "label": "En iyi performans hangi üründe?", "question": "Mağazamdaki ürünleri karşılaştır: yorum, puan ve olumlu oranına göre şu an en iyi performans gösteren hangisi? Neden?"},
            {"id": "reviews-worst-product", "label": "Hangi ürün en problemli?", "question": "Mağazamdaki tüm ürünleri incele: en problemli ve en iyi performans gösteren hangisi? Nedenlerini açıkla ve iyi üründe yaptığımızı diğer ürünlere nasıl aktarabileceğimizi öner."},
            {"id": "store-most-returns", "label": "En çok iade alan ürün?", "question": "Ürünlerimi karşılaştır: en çok iade alan hangisi, diğerlerine göre farkı ne?"},
            {"id": "store-review-volume", "label": "En çok yorum alan ürün?", "question": "Hangi ürünüm en çok değerlendirme ve yorum almış? Ürünleri hacme göre karşılaştır."},
            {"id": "store-rating-leader", "label": "En yüksek puanlı ürün?", "question": "Ürünlerimi ortalama puana göre karşılaştır: en yüksek müşteri puanını hangisi alıyor?"},
        ],
    },
    {
        "id": "reviews", "title": "Yorum",
        "description": "Genel mağaza yorum özeti",
        "questions": [
            {"id": "reviews-sentiment-ratio", "label": "Olumlu / olumsuz oranımız", "question": "Mağazamın olumlu ve olumsuz yorum oranı nasıl?"},
            {"id": "reviews-positive-themes", "label": "Müşteriler neyi beğeniyor?", "question": "Olumlu yorumlarda müşteriler en çok neyi övüyor?"},
        ],
    },
    {
        "id": "returns", "title": "İade",
        "description": "Mağaza geneli iade analizi",
        "questions": [
            {"id": "returns-status", "label": "İade durumu özeti", "question": "İade oranımız ve iade kayıtlarımız ne durumda?"},
            {"id": "returns-top-reason", "label": "En sık iade sebebi", "question": "En sık görülen iade sebebi nedir?"},
            {"id": "returns-product-rank", "label": "Hangi üründe iade fazla?", "question": "Hangi ürünlerde iade daha fazla görünüyor?"},
            {"id": "returns-reduce-advice", "label": "İadeyi azaltmak için ne yapmalıyım?", "question": "İade oranını düşürmek için yorum verilerine göre ne önerirsin?"},
        ],
    },
    {
        "id": "cargo", "title": "Kargo",
        "description": "Mağaza geneli teslimat analizi",
        "questions": [
            {"id": "cargo-has-complaints", "label": "Kargo şikayeti var mı?", "question": "Yorumlarımda kargo veya teslimat şikayeti var mı, ne kadar?"},
            {"id": "cargo-delivery-summary", "label": "Teslimat yorumları özeti", "question": "Kargo ve teslimat konusunda müşteriler ne diyor?"},
            {"id": "cargo-product-rank", "label": "Kargo sorunu olan ürünler", "question": "Hangi ürünlerde kargo şikayeti öne çıkıyor?"},
            {"id": "cargo-reduce-advice", "label": "Kargo için ne yapmalıyım?", "question": "Kargo şikayetlerini azaltmak için ne önerirsin?"},
        ],
    },
]


def _tailor_preset_question(question: str, product_name: str) -> str:
    return (
        question
        .replace("Mağazamın", f"{product_name} ürününün")
        .replace("Yorumlarıma", f"{product_name} yorumlarına")
        .replace("Yorumlarımda", f"{product_name} yorumlarında")
        .replace("Mağazamdaki ürünleri karşılaştır", f"{product_name} ürününü mağaza ortalamasıyla karşılaştır")
        .replace("Ürünlerimi karşılaştır", f"{product_name} ürününü diğer ürünlerle karşılaştır")
        .replace("Hangi ürünüm en çok", f"{product_name} en çok")
        .replace("En çok yorumu olan ürünümü benzer ürünlerle karşılaştır.", f"{product_name} ürününü benzer ürünlerle karşılaştır.")
    )


def get_consultant_presets(product_name: str | None = None) -> list[dict]:
    groups = PRODUCT_PRESET_GROUPS if product_name else STORE_PRESET_GROUPS
    if not product_name:
        return groups
    tailored = []
    for group in groups:
        tailored.append({
            **group,
            "description": f"{product_name} için — {group['description'].lower()}",
            "questions": [
                {**q, "question": _tailor_preset_question(q["question"], product_name)}
                for q in group["questions"]
            ],
        })
    return tailored


# ------------------------------------------------------------------ Soru-cevap

_SMALL_TALK = re.compile(
    r"^(nasılsın|nasilsin|naber|ne haber|merhaba|selam|günaydın|gunaydin|iyi günler|"
    r"iyi akşamlar|teşekkür|tesekkur|sağol|sagol|hello|hi)\b|kimsin",
    re.I,
)
_NEGATIVE_KEYWORDS = ("olumsuz", "negatif", "kötü", "şikayet", "sikayet")


def _is_gibberish(q: str) -> bool:
    alpha = re.sub(r"[^a-zçğıöşüA-ZÇĞİÖŞÜ]", "", q)
    return len(alpha) <= 2


def _small_talk_response(q: str) -> dict:
    ql = q.lower()
    if any(w in ql for w in ("teşekkür", "tesekkur", "sağol", "sagol")):
        answer = "Rica ederim! Başka bir ürün veya yorum konusunda yardımcı olabilirim."
    elif "kimsin" in ql:
        answer = ("Ben AnalizAi mağaza asistanınızım — yüklediğiniz yorum verilerine bakarak "
                  "size somut öneriler veririm.")
    elif any(w in ql for w in ("nasılsın", "nasilsin", "naber", "ne haber")):
        answer = "İyiyim, teşekkür ederim — siz nasılsınız? İsterseniz bir ürün adı yazın, yorumlara birlikte bakalım."
    else:
        answer = 'Merhaba! Nasıl yardımcı olabilirim? Örneğin: "Bluetooth kulaklık hakkında olumsuz yorumları göster".'
    return {"answer": answer, "recommendations": [], "evidence": [], "sorted_complaints": [], "source": "validation"}


def _fallback_answer(
    question: str,
    stats: dict,
    returns_stats: dict | None = None,
    shipment_stats: dict | None = None,
) -> dict:
    total = stats.get("total_reviews", 0)
    returns_stats = returns_stats or compute_returns_stats()
    shipment_stats = shipment_stats or compute_shipments_stats()

    if total == 0 and returns_stats["total_returns"] == 0 and shipment_stats["total"] == 0:
        return {
            "answer": (
                "Henüz mağaza verisi yok. Yorumlar sekmesinden ürün linki ile yorum çekebilir "
                "veya CSV yükleyebilirsiniz — panel açıldığında örnek veri otomatik yüklenir."
            ),
            "recommendations": [
                "Sayfayı yenileyip tekrar deneyin (örnek veri otomatik yüklenir)",
                "Yorumlar → Link ile çek veya kendi CSV'nizi yükleyin",
            ],
            "evidence": [], "sorted_complaints": [], "source": "fallback",
        }

    if total == 0 and returns_stats["total_returns"] > 0:
        top_reason = max(returns_stats["reason_breakdown"].items(), key=lambda x: x[1])[0] if returns_stats["reason_breakdown"] else "genel"
        return {
            "answer": (
                f"Yorum verisi yok; ancak {returns_stats['total_returns']} iade kaydı var "
                f"({returns_stats['return_quantity']} adet). En sık iade sebebi: {top_reason}."
            ),
            "recommendations": [
                f"'{top_reason}' kaynaklı iadeleri azaltmak için ürün açıklaması ve kalite kontrolünü gözden geçirin.",
                "Yorum verisi ekleyerek daha detaylı analiz alabilirsiniz.",
            ],
            "evidence": [f"{returns_stats['total_returns']} iade kaydı"],
            "sorted_complaints": [], "source": "fallback",
        }

    if total == 0 and shipment_stats["total"] > 0:
        return {
            "answer": (
                f"Yorum verisi yok; ancak {shipment_stats['total']} kargo kaydı mevcut "
                f"({shipment_stats['in_transit']} yolda, {shipment_stats['delivered']} teslim)."
            ),
            "recommendations": [
                "Kargo sürelerini kısaltmak müşteri memnuniyetini artırır.",
                "Yorum verisi ekleyerek şikayet temalarını daha net görebilirsiniz.",
            ],
            "evidence": [f"{shipment_stats['total']} kargo kaydı"],
            "sorted_complaints": [], "source": "fallback",
        }

    breakdown = stats.get("complaint_breakdown") or {}
    top = max(breakdown.items(), key=lambda x: x[1])[0] if breakdown else "genel"
    label = TOPIC_LABELS.get(top, top)
    return {
        "answer": (
            f"Mevcut verilere göre {total} yorum var; "
            f"%{round(100 * stats['negative_reviews'] / total) if total else 0} olumsuz. "
            f"En baskın tema: {label}."
        ),
        "recommendations": [f"'{label}' konusuna öncelik verin."],
        "evidence": [f"{stats['negative_reviews']} olumsuz yorum"],
        "sorted_complaints": [f"{TOPIC_LABELS.get(k, k)}: {v}" for k, v in list(breakdown.items())[:5]],
        "source": "fallback",
    }


def answer_store_question(question: str, sku: str | None = None) -> dict:
    question = question.strip()
    if _is_gibberish(question):
        return {
            "answer": ('Anlaşılmadı, sorunuzu tekrar alabilir miyim? '
                       'Örneğin: "Kargo şikayetlerinde ne yapmalıyım?"'),
            "recommendations": [], "evidence": [], "sorted_complaints": [], "source": "validation",
        }

    all_reviews = load_reviews()
    reviews = [r for r in all_reviews if r.get("sku") == sku] if sku else all_reviews
    stats = compute_stats(reviews, None)
    returns_stats = compute_returns_stats()
    shipment_stats = compute_shipments_stats()

    if _SMALL_TALK.search(question) and len(question) < 40:
        return _small_talk_response(question)

    has_data = (
        stats["total_reviews"] > 0
        or returns_stats["total_returns"] > 0
        or shipment_stats["total"] > 0
    )
    if not has_data:
        return _fallback_answer(question, stats, returns_stats, shipment_stats)

    if not ai_available():
        return _fallback_answer(question, stats, returns_stats, shipment_stats)

    negative_only = any(k in question.lower() for k in _NEGATIVE_KEYWORDS)
    payload = {
        "question": question,
        "negative_only_request": negative_only,
        "platform": load_settings().get("platform", "trendyol"),
        "store_metrics": {
            "total_reviews": stats["total_reviews"],
            "negative_reviews": stats["negative_reviews"],
            "average_rating": stats["average_rating"],
            "complaint_breakdown": stats["complaint_breakdown"],
        },
        "returns": {
            "total_returns": returns_stats["total_returns"],
            "return_quantity": returns_stats["return_quantity"],
            "reason_breakdown": returns_stats["reason_breakdown"],
            "top_products": returns_stats["products"][:5],
        },
        "shipments": {
            "total": shipment_stats["total"],
            "in_transit": shipment_stats["in_transit"],
            "delivered": shipment_stats["delivered"],
            "carriers": shipment_stats["carriers"][:5],
        },
        "products": [
            {k: p[k] for k in ("product_name", "sku", "total_reviews", "negative_reviews",
                               "positive_reviews", "average_rating", "top_complaint",
                               "negative_samples", "positive_samples")}
            for p in stats["products"][:20]
        ],
        "reviews_sample": [
            {"product": r["product_name"], "rating": r["rating"], "sentiment": r["sentiment"],
             "category": r["complaint_category"], "text": r["comment_text"][:250]}
            for r in reviews[:120]
        ],
    }
    try:
        data = _chat_json(ASK_SYSTEM, json.dumps(payload, ensure_ascii=False))
        result = {
            "answer": str(data.get("answer", "")).strip(),
            "recommendations": _norm_list(data.get("recommendations"), 3),
            "evidence": _norm_list(data.get("evidence"), 3),
            "sorted_complaints": _norm_list(data.get("sorted_complaints"), 10),
            "source": "gemini",
        }
        if not result["answer"]:
            return _fallback_answer(question, stats, returns_stats, shipment_stats)
        return result
    except Exception:
        logger.exception("Soru-cevap Gemini hatası")
        return _fallback_answer(question, stats, returns_stats, shipment_stats)


# --------------------------------------------------------------- Cevap önerisi

def suggest_reply_for_review(review: dict) -> dict:
    rating = int(review.get("rating", 3))
    sentiment = review.get("sentiment") or ("negative" if rating <= 3 else "positive")
    category = review.get("complaint_category") or detect_complaint_topic(review.get("comment_text", ""))

    if sentiment == "positive" or rating >= 4:
        fallback = {
            "suggested_reply": (
                f"Güzel yorumunuz için çok teşekkür ederiz! "
                f"{review.get('product_name', 'Ürünümüzü')} beğenmenize sevindik; "
                "tekrar aramızda görmekten mutluluk duyarız."
            ),
            "source": "fallback",
        }
    else:
        fallback = {
            "suggested_reply": _CATEGORY_REPLIES.get(category, _CATEGORY_REPLIES["diğer"]),
            "source": "fallback",
        }

    if not ai_available():
        return fallback

    payload = {
        "product_name": review.get("product_name", ""),
        "rating": rating,
        "comment_text": (review.get("comment_text") or "")[:2000],
        "sentiment": sentiment,
        "complaint_category": category,
    }
    try:
        data = _chat_json(REPLY_SYSTEM, json.dumps(payload, ensure_ascii=False))
        text = str(data.get("suggested_reply", "")).strip()
        if not text:
            return fallback
        return {"suggested_reply": text, "source": "gemini"}
    except Exception:
        logger.exception("Cevap önerisi Gemini hatası")
        return fallback
