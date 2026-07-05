# 🛒 TrendolAi — Çoklu Pazaryeri E-Ticaret SaaS Paneli

Üyelik sistemli, paket satın alınabilen, yapay zeka destekli pazaryeri satış asistanı.
Satıcılar hesap açar, paket seçer, mağazalarını bağlar ve sidebar'lı tek panelden yönetir:

**Operasyon modülleri** (`/api/mp/*` — tüm platformlardan normalize veri):
- **Sipariş yönetimi**: tüm pazaryerlerinden paketler tek listede, kargo takibiyle
- **Ürün & stok yönetimi**: panelden fiyat/stok güncelle → API ile mağazaya işlenir; **düşük stok alarmı** (eşik ayarlanabilir)
- **Müşteri soruları**: bekleyen sorular tek listede, **AI cevap önerisi** + tek tıkla gönderim
- **İade & tazmin**: talepleri onayla / itiraz et
- **Finans modülü**: hakediş, komisyon, kesinti, net kazanç (platform bazında)
- **Buybox Tracker**: rakip fiyat takibi + **AI dinamik fiyatlandırma** önerisi
- **Webhook yönetimi**: yeni sipariş bildirimlerini kendi sistemine ilet (Trendyol)

**AI araçları**:
- **AI hızlı ürün ekleme**: fotoğraf → başlık/açıklama/kategori/özellik/fiyat → Trendyol'a gönder
- **AI satış görselleri**: mankenli / kullanım senaryolu 4 profesyonel görsel
- **Yorum analizi**: CSV veya ürün linkinden yorum çekme, şikayet kırılımı, AI içgörüleri, cevap önerileri
- **Satış & kârlılık analitiği**: ciro, net kâr, marj, şehir/platform kırılımı, AI aksiyon önerileri

Arayüz: pazarop tarzı tanıtım sayfası, sidebar'lı dashboard, **dark mode** (tüm sayfalarda, kalıcı).

## Pazaryeri entegrasyonları (app/marketplace_api/)

| Platform | Gerçek API kapsamı |
|---|---|
| **Trendyol** | Ürünler, fiyat/stok, siparişler, iadeler (onay/itiraz), soru-cevap, hakediş, webhook — Satıcı API v2 |
| **Hepsiburada** | Listing (fiyat/stok) + OMS (sipariş/paket) |
| **N11** | REST: ürünler, fiyat/stok, siparişler |
| **Amazon TR** | SP-API + LWA: siparişler, fiyat/stok (Listings), finans (Finances), buybox (Product Pricing) |

Anahtar girilmeyen bağlantılar **DEMO modda** çalışır: panel gerçekçi örnek veriyle tam
fonksiyoneldir, yazma işlemleri simüle edilir. Anahtarlar girildiği anda aynı ekranlar
gerçek mağaza verisiyle çalışır.

## Kurulum

```bash
cd "TRENDYOL AGENT"
./run.sh            # sanal ortamı kurar, bağımlılıkları yükler, sunucuyu başlatır
```

Tarayıcıda **http://127.0.0.1:8000** aç → "Ücretsiz Hesap Aç" ile kayıt ol.

`.env` içine `GEMINI_API_KEY` eklemek AI özelliklerini canlıya alır
(https://aistudio.google.com — ücretsiz). Anahtar yokken AI akışları demo modda çalışır.

## SaaS mimarisi

| Katman | Ne yapar |
|---|---|
| **Üyelik** | E-posta + parola kayıt/giriş (PBKDF2), httpOnly çerezle 30 günlük oturum |
| **Paketler** | Ücretsiz ₺0 / Başlangıç ₺299 / Profesyonel ₺699 / Kurumsal ₺1999 (aylık) |
| **Limitler** | AI ilan, yorum çekme, içgörü, görsel üretimi ve mağaza sayısı paket bazlı sayaçla takip edilir (aylık sıfırlanır) |
| **Ödeme** | `PAYMENT_PROVIDER=demo` (varsayılan): kart Luhn kontrolünden geçer, gerçek para çekilmez. Canlıda iyzico/Stripe `app/billing.py → _charge_card` fonksiyonuna bağlanır |
| **Veri izolasyonu** | Her kullanıcının verisi `data/users/<id>/` altında ayrı tutulur; hesap/abonelik/bağlantılar SQLite'ta (`data/app.db`) |
| **Bağlantılar** | Kullanıcı başına pazaryeri hesapları; Trendyol anahtarları kategori API'siyle canlı doğrulanır |

## Sayfalar

- `/` — pazarop tarzı tanıtım: hero + animasyonlu sayaçlar, "Her Şey Tek Panelde", "AI Süper Güçleri", öne çıkanlar, 3 adım, fiyatlandırma, SSS
- `/auth` — giriş / kayıt
- `/account` — abonelik & kullanım, paket değiştirme (checkout), pazaryeri bağlantıları, fatura geçmişi
- `/panel` — sidebar'lı dashboard: Genel Bakış · Siparişler · Ürünler & Stok · Sorular · İade & Tazmin · Finans · AI Ürün Ekleme · Buybox & Fiyat · Yorum Analizi · Satış Analizi

## Trendyol'da canlıya geçiş

1. Satıcı Paneli → **Hesap Bilgileri → Entegrasyon Bilgileri**'nden `Satıcı ID`, `API Key`, `API Secret` al.
2. Panelde **Hesabım → Mağaza Bağla → Trendyol**'a gir — bağlantı otomatik doğrulanır.
3. Görsel hosting için `.env`'e `IMGBB_API_KEY` ekle (Trendyol herkese açık HTTPS URL ister).
4. Marka adının Trendyol marka listesinde kayıtlı olması gerekir.

`.env`'deki `TRENDYOL_*` anahtarları hâlâ desteklenir (tek kiracılı kullanım için yedek);
kullanıcının kendi bağlantısı varsa o önceliklidir.

## CSV formatları (örnekler `sample-data/` içinde)

- **Yorumlar**: `product_name,sku,rating,comment_text,review_date`
- **Siparişler**: `order_id,product_name,sku,quantity,unit_price,cost,commission_rate,order_date,city,status,platform`
- **İadeler**: `product_name,sku,return_reason,return_date,quantity`
- **Kargolar**: `order_id,product_name,sku,carrier,tracking_number,status,shipped_date,delivered_date`

## Proje yapısı

```
app/
  main.py             FastAPI uygulaması, auth middleware, listeleme/asistan uçları
  auth.py             Kayıt/giriş/oturum (/api/auth/*)
  billing.py          Paketler, limitler, checkout, faturalar (/api/billing/*)
  marketplaces.py     Pazaryeri bağlantıları (/api/connections/*)
  ops.py              Operasyon merkezi: sipariş/ürün/soru/iade/finans/buybox/webhook (/api/mp/*)
  analytics.py        Satış & kârlılık analitiği (/api/analytics/*)
  marketplace_api/    Platform istemcileri: trendyol_api, hepsiburada_api, n11_api, amazon_api (+demo veri)
  db.py               SQLite katmanı (users, sessions, subscriptions, payments, connections, usage)
  ai_agent.py         Gemini çağrıları: görsel analiz, fiyat, kategori, özellik
  ai_seller.py        Mağaza asistanı (yorum/iade/kargo analizi) — kullanıcı başına veri
  trendyol_client.py  Trendyol ürün oluşturma istemcisi (AI ilan akışı)
  review_scraper.py   Ürün linkinden yorum çekme
  static/             index (landing) · auth · account · panel — hepsi dark mode destekli
data/
  app.db              Hesap/abonelik/bağlantı veritabanı (git'e girmez)
  users/<id>/         Kullanıcı başına yorum/sipariş/iade/kargo/buybox verileri (git'e girmez)
```

## Bilinmesi gerekenler

- Gemini ücretsiz katman limitleri: ~10 istek/dk — yoğun kullanımda `GEMINI_MODEL=gemini-2.5-flash-lite` daha yüksek kotalıdır.
- Trendyol ürün gönderimi asenkrondur; batch durumu panelde gösterilir ve ürün Trendyol onayından sonra yayına girer.
- Demo ödeme test kartı: `4242 4242 4242 4242` (herhangi bir gelecek tarih + CVC).
