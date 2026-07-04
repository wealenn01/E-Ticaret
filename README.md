# 🛒 Trendyol Listeleme Agent'ı

Ürün fotoğrafını yükle → yapay zeka (Google Gemini, **ücretsiz API**) fotoğrafı analiz etsin,
başlık/açıklama/özellikleri yazsın, web'den (DuckDuckGo) fiyat araştırması yapsın, Trendyol
kategorisini eşleştirsin → sen kontrol edip onayla → ürün Trendyol Marketplace API ile
pazaryerine gönderilsin.

## Akış

```
Fotoğraf → [1. Görsel analiz] → [2. Fiyat araştırması (web search)]
        → [3. Kategori eşleştirme] → [4. Özellik doldurma]
        → Taslak (sen düzenler/onaylarsın) → Trendyol createProducts V2 → Batch takibi
```

## Kurulum

Tek komut (sanal ortamı kurar, bağımlılıkları yükler, sunucuyu başlatır):

```bash
cd "TRENDYOL AGENT"
./run.sh            # veya farklı port: ./run.sh 8080
```

Elle kurmak istersen:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # GEMINI_API_KEY'i doldur (https://aistudio.google.com — ücretsiz)
uvicorn app.main:app --reload
```

Sonra tarayıcıda **http://127.0.0.1:8000** aç.

## Modlar

| Mod | Ne zaman | Davranış |
|---|---|---|
| **DEMO (mock)** | `.env`'de Trendyol anahtarları boşsa | Tüm AI akışı gerçek çalışır; kategori listesi örnek veridir, "yayınla" adımı simüle edilir. |
| **stage** | `TRENDYOL_ENV=stage` + anahtarlar dolu | Trendyol test ortamına (stageapigw) gönderir. |
| **prod** | `TRENDYOL_ENV=prod` | Gerçek pazaryerine gönderir. Ürün, Trendyol onay sürecinden sonra yayına girer. |

## Gerçek moda geçiş — kontrol listesi

1. **Trendyol satıcı hesabı** aç ve onaylat.
2. Satıcı Paneli → **Hesap Bilgileri → Entegrasyon Bilgileri**'nden `Satıcı ID`, `API Key`, `API Secret` al, `.env`'e yaz.
3. **Görsel hosting:** Trendyol görselleri herkese açık HTTPS URL olarak ister (1200×1800 px önerilir).
   En kolayı: [imgbb.com](https://api.imgbb.com) ücretsiz API anahtarı alıp `IMGBB_API_KEY`'e yazmak.
   Kendi S3/CDN'in varsa `app/main.py` içindeki `_host_image` fonksiyonunu değiştir.
4. **Marka:** Gönderdiğin marka adının Trendyol marka listesinde kayıtlı olması gerekir
   (kendi markan varsa panelden marka başvurusu yap).
5. Önce `TRENDYOL_ENV=stage` ile test et, sonra `prod`'a geç.

## Satış görseli üretimi

Taslak ekranındaki **"🎨 4 Satış Görseli Oluştur"** butonu, ürün tipine göre 4 sahne planlar
(giyilebilir ürün → manken üzerinde, elde kullanılan ürün → elde kullanım, büyük ürün → yaşam
alanında vb.) ve Gemini görsel modeliyle (`gemini-2.5-flash-image`) üretir. Beğendiklerini
işaretlersen ilana ek görsel olarak gönderilir; beğenmediklerini tek tek yeniden üretebilirsin.
Görsel modeli ücretsiz katmanda sınırlı kotaya sahiptir; kota dolarsa hata mesajı gösterilir.

Kategori yanlış seçildiyse taslak ekranındaki kategori kutusuna yazarak arayıp değiştirebilirsin —
özellikler yeni kategoriye göre otomatik yeniden doldurulur.

## Bilinmesi gerekenler

- Ürün gönderimi asenkron çalışır: API bir `batchRequestId` döner, agent durumu birkaç kez
  yoklar ve `failureReasons` hatalarını arayüzde gösterir.
- Trendyol'a giden her ürün, Trendyol'un kendi **onay sürecinden** geçer — API "başarılı" dese
  bile yayına girmesi zaman alabilir.
- Fiyat önerisi web aramasına (DuckDuckGo) dayalı bir **tahmindir**; onay ekranında her zaman kontrol et.
- Gemini ücretsiz katman limitleri: ~10 istek/dk, 1500 istek/gün (fotoğraf başına 4 çağrı yapılır).
  Ücretsiz katmanda gönderilen veriler Google tarafından model geliştirmede kullanılabilir.
- Trendyol Product V1 API 10 Ağustos 2026'da kapanıyor; bu proje **V2** kullanır.

## Proje yapısı

```
app/
  main.py             FastAPI uygulaması (analyze / publish uçları)
  ai_agent.py         Gemini çağrıları: görsel analiz, fiyat, kategori, özellik
  trendyol_client.py  Trendyol Marketplace API istemcisi (mock/stage/prod)
  models.py           Pydantic şemaları
  static/index.html   Web arayüzü
uploads/              Yüklenen fotoğraflar
```
# trendyol_ai_panel
# trendyol_ai_panel
