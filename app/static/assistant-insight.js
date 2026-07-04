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
    const fiveStar = pool.filter(s => s.rating >= 5);
    return (fiveStar.length ? fiveStar : pool).slice(0, 5).map(s => ({ text: s.text, rating: s.rating }));
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

  function friendlyGoodAdvice(sample, product) {
    const quote = truncateQuote(sample.text, 70);
    const themes = [];
    const low = sample.text.toLowerCase();
    if (/kargo|hızlı|hizli|teslimat/.test(low)) themes.push("hızlı teslimat");
    if (/kalite|kumaş|kumas|sağlam|saglam/.test(low)) themes.push("ürün kalitesi");
    if (/paket|ambalaj/.test(low)) themes.push("özenli paketleme");
    if (/fiyat|uygun/.test(low)) themes.push("fiyat/performans");
    const theme = themes[0] || "genel memnuniyet";
    return `Bir müşteri "${quote}" demiş — ${theme} konusunda iyi bir izlenim bırakmışsın. Bu tarz yorumlar genelde aynı standardın devam ettiğini gösterir; sayfanda da bunu yansıtırsan yeni alıcılar doğru beklentiyle gelir.`;
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
        detail: `"${topLabel}" hâlâ geliyorken daha fazla trafik çekmek, aynı hayal kırıklığını daha çok kişiye yaşatır. Önce ${product.negative_reviews} olumsuz kaydın kök nedenini çöz, sonra bütçeyi artır — yoksa iade ve düşük puan oranı büyür.`,
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
    return positives.slice(0, 3).map((sample, index) => {
      const shortTitle = truncateQuote(sample.text, 55);
      return makeEvidence(
        index + 1,
        `Müşteriler: "${shortTitle}"`,
        friendlyGoodAdvice(sample, product),
        product, null, product.positive_reviews, [sample]
      );
    });
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

    const rec1 = specificRecommendation(product, topCode, topLabel, sampleQuote, sellerModel);
    const rec2 = secondRecommendation(product, topCode, secondCode, secondTopic, sampleQuote);
    const rec3 = thirdRecommendation(product, topCode, sampleQuote);

    const recommendations = [
      makeEvidence(1, rec1.title, rec1.detail, product, topCode, product.negative_reviews, samplesForTopic(product, topCode)),
      makeEvidence(2, rec2.title, rec2.detail, product, secondCode, secondCode ? countTopic(product, secondCode) : product.positive_reviews, []),
      makeEvidence(3, rec3.title, rec3.detail, product, null, product.negative_reviews || product.total_reviews, []),
    ];

    const avoidActions = buildAvoidMistakes(product, topCode, topLabel, sampleQuote, sellerModel);

    const applySeller = items => {
      if (!sellerModel) return items;
      return items.map(item => ({
        ...item,
        title: tailorRecommendationTitle(item.title, sellerModel),
        detail: tailorRecommendationDetail(item.detail, sellerModel, topLabel),
      }));
    };

    return {
      verdict, statsLine, verdictTone,
      chart: {
        positive: product.positive_pct,
        negative: product.negative_pct,
        neutral: Math.max(0, 100 - product.positive_pct - product.negative_pct),
      },
      recommendations: applySeller(recommendations.slice(0, 3)),
      avoidActions: avoidActions.slice(0, 3),
      goodPractices: applySeller(buildGoodPractices(product).slice(0, 3)),
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
