/** otomasyonv3 productInsightBuilder + reviewsInsight + sellerModel — JS port */
(function (global) {
  const COMPLAINT_LABELS = {
    kargo: "Kargo", beden: "Beden uyuşmazlığı", kalite: "Ürün kalitesi",
    paketleme: "Paketleme", yanlis_urun: "Yanlış ürün", eksik_urun: "Eksik / parça eksik",
    iade: "İade talebi", urun_bilgisi: "Ürün bilgisi / etiket", fiyat: "Fiyat beklentisi",
    diğer: "Diğer", genel_memnuniyetsizlik: "Diğer",
  };
  const GENERIC = new Set(["diğer", "genel_memnuniyetsizlik"]);
  const TEXT_TOPIC_RULES = [
    ["yanlis_urun", ["yanlış ürün", "yanlis urun", "farklı ürün", "farkli urun", "yanlış geldi"]],
    ["eksik_urun", ["eksik", "parça yok", "parca yok", "set eksik"]],
    ["beden", ["beden", "numara", "küçük geldi", "kucuk geldi", "büyük geldi", "dar geldi", "uyumsuz"]],
    ["kargo", ["kargo", "gecik", "teslimat", "kurye", "geç geldi", "gec geldi"]],
    ["paketleme", ["ambalaj", "özensiz paket", "ezilmiş kutu"]],
    ["kalite", ["kalite", "defo", "bozuk", "kırık", "kirik", "çalışmıyor", "calismiyor"]],
    ["iade", ["iade", "geri gönder", "değişim", "degisim"]],
    ["urun_bilgisi", ["üretim yılı", "etiket", "fotoğraftaki", "fotograftaki"]],
    ["fiyat", ["fiyat", "pahalı", "pahali", "değmez", "degmez"]],
  ];

  function inferComplaintFromText(text) {
    const lower = (text || "").toLowerCase();
    for (const [topic, words] of TEXT_TOPIC_RULES) {
      if (words.some(w => lower.includes(w))) return topic;
    }
    return null;
  }

  function formatComplaintLabel(key) {
    if (!key) return "";
    const n = key.trim().toLowerCase();
    if (COMPLAINT_LABELS[n]) return COMPLAINT_LABELS[n];
    return key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  function resolveProductTopTopic(product) {
    const code = product.top_complaint;
    if (code && !GENERIC.has(code)) return formatComplaintLabel(code);
    const sample = pickTopNegativeSample(product);
    if (sample?.category && !GENERIC.has(sample.category)) return formatComplaintLabel(sample.category);
    if (sample?.text) {
      const inferred = inferComplaintFromText(sample.text);
      if (inferred) return formatComplaintLabel(inferred);
    }
    return null;
  }

  function pickTopNegativeSample(product) {
    const samples = product.negative_samples || [];
    if (!samples.length) return null;
    const topicCode = product.top_complaint;
    if (topicCode && !GENERIC.has(topicCode)) {
      const matched = samples.find(s => s.category === topicCode);
      if (matched) return matched;
    }
    for (const s of samples) {
      if (s.category && !GENERIC.has(s.category)) return s;
      const inferred = inferComplaintFromText(s.text);
      if (inferred) return { ...s, category: inferred };
    }
    return samples[0];
  }

  function ratingToSatisfactionPct(rating) {
    const clamped = Math.min(5, Math.max(0, rating));
    return Math.round((clamped / 5) * 100);
  }

  function truncateQuote(text, max = 90) {
    const t = (text || "").trim();
    return t.length <= max ? t : t.slice(0, max - 1) + "…";
  }

  function topicLabel(code) {
    if (!code || GENERIC.has(code)) return "";
    return formatComplaintLabel(code);
  }

  function samplesForTopic(product, topic) {
    const negatives = product.negative_samples || [];
    if (!negatives.length) return [];
    const matched = negatives.filter(s => {
      if (topic && s.category === topic) return true;
      if (topic && inferComplaintFromText(s.text) === topic) return true;
      return false;
    });
    const pool = matched.length ? matched : negatives;
    return pool.slice(0, 5).map(s => ({ text: s.text, rating: s.rating }));
  }

  function positiveSamples(product) {
    const pool = (product.positive_samples || []).filter(s => s.text?.trim());
    const scored = pool.map(s => {
      const low = s.text.toLowerCase();
      let score = s.rating || 4;
      if (/tavsiye|kesinlikle|mükemmel|mukemmel|harika|çok memnun|cok memnun|bayıldım|bayildim/.test(low)) score += 4;
      if (/henüz|fark hissetmedi|fark yok|bilmiyorum|yeni kullan|yeni başlad/.test(low)) score -= 3;
      if (s.text.length > 40) score += 1;
      return { ...s, _score: score };
    });
    scored.sort((a, b) => b._score - a._score);
    const fiveStar = scored.filter(s => s.rating >= 5);
    const ordered = fiveStar.length ? fiveStar : scored;
    return ordered.slice(0, 5).map(s => ({ text: s.text, rating: s.rating }));
  }

  function countTopic(product, topic) {
    if (!topic) return product.negative_reviews;
    if (product.top_complaint === topic) return product.top_complaint_count;
    return (product.negative_samples || []).filter(s => s.category === topic || inferComplaintFromText(s.text) === topic).length;
  }

  function makeEvidence(priority, title, detail, product, topic, totalRelevant, samples) {
    const total = product.total_reviews || 1;
    const count = topic ? countTopic(product, topic) : totalRelevant;
    const pct = Math.round((100 * count) / total);
    return {
      priority, title, detail,
      evidenceLabel: topic ? (topicLabel(topic) || "olumsuz yorum") : "yorum",
      evidencePct: pct, evidenceCount: count,
      totalRelevant: totalRelevant || count,
      sampleComments: samples || samplesForTopic(product, topic),
    };
  }

  function sellerContextVerdict(model) {
    return "";
  }

  function tailorRecommendationTitle(title, model) {
    if (model === "manufacturer" && /tedarik|toptancı/i.test(title)) return title.replace(/tedarikçi|toptancı/gi, "üretim süreci");
    if (model === "reseller" && /üretim|kalite kontrol listesi/i.test(title)) return "Toptancıyla kalite şartı netleştirin";
    return title;
  }

  function tailorRecommendationDetail(detail, model, topicLabelStr) {
    if (model === "manufacturer") {
      if (/ürün sayfası|açıklama|görsel|beklenti/i.test(detail)) return detail + " Üretimde düzelttiğiniz noktayı sayfada da güncelleyin.";
      return detail + " Üretici olarak bir sonraki seride kalite kontrol ve numune onayını sıkılaştırın.";
    }
    if (/üretim|işçilik|numune/i.test(detail) && !/tedarik|toptan/i.test(detail)) {
      return `Toptancınızla "${topicLabelStr || "şikayet"}" konusunu yazılı paylaşın; lot kalite şartı ekleyin.`;
    }
    if (/ürün sayfası|açıklama|beklenti|görsel/i.test(detail)) {
      return detail + " Marka ürünü siz üretmiyorsanız sayfada gerçekçi beklenti yazın.";
    }
    return detail + " Tedarikçiyle partiyi inceletin; gerekirse alternatif toptancı değerlendirin.";
  }

  function productInitial(name) {
    const t = (name || "?").trim();
    return t ? t[0].toUpperCase() : "?";
  }

  function classifyPositiveTone(text) {
    const low = (text || "").toLowerCase();
    if (/inşallah|insallah|umarım|umarim|memnun kalır|memnun kalir/.test(low) && !/kesinlikle|mükemmel|mukemmel|harika|bayıldım|bayildim/.test(low)) return "hope";
    if (/tavsiye|öneririm|oneririm|kesinlikle|tekrar al|herkese/.test(low)) return "recommend";
    if (/henüz|henuz|fark hissetmedi|fark yok|bilmiyorum|eminsiz|şüphe|suphe|yeni kullan|yeni başlad|bekliyorum|erken/.test(low)) return "early";
    if (/kargo|hızlı|hizli|teslimat|çabuk|cabuk|zamanında|zamaninda/.test(low)) return "shipping";
    if (/kalite|kumaş|kumas|sağlam|saglam|dayanıklı|dayanikli|doku/.test(low)) return "quality";
    if (/paket|ambalaj|özenli|ozenli/.test(low)) return "packaging";
    if (/fiyat|uygun|ucuz|performans/.test(low)) return "price";
    if (/mükemmel|mukemmel|harika|süper|super|bayıldım|bayildim|çok güzel|cok guzel/.test(low)) return "delight";
    if (/etki|işe yar|ise yar|fark etti|memnun|beğen|begen/.test(low)) return "effect";
    return "general";
  }

  const GOOD_HEADLINES = {
    hope: ["Güven veren bir mağazasın", "İtibarın güçlü"],
    recommend: ["Tavsiye edilen mağazasın", "Müşteriler seni öneriyor"],
    delight: ["Müşteriyi mutlu ediyorsun", "Deneyim tarafı çok iyi"],
    early: ["İlk izlenimi iyi yönetiyorsun", "Yeni müşteride güven veriyorsun"],
    shipping: ["Kargoyu iyi yapıyorsun", "Teslimat hızın güçlü yanın"],
    quality: ["Kaliteyi tutturuyorsun", "Kalite algın sağlam"],
    packaging: ["Paketlemeye özen gösteriyorsun", "Paket sunumun iyi"],
    price: ["Fiyat/performansı yakaladın", "Değer algın güçlü"],
    effect: ["Ürün gerçekten işe yarıyor", "Sonuç tarafında iyisin"],
    general: ["Bunu iyi yapıyorsun", "Doğru işi yapıyorsun"],
  };
  const GOOD_GENERIC = [
    "Bunu iyi yapıyorsun", "Güçlü bir yanın", "Bunu böyle sürdür",
    "Doğru işi yapıyorsun", "Memnuniyeti yakalamışsın",
  ];

  function friendlyGoodAdvice(sample, index) {
    const quote = truncateQuote(sample.text, 70);
    const tone = classifyPositiveTone(sample.text);
    const variants = {
      hope: [
        `Müşteri "${quote}" demiş — bir başkasının tavsiyesiyle sana güvenip gelmiş. İtibar tarafında işi gerçekten iyi yapıyorsun; insanlar seni anlatmaya değer buluyor. Bu güveni korumak için ilk sipariş deneyimini sıcak tut, böyle devam.`,
        `"${quote}" — henüz sonucu bilmese de seni seçmiş; bu, mağazanın güven verdiğinin işareti. İyi yaptığın şey: tavsiye zincirini bozmadan karşılamak. Küçük trick: kısa bir “iyi günlerde kullanın” notu bu umutlu müşteriyi sadık alıcıya çevirir.`,
      ],
      recommend: [
        `Müşteri "${quote}" demiş — açıkça seni öneriyor. Tavsiye alan mağazalar en sağlam büyür; bu yönde gerçekten iyi iş çıkarıyorsun, standardı düşürme.`,
        `"${quote}" — güçlü bir öneri. Müşteri seni çevresine anlatmaya değer bulmuş; bu en değerli pazarlama. Böyle giderse yeni alıcılar daha az tereddüt eder. Trick: memnun müşteriye samimi bir teşekkür bazen ikinci tavsiyeyi tetikler.`,
        `"${quote}" — net tavsiye geliyor. Güven tarafında tutturmuşsun; aynı kalite ve hizmetle devam ettiğinde bu cümleler çoğalır.`,
      ],
      delight: [
        `"${quote}" — coşkulu bir memnuniyet. Müşteri kısa bile yazsa hissi net: ürün beklentiyi karşılamış. Bu yönde iyi gidiyorsun, enerjini koru.`,
        `Müşteri "${quote}" demiş — tek cümlede bile sevgi var. Deneyim tarafında işi güzel yapıyorsun; bu tarz yorumlar birikince mağaza algısı kendiliğinden güçlenir.`,
      ],
      early: [
        `"${quote}" — henüz sonuç bekliyor ama sana güvenmiş. İlk izlenim tarafında doğru yapıyorsun; ürünü denemeye değer bulmuşlar. Sabırlı müşteriyi kaybetmemek için kullanım sürecini nazikçe hatırlatmak yeterli olur.`,
        `Bu yorum ("${quote}") olumsuz değil — yeni kullanıcı. Güven vermeyi başarmışsın; şimdi beklenti yönetimini de iyi yaparsan bu kişi memnun kalıcıya döner.`,
      ],
      shipping: [
        `Kargo tarafında gerçekten iyi iş çıkarıyorsun — "${quote}" bunun kanıtı. Hızlı teslimat senin güçlü yanın; aynı tempoyla devam. Trick: haftalık ortalama çıkış süresini takip etmek bu övgüyü sürdürür.`,
        `"${quote}" — teslimat memnuniyeti net. Operasyonu bu hızda tuttuğun sürece rakiplerin önündesin; müşteri bunu fark ediyor ve yazıyor.`,
      ],
      quality: [
        `Kalite konusunda müşterinin gözünde tutturmuşsun: "${quote}". Bu işi bu yönde iyi yapıyorsun — malzeme ve sunum standardını koru.`,
        `"${quote}" — kalite algısı güçlü. Müşteri eline aldığında beklediğini bulmuş; bu en zor kısım ve sen başarıyorsun. Trick: övülen kelimeyi (sağlam, doku vb.) görsellerin altına yazmak aynı algıyı pekiştirir.`,
      ],
      packaging: [
        `"${quote}" — paketlemeye özen göstermişsin ve müşteri fark etmiş. Küçük detay büyük fark yaratır; bu standardı bozma, hasarlı kutu yorumları azalır.`,
      ],
      price: [
        `"${quote}" — fiyat/performans dengesini yakalamışsın. Müşteri “değdi” diyor; bu algıyı korudukça tekrar alış gelir. Kampanya döneminde bile aynı değer hissini vermeye devam et.`,
      ],
      effect: [
        `"${quote}" — ürün etkisini hissetmiş. Sonuç tarafında işe yarıyorsun; bu en güçlü övgü. Benzer müşteri profiline ulaştıkça aynı cümleler çoğalır.`,
        `Müşteri "${quote}" demiş — fayda görmüş. Vaat ile gerçek uyumlu; bu yönde iyi gidiyorsun. Trick: kullanım süresini kısaca anlatan bir not sadık müşteri getirir.`,
      ],
      general: [
        `"${quote}" — olumlu ve samimi bir geri bildirim. Müşteri deneyiminden memnun kalmış; genel hatlarıyla işi iyi yapıyorsun, bu tempoyu koru.`,
        `Müşteri "${quote}" demiş — güven veren bir sinyal. Mağaza algın olumlu; küçük detaylara da özen gösterdiğinde bu yorumlar kendiliğinden artar.`,
        `"${quote}" — içten bir memnuniyet. Doğru müşteriye ulaştığında işin karşılığını alıyorsun; aynı özeni sürdür.`,
      ],
    };
    const pool = variants[tone] || variants.general;
    return pool[index % pool.length];
  }

  function specificRecommendation(product, topCode, topLabel, sampleQuote, sellerModel) {
    const negCount = product.negative_reviews || 0;
    const samples = samplesForTopic(product, topCode);
    const quote = sampleQuote || (samples[0]?.text ? truncateQuote(samples[0].text, 80) : null);

    if (topCode === "beden") {
      return {
        title: "Beden tablosunu yorumlara göre güncelle",
        detail: quote
          ? `"${quote}" gibi yorumlar beden beklentisi sapmasından geliyor olabilir. Sayfaya 'bir beden küçük/büyük kalabilir' notu ve ölçü tablosu ekle; ${negCount} düşük puanın bir kısmı bu yüzden gelmiş olabilir.`
          : `${negCount} düşük puan var ve beden konusu öne çıkıyor. Gerçek ölçüleri fotoğrafla birlikte yaz — sadece 'standart beden' demek yetmez.`,
      };
    }
    if (topCode === "kargo") {
      return {
        title: "Teslimat vaadini yorumlarla uyumlu tut",
        detail: quote
          ? `"${quote}" — kargo tarafında somut bir sorun var. Hangi kargo firması, kaç günde gidiyor, geç kalan siparişlere ne yapıyorsun — bunu netleştir. Sayfada yazdığın süre ile gerçek süre uyuşmazsa şikayet artar.`
          : `Kargo şikayeti ${negCount} kayıtta geçiyor. Teslimat süresini kısaltamıyorsan bile sayfada gerçekçi süre yaz; müşteri sürpriz yaşamasın.`,
      };
    }
    if (topCode === "kalite") {
      return {
        title: "Kalite şikayetinin kaynağını netleştir",
        detail: quote
          ? `"${quote}" — bu tür yorumlar genelde beklenti uyumsuzluğu veya partideki tutarsızlıktan gelir. ${sellerModel === "manufacturer" ? "Son partide numune kontrolü yap; fotoğraf ve açıklamayı gerçek ürünle eşleştir." : "Tedarikçiden bu lotu sor; gerekirse fotoğrafla karşılaştırıp değişim yap."}`
          : `Kalite konusunda ${negCount} olumsuz kayıt var. Ürün fotoğrafı stok görseli mi, gerçek çekim mi — müşteri gördüğüyle aldığının aynı olduğundan emin ol.`,
      };
    }
    if (topCode === "paketleme") {
      return {
        title: "Paketlemeyi güçlendir",
        detail: quote
          ? `"${quote}" — hasarlı/özensiz paket yorumları iade ve 1 yıldıza gider. Köpük, çift kat kutu ve kırılgan ürünlerde 'kırılır' etiketi küçük maliyet, büyük fark yaratır.`
          : `Paketleme ${negCount} yorumda geçiyor. Bir sonraki 10 siparişte paket fotoğrafı çekip standart belirle.`,
      };
    }
    if (topCode === "yanlis_urun" || topCode === "eksik_urun") {
      return {
        title: "Sipariş hazırlık kontrol listesi ekle",
        detail: quote
          ? `"${quote}" — yanlış/eksik gönderim güveni hızla düşürür. SKU etiketi + çift kontrol (ürün + adet) basit ama etkili; özellikle yoğun günlerde atlama.`
          : `${topLabel} konusunda ${negCount} şikayet var. Depoda barkod okutma veya fotoğraflı paketleme kaydı düşün.`,
      };
    }
    if (quote) {
      return {
        title: `"${topLabel}" konusuna odaklan`,
        detail: `"${quote}" — ${negCount} düşük puanda bu tema tekrar ediyor. Önce bu konuyu çöz; sayfa, paket veya süreçte somut bir adım atmadan pazarlama harcaması artırmak aynı şikayeti çoğaltır.`,
      };
    }
    return {
      title: "Düşük puanlı yorumları tek tek oku",
      detail: `${negCount} kişi 1–3 yıldız vermiş. Metin yazmasa bile puan düşük demek bir şey ters gitmiş — son 10 düşük puanı Excel'e al, ortak kelimeyi bul, ona göre düzelt.`,
    };
  }

  function secondRecommendation(product, topCode, secondCode, secondTopic, sampleQuote) {
    if (secondCode && countTopic(product, secondCode) > 0) {
      const q = (samplesForTopic(product, secondCode)[0]?.text);
      return {
        title: `"${secondTopic}" için de küçük bir adım at`,
        detail: q
          ? `İkinci sırada "${secondTopic}" var — örnek: "${truncateQuote(q, 60)}". Ana sorunu çözerken bunu da sayfaya veya sürece yansıt.`
          : `"${secondTopic}" ${countTopic(product, secondCode)} yorumda geçiyor; görseller ve açıklamayı buna göre güncelle.`,
      };
    }
    const pos = positiveSamples(product)[0];
    if (pos?.text) {
      return {
        title: "Olumlu yorumdaki güçlü yanı başlığa taşı",
        detail: `"${truncateQuote(pos.text, 70)}" — müşteriler bunu seviyor. Başlık veya ilk bullet'ta aynı ifadeyi kullan; alıcı ne alacağını net görür.`,
      };
    }
    return {
      title: "Ürün sayfasına gerçek kullanım fotoğrafı ekle",
      detail: "Stok görseli tek başına yetmeyebilir. Müşterinin çektiği (izinle) veya kendi çekiminle boyut/kullanım göster — beklenti sapması azalır.",
    };
  }

  function thirdRecommendation(product, topCode, sampleQuote) {
    const lowRated = (product.negative_samples || []).filter(s => s.rating <= 2 && s.text?.trim());
    if (lowRated.length) {
      const s = lowRated[0];
      return {
        title: "1–2 yıldızlı yoruma kişisel dönüş yap",
        detail: `"${truncateQuote(s.text, 70)}" — ${lowRated.length} tane ciddi düşük puan var. Şablon değil, bu yorumdaki somut konuya değinen kısa bir cevap yaz; hem algoritma hem yeni alıcılar bunu görür.`,
      };
    }
    if (product.average_rating < 4) {
      return {
        title: "Beklenti uyumsuzluğunu kapat",
        detail: `Ortalama ${product.average_rating}/5 — genelde fotoğraf/açıklama ile gerçek ürün uyuşmuyor demek. Ölçü, malzeme ve kullanım alanını net yaz.`,
      };
    }
    return {
      title: "Yorum hacmini artır (doğru müşteriden)",
      detail: "Puan iyi ama az yorum varsa algoritma güveni düşük kalır. Memnun müşteriye teslimat sonrası kısa anket veya kupon teşviki düşün — sahte değil, gerçek alıcıdan.",
    };
  }

  function buildAvoidMistakes(product, topCode, topLabel, sampleQuote, sellerModel) {
    const mistakes = [];

    if (product.negative_pct >= 8) {
      mistakes.push({
        title: "Şikayet devam ederken reklamı artırmak",
        detail: `${sampleQuote ? `Şu yorum ortada dururken ("${truncateQuote(sampleQuote, 55)}") ` : `"${topLabel}" şikayeti sürerken `}daha fazla trafik çekmek aynı hayal kırıklığını daha çok kişiye yaşatır ve iadeyi büyütür. Önce ${product.negative_reviews} olumsuz kaydın kök nedenini çöz; sonra bütçeyi artırınca harcadığın her lira ciroya döner, yoksa reklam parası boşa gider.`,
        topic: topCode,
      });
    }

    if (topCode === "beden") {
      mistakes.push({
        title: "Beden tablosu düzelmeden yeni varyant açmak",
        detail: "Beden şikayeti varken yeni renk/model eklemek aynı sorunu çoğaltır — farklı SKU, aynı kalıp. Önce tabloyu düzelt, sonra çeşitlendir.",
        topic: "beden",
      });
    } else if (topCode === "kargo") {
      mistakes.push({
        title: "Kargo sorunu varken 'aynı gün kargo' yazmak",
        detail: "Teslimat gecikmesi yorumlarda geçiyorsa agresif vaat (aynı gün, ertesi gün) 1 yıldız patlaması yaratır. Gerçek süren neyse onu yaz.",
        topic: "kargo",
      });
    } else if (topCode === "kalite") {
      mistakes.push({
        title: "Kalite şikayetini sadece indirimle susturmak",
        detail: sampleQuote
          ? `"${truncateQuote(sampleQuote, 60)}" gibi yorumlar fiyat indirimiyle düzelmez — ürün aynı kalırsa iade gelir. İndirim geçici satış getirir, tekrarlayan şikayet getirmez.`
          : "Operasyonel kalite sorunu varken kampanya açmak kısa vadede satış artırır ama uzun vadede puanı daha da düşürür.",
        topic: "kalite",
      });
    } else {
      mistakes.push({
        title: "Olumsuz yorumu görmezden gelmek",
        detail: "Az sayıda bile 1–3 yıldız, yeni alıcının kararını etkiler. Cevap vermemek 'ilgilenmiyorlar' algısı yaratır; bu da satışa giden son adımda kaybettirir.",
        topic: topCode,
      });
    }

    mistakes.push({
      title: "Her müşteriye aynı kopyala-yapıştır cevap",
      detail: "Robot cevap hem platformda hem müşteride kötü karşılanır. 'Değerli müşterimiz' şablonu yerine o yorumdaki kelimeyi (beden, kargo, renk vb.) geçir — samimi ama kısa ol.",
      topic: null,
    });

    if (sellerModel === "reseller" && topCode === "kalite") {
      mistakes.push({
        title: "Tedarikçiyi sorgulamadan sadece sayfayı değiştirmek",
        detail: "Toptancıdan gelen lot bozuksa sayfa metnini değiştirmek yetmez — aynı partiden gelen siparişler aynı şikayeti tekrarlar. Lot numarası sor, gerekirse değiştir.",
        topic: "kalite",
      });
    } else {
      mistakes.push({
        title: "Yorumdaki ipucunu operasyona taşımamak",
        detail: "Müşteri yorumu ücretsiz kalite kontrolüdür. 'Paket yırtık', 'dikiş sökük' gibi tekrar eden ifadeleri depo/paketleme tarafında düzeltmezsen aynı cümleler gelmeye devam eder.",
        topic: topCode,
      });
    }

    return mistakes.slice(0, 3).map((m, i) =>
      makeEvidence(i + 1, m.title, m.detail, product, m.topic, product.negative_reviews || 1, samplesForTopic(product, m.topic))
    );
  }

  function buildGoodPractices(product) {
    const positives = positiveSamples(product);
    if (!positives.length) {
      return [
        makeEvidence(1, "İlk olumlu yorumları topla", "Henüz metinli 5 yıldız az — teslimat sonrası kısa teşekkür mesajı veya anket linki işe yarar. Sahte yorum değil, gerçek memnun müşteriden.", product, null, product.positive_reviews, []),
        makeEvidence(2, "Sayfaya gerçek ürün fotoğrafı koy", "Stok görseli tek başına yetmeyebilir; boyutu ve dokuyu gösteren bir çekim beklenti sapmasını azaltır.", product, null, product.total_reviews, []),
      ];
    }
    const used = new Set();
    return positives.slice(0, 3).map((sample, index) => {
      const tone = classifyPositiveTone(sample.text);
      const title = pickUniqueTitle(GOOD_HEADLINES[tone], GOOD_GENERIC, used);
      return makeEvidence(
        index + 1,
        title,
        friendlyGoodAdvice(sample, index),
        product, null, product.positive_reviews, [sample]
      );
    });
  }

  function distinctNegativeSamples(product, limit) {
    const out = [];
    const seen = new Set();
    for (const s of (product.negative_samples || [])) {
      const t = (s.text || "").trim();
      if (t.length < 3) continue;
      const key = t.slice(0, 40).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(s);
      if (out.length >= limit) break;
    }
    return out;
  }

  function friendlyFixAdvice(sample) {
    const quote = truncateQuote(sample.text, 70);
    const topic = (sample.category && !GENERIC.has(sample.category))
      ? sample.category
      : inferComplaintFromText(sample.text);
    const map = {
      beden: `Bak, müşteri "${quote}" demiş — beden/ölçü beklentisi tutmamış. Sayfaya net ölçü tablosu ve "bir beden büyük/küçük tercih edin" notu ekle. Bu küçük dokunuş beden kaynaklı iadeleri ciddi düşürür; iade azaldıkça net ciron kendiliğinden artar.`,
      kargo: `Bu yorumda "${quote}" diyor — teslimat tarafında canı sıkılmış. Sayfada gerçekçi süre yaz, geciken siparişe kısa bir bilgilendirme/özür mesajı at. Müşteri kendini görülmüş hisseder; 1 yıldız yerine tekrar sipariş bırakır, bu da doğrudan satışa yansır.`,
      kalite: `Müşteri "${quote}" demiş — eline aldığında beklediğini bulamamış. Aynı temanın kaç yorumda tekrar ettiğine bak; malzeme/işçilikte küçük bir iyileştirme "kalitesiz" yorumlarını keser, puanın yükselir. Yüksek puan = aramada üst sıra = daha çok satış.`,
      paketleme: `"${quote}" — paket müşteriyi üzmüş. Çift kat kutu/köpük gibi düşük maliyetli bir önlem hasar kaynaklı iadeleri ve olumsuz yorumu bitirir. İade maliyetinden kurtardığın para doğrudan cirona yazar.`,
      yanlis_urun: `Bu yorumda "${quote}" demiş — yanlış/eksik gönderim güveni hızla düşürür. Paketlemede ürün + adet çift kontrolü (barkod/fotoğraf) uygula. Bu tekrarlayan hatayı bitirir, iadeyi ve puan kaybını durdurur.`,
      eksik_urun: `Bu yorumda "${quote}" demiş — yanlış/eksik gönderim güveni hızla düşürür. Paketlemede ürün + adet çift kontrolü (barkod/fotoğraf) uygula. Bu tekrarlayan hatayı bitirir, iadeyi ve puan kaybını durdurur.`,
      fiyat: `"${quote}" — fiyat/değer algısı zorlanmış. Ürünün somut faydasını (ne kazandırdığını) ilk görselde ve başlıkta vurgula; "pahalı" algısı "değer" algısına dönünce dönüşüm oranın artar.`,
      beklenti: `Müşteri "${quote}" demiş — aldığı, gördüğüyle uyuşmamış. Görsel ve açıklamayı gerçek ürünle birebir eşitle. Beklenti sapması kapanınca hem iade hem olumsuz yorum azalır, memnun müşteri çoğalır.`,
    };
    return map[topic] || `Bak, bu müşteri "${quote}" demiş. Bu konuya (şablon cevap değil) somut bir dönüş yap ve süreçte düzelt. Aynı şeyi yaşayıp sessizce giden onlarca alıcı var; buna yoğunlaşman satış kaybını durdurur ve puanını yukarı çeker.`;
  }

  const FIX_HEADLINES = {
    beden: ["Beden/ölçü beklentisini netleştir", "Ölçü tablosunu yorumlara göre güncelle"],
    kargo: ["Teslimat deneyimini iyileştir", "Kargo vaadini gerçekle eşitle"],
    kalite: ["Kalite algısını yükselt", "Kalite tutarlılığını sağla"],
    paketleme: ["Paketlemeyi güçlendir", "Hasarsız paket standardı kur"],
    yanlis_urun: ["Doğru ürün/adet kontrolü yap", "Sipariş hazırlığını çift kontrol et"],
    eksik_urun: ["Eksik gönderimi önle", "Sipariş hazırlığını çift kontrol et"],
    fiyat: ["Fiyat/değer algısını yönet", "Değer algısını güçlendir"],
    beklenti: ["Sayfa ile ürünü birebir eşitle", "Beklenti sapmasını kapat"],
  };
  const FIX_GENERIC = [
    "Bu yorumdaki konuya odaklan", "Bu şikayeti kökten çöz",
    "Tekrarlayan bu konuyu düzelt", "Bu geri bildirime aksiyon al",
    "Bu detayı süreçte iyileştir",
  ];

  function pickUniqueTitle(candidates, generics, used) {
    for (const c of (candidates || [])) if (c && !used.has(c)) { used.add(c); return c; }
    for (const g of (generics || [])) if (g && !used.has(g)) { used.add(g); return g; }
    const base = (candidates && candidates[0]) || (generics && generics[0]) || "Öneri";
    let i = 2;
    while (used.has(`${base} (${i})`)) i++;
    const t = `${base} (${i})`;
    used.add(t);
    return t;
  }

  function buildRecommendations(product, topCode, topLabel, secondCode, secondTopic, sampleQuote, sellerModel) {
    const items = [];
    const used = new Set();
    const push = (title, detail, topic, count, samples) => {
      if (items.length >= 3) return;
      items.push(makeEvidence(items.length + 1, title, detail, product, topic || null, count, samples || []));
    };

    // 1) Gerçek olumsuz yorumları tek tek analiz et — samimi, ciro odaklı
    for (const s of distinctNegativeSamples(product, 8)) {
      if (items.length >= 3) break;
      const topic = (s.category && !GENERIC.has(s.category)) ? s.category : inferComplaintFromText(s.text);
      const title = pickUniqueTitle(FIX_HEADLINES[topic], FIX_GENERIC, used);
      push(title, friendlyFixAdvice(s), topic, product.negative_reviews, [s]);
    }

    // 2) Yeterli olumsuz yorum yoksa konu/olumlu temelli önerilerle 3'e tamamla
    if (items.length < 3) {
      const fillers = [];
      const rec1 = specificRecommendation(product, topCode, topLabel, sampleQuote, sellerModel);
      fillers.push({ t: rec1.title, d: rec1.detail, topic: topCode, s: samplesForTopic(product, topCode) });
      const rec2 = secondRecommendation(product, topCode, secondCode, secondTopic, sampleQuote);
      fillers.push({ t: rec2.title, d: rec2.detail, topic: secondCode, s: [] });
      const pos = positiveSamples(product)[0];
      if (pos?.text) {
        fillers.push({
          t: "Güçlü yanını başlıkta öne çıkar",
          d: `"${truncateQuote(pos.text, 60)}" — müşteriler bunu seviyor. Aynı ifadeyi başlık ve ilk görselde vurgula; kararsız alıcı ne alacağını net görünce dönüşüm artar, ciron yükselir.`,
          topic: null, s: [pos],
        });
      }
      const rec3 = thirdRecommendation(product, topCode, sampleQuote);
      fillers.push({ t: rec3.title, d: rec3.detail, topic: null, s: [] });
      fillers.push({
        t: "Memnun müşteriden gerçek yorum topla",
        d: "Teslimat sonrası kısa ve samimi bir teşekkür/anket mesajı at (sahte değil). Yorum hacmi arttıkça algoritma güveni ve satış birlikte yükselir.",
        topic: null, s: [],
      });
      for (const f of fillers) {
        if (items.length >= 3) break;
        if (used.has(f.t)) continue;
        used.add(f.t);
        const cnt = f.topic ? countTopic(product, f.topic) : (product.negative_reviews || product.total_reviews);
        push(f.t, f.d, f.topic, cnt, f.s);
      }
    }

    return items.slice(0, 3);
  }

  function buildProductReviewInsight(product, sellerModel) {
    const topCode = product.top_complaint && !GENERIC.has(product.top_complaint)
      ? product.top_complaint
      : inferComplaintFromText(product.negative_samples?.[0]?.text ?? "") ||
        product.negative_samples?.find(s => s.category && !GENERIC.has(s.category))?.category || null;

    const topLabel = resolveProductTopTopic(product) || topicLabel(topCode) || "olumsuz geri bildirim";
    const secondBar = Object.entries(product.topic_breakdown || {}).filter(([c]) => !GENERIC.has(c))[1];
    const secondCode = secondBar ? secondBar[0] : null;
    const secondTopic = secondBar ? formatComplaintLabel(secondBar[0]) : null;

    const commented = product.commented_reviews ?? product.total_reviews;
    const ratingOnly = product.rating_only_reviews ?? Math.max(0, product.total_reviews - commented);
    const statsLine = `${product.total_reviews} değerlendirme · ${commented} yorum yazılmış · ${ratingOnly} sadece puan vermiş · olumsuz: 1–3 yıldız`;

    const firstNeg = (product.negative_samples || []).find(s => s.text?.trim());
    const sampleQuote = firstNeg ? truncateQuote(firstNeg.text) : null;

    let verdictTone = "good";
    if (product.negative_pct >= 25 || product.average_rating < 3.5) verdictTone = "bad";
    else if (product.negative_pct >= 12 || product.average_rating < 4) verdictTone = "warn";

    let verdict = verdictTone === "good"
      ? `Genel tablo gayet iyi görünüyor — %${product.positive_pct} olumlu, ortalama memnuniyet yaklaşık %${ratingToSatisfactionPct(product.average_rating)}.`
      : verdictTone === "warn"
        ? `Biraz dikkat: %${product.negative_pct} olumsuz var ve "${topLabel}" tekrar ediyor. Büyütmeden önce bu konuya bakmak iyi olur.`
        : `Burada ciddi bir sinyal var — %${product.negative_pct} olumsuz, ortalama ${product.average_rating}/5. "${topLabel}" baskın; önce bunu çözmek mantıklı.`;

    const recommendations = buildRecommendations(product, topCode, topLabel, secondCode, secondTopic, sampleQuote, sellerModel);

    const avoidActions = buildAvoidMistakes(product, topCode, topLabel, sampleQuote, sellerModel);

    return {
      verdict, statsLine, verdictTone,
      chart: {
        positive: product.positive_pct,
        negative: product.negative_pct,
        neutral: Math.max(0, 100 - product.positive_pct - product.negative_pct),
      },
      recommendations: recommendations.slice(0, 3),
      avoidActions: avoidActions.slice(0, 3),
      goodPractices: buildGoodPractices(product).slice(0, 3),
    };
  }

  function validateScrapeUrl(url, platform) {
    const trimmed = (url || "").trim();
    if (!trimmed) return "Ürün linkini girin";
    if (!trimmed.startsWith("http")) return "Link https:// ile başlamalı";
    let host = "";
    try { host = new URL(trimmed).hostname.toLowerCase(); } catch { return "Geçerli bir link girin"; }
    const checks = {
      trendyol: "trendyol.com", hepsiburada: "hepsiburada.com",
      n11: "n11.com", amazon: "amazon.com.tr",
    };
    if (!host.includes(checks[platform] || "trendyol.com")) {
      const labels = { trendyol: "Trendyol", hepsiburada: "Hepsiburada", n11: "N11", amazon: "Amazon.com.tr" };
      return `Bu platform ${labels[platform] || platform}. ${labels[platform]} ürün linki girin.`;
    }
    return null;
  }

  const SCRAPE_COPY = {
    trendyol: { title: "Trendyol ürün linki", hint: "Trendyol ürün sayfası linkini yapıştırın (…-p-…).", placeholder: "https://www.trendyol.com/...-p-..." },
    hepsiburada: { title: "Hepsiburada ürün linki", hint: "Hepsiburada ürün sayfası linkini yapıştırın (…-pm-… veya …-p-…).", placeholder: "https://www.hepsiburada.com/...-p-..." },
    n11: { title: "N11 ürün linki", hint: "N11 ürün sayfası linkini yapıştırın.", placeholder: "https://www.n11.com/urun/..." },
    amazon: { title: "Amazon.com.tr ürün linki", hint: "Amazon ürün sayfası linkini yapıştırın (/dp/ASIN).", placeholder: "https://www.amazon.com.tr/dp/..." },
  };

  function trackingUrl(carrier, trackingNumber) {
    const c = (carrier || "").toLowerCase();
    const n = encodeURIComponent(trackingNumber || "");
    if (c.includes("sürat") || c.includes("surat")) return `https://www.suratkargo.com.tr/KargoTakip/?kargotakipno=${n}`;
    if (c.includes("yurtiçi") || c.includes("yurtici")) return `https://www.yurticikargo.com/tr/online-servisler/gonderi-sorgula?code=${n}`;
    if (c.includes("aras")) return `https://www.araskargo.com.tr/tr/cargo-tracking?code=${n}`;
    if (c.includes("mng")) return `https://www.mngkargo.com.tr/track/${n}`;
    if (c.includes("ptt")) return `https://gonderitakip.ptt.gov.tr/Track/Verify?q=${n}`;
    return null;
  }

  function escHtml(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  }

  function parseProductRankLine(line) {
    const m = line.match(/^\d+\.\s(.+?)\s—\s(.+)$/);
    if (!m) return null;
    const name = m[1].trim();
    const statsPart = m[2];
    const ratingMatch = statsPart.match(/★([\d.]+)/);
    const posMatch = statsPart.match(/%(\d+)\s+olumlu/);
    const negMatch = statsPart.match(/%(\d+)\s+olumsuz/);
    const complaintMatch = statsPart.match(/ana şikayet:\s*(.+)$/i);
    return {
      name,
      meta: statsPart.replace(/\s*—\s*ana şikayet:.+$/i, ""),
      rating: ratingMatch?.[1],
      posPct: posMatch?.[1],
      negPct: negMatch?.[1],
      complaint: complaintMatch?.[1],
    };
  }

  function isRankingHighlight(line) {
    const low = line.toLowerCase();
    return low.startsWith("en iyi performans") || low.startsWith("en problemli") || low.startsWith("büyük ihtimalle");
  }

  function proseTone(line) {
    const low = line.toLowerCase();
    if (low.startsWith("bu alanda gerçekten")) return "praise";
    if (low.startsWith("örnek yorum")) return "quote";
    if (low.startsWith("ikinci sırada")) return "secondary";
    return "default";
  }

  function miniBarHtml(label, value, max, color) {
    const w = max ? Math.round((100 * value) / max) : 0;
    return `<div class="mini-bar-row"><span class="mini-bar-label">${escHtml(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${color}"></div></div><span class="mini-bar-val">${value}</span></div>`;
  }

  function renderConsultantAnswer(answer, recommendations, sortedComplaints) {
    const cleanAnswer = (answer || "")
      .replace(/^CSV'nize göre ürün bazında performans özeti:\s*\n?/i, "")
      .trim();
    const blocks = cleanAnswer.split(/\n\n+/).map(b => b.trim()).filter(Boolean);
    const productLines = [];
    const narrativeBlocks = [];

    for (const block of blocks) {
      const lines = block.split("\n").map(l => l.trim()).filter(Boolean);
      if (lines.length > 0 && lines.every(l => /^\d+\.\s/.test(l))) {
        productLines.push(...lines);
      } else {
        narrativeBlocks.push(block);
      }
    }

    const rankingHighlights = [];
    const proseParagraphs = [];
    for (const block of narrativeBlocks) {
      for (const line of block.split("\n").map(l => l.trim()).filter(Boolean)) {
        if (isRankingHighlight(line)) rankingHighlights.push(line);
        else proseParagraphs.push(line);
      }
    }

    const showLegacyList = (sortedComplaints || []).length > 0 &&
      !(sortedComplaints || []).every(item => cleanAnswer.includes(item.trim()));

    let html = '<div class="consultant-answer-body">';

    if (productLines.length) {
      html += '<div class="consultant-answer-products">';
      for (const line of productLines) {
        const parsed = parseProductRankLine(line);
        const rank = line.match(/^(\d+)\./)?.[1] || "";
        if (!parsed) {
          html += `<div class="consultant-product-rank-card"><p class="consultant-product-rank-line">${escHtml(line.replace(/^\d+\.\s/, ""))}</p></div>`;
        } else {
          html += `<article class="consultant-product-rank-card">
            <div class="consultant-product-rank-head"><span class="consultant-product-rank-num">${rank}</span><h4 class="consultant-product-rank-name">${escHtml(parsed.name)}</h4></div>
            <p class="consultant-product-rank-meta">${escHtml(parsed.meta)}</p>
            <div class="consultant-product-rank-pills">
              ${parsed.rating ? `<span class="consultant-rank-pill consultant-rank-pill-star">★ ${parsed.rating}</span>` : ""}
              ${parsed.posPct ? `<span class="consultant-rank-pill consultant-rank-pill-pos">%${parsed.posPct} olumlu</span>` : ""}
              ${parsed.negPct ? `<span class="consultant-rank-pill consultant-rank-pill-neg">%${parsed.negPct} olumsuz</span>` : ""}
            </div>
            ${parsed.complaint ? `<p class="consultant-product-rank-complaint">Ana şikayet: ${escHtml(parsed.complaint)}</p>` : ""}
          </article>`;
        }
      }
      html += "</div>";
    }

    if (rankingHighlights.length) {
      html += '<div class="consultant-answer-highlights">';
      for (const line of rankingHighlights) {
        const isBest = line.toLowerCase().startsWith("en iyi performans");
        const isWorst = line.toLowerCase().startsWith("en problemli");
        const tone = isBest ? "best" : isWorst ? "worst" : "neutral";
        html += `<p class="consultant-highlight-line consultant-highlight-${tone}">${escHtml(line)}</p>`;
      }
      html += "</div>";
    }

    if (proseParagraphs.length) {
      html += '<div class="consultant-answer-prose">';
      for (const line of proseParagraphs) {
        html += `<p class="consultant-answer-text consultant-prose-${proseTone(line)}">${escHtml(line)}</p>`;
      }
      html += "</div>";
    }

    if ((recommendations || []).length) {
      html += `<section class="consultant-rec-section"><h4 class="consultant-rec-heading">Danışman önerileri</h4><ul class="consultant-rec-list">${recommendations.map(r => `<li>${escHtml(r)}</li>`).join("")}</ul></section>`;
    }

    if (showLegacyList) {
      html += `<ul class="consultant-legacy-rec-list">${sortedComplaints.map(item => `<li>${escHtml(item)}</li>`).join("")}</ul>`;
    }

    html += "</div>";
    return html;
  }

  global.AssistantInsight = {
    buildProductReviewInsight, formatComplaintLabel, validateScrapeUrl,
    SCRAPE_COPY, trackingUrl, inferComplaintFromText,
    renderConsultantAnswer, miniBarHtml, productInitial,
  };
})(window);
