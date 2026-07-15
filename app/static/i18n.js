/** Panel i18n — TR / EN */
(function (global) {
  const DICT = {
    tr: {
      settings: "Ayarlar",
      settingsTitle: "Ayarlar",
      settingsClose: "Kapat",
      secAppearance: "Görünüm",
      themeLight: "Aydınlık mod",
      themeDark: "Karanlık mod",
      secLanguage: "Dil",
      langTr: "Türkçe",
      langEn: "English",
      secSupport: "Destek & hesap",
      support: "Destek",
      account: "Hesabım",
      secLogout: "Oturum",
      logout: "Hesaptan çıkış yap",
      navOverview: "Genel Bakış",
      navOps: "Operasyon",
      navOrders: "Siparişler",
      navProducts: "Ürünler & Stok",
      navQuestions: "Sorular",
      navClaims: "İade & Tazmin",
      navFinance: "Finans",
      navWarehouse: "Depo",
      navWarehouseItems: "Depodaki Ürünlerim",
      navAi: "AI Araçları",
      navListing: "AI Ürün Ekleme",
      navBuybox: "Buybox & Fiyat",
      navAssistant: "Analiz",
      navAnalytics: "Satış Analizi",
      brandSub: "E-ticaret paneli",
      "view.overview.title": "Genel Bakış",
      "view.overview.sub": "Tüm mağazalarının güncel durumu",
      "view.orders.title": "Siparişler",
      "view.orders.sub": "Tüm pazaryerlerinden gelen paketler",
      "view.products.title": "Ürünler & Stok",
      "view.products.sub": "Fiyat ve stokları buradan anında güncelle",
      "view.questions.title": "Müşteri Soruları",
      "view.questions.sub": "AI önerisiyle saniyeler içinde cevapla",
      "view.claims.title": "İade & Tazmin",
      "view.claims.sub": "AI önerir, siz onaylar veya düzenlersiniz",
      "view.finance.title": "Finans",
      "view.finance.sub": "Hakediş, komisyon ve net kazanç",
      "view.listing.title": "AI Ürün Ekleme",
      "view.listing.sub": "Fotoğraftan saniyeler içinde ilan hazırla",
      "view.warehouse.title": "Depodaki Ürünlerim",
      "view.warehouse.sub": "AI ile hazırladığın taslak ürünler",
      "view.buybox.title": "Buybox & Fiyat",
      "view.buybox.sub": "Rakip fiyatlarını izle, AI ile fiyat öner",
      "view.assistant.title": "Analiz",
      "view.assistant.sub": "Yorum, iade ve kargo verilerine göre kanıt odaklı öneriler",
      "view.analytics.title": "Satış Analizi",
      "view.analytics.sub": "Sipariş CSV'nden ciro ve kârlılık raporu",
      "view.support.title": "Destek",
      "view.support.sub": "E-posta ile destek ekibine ulaş",
      "view.settings.title": "Ayarlar",
      "view.settings.sub": "Tema, dil ve hesap seçenekleri",
      langChanged: "Dil İngilizce olarak ayarlandı.",
      langChangedTr: "Dil Türkçe olarak ayarlandı.",
      noStore: "Mağaza bağlı değil",
    },
    en: {
      settings: "Settings",
      settingsTitle: "Settings",
      settingsClose: "Close",
      secAppearance: "Appearance",
      themeLight: "Light mode",
      themeDark: "Dark mode",
      secLanguage: "Language",
      langTr: "Türkçe",
      langEn: "English",
      secSupport: "Support & account",
      support: "Support",
      account: "My account",
      secLogout: "Session",
      logout: "Sign out",
      navOverview: "Overview",
      navOps: "Operations",
      navOrders: "Orders",
      navProducts: "Products & Stock",
      navQuestions: "Questions",
      navClaims: "Returns",
      navFinance: "Finance",
      navWarehouse: "Warehouse",
      navWarehouseItems: "My warehouse items",
      navAi: "AI Tools",
      navListing: "AI product listing",
      navBuybox: "Buybox & Pricing",
      navAssistant: "Insights",
      navAnalytics: "Sales analytics",
      brandSub: "E-commerce panel",
      "view.overview.title": "Overview",
      "view.overview.sub": "Current status of your stores",
      "view.orders.title": "Orders",
      "view.orders.sub": "Packages from all marketplaces",
      "view.products.title": "Products & Stock",
      "view.products.sub": "Update price and stock instantly",
      "view.questions.title": "Customer questions",
      "view.questions.sub": "Answer in seconds with AI suggestions",
      "view.claims.title": "Returns & claims",
      "view.claims.sub": "AI suggests, you confirm or edit",
      "view.finance.title": "Finance",
      "view.finance.sub": "Settlements, commission and net earnings",
      "view.listing.title": "AI product listing",
      "view.listing.sub": "Create a listing from a photo in seconds",
      "view.warehouse.title": "My warehouse items",
      "view.warehouse.sub": "Draft products prepared with AI",
      "view.buybox.title": "Buybox & Pricing",
      "view.buybox.sub": "Track competitor prices, get AI suggestions",
      "view.assistant.title": "Insights",
      "view.assistant.sub": "Evidence-based recommendations from reviews and returns",
      "view.analytics.title": "Sales analytics",
      "view.analytics.sub": "Revenue and profitability from order CSV",
      "view.support.title": "Support",
      "view.support.sub": "Reach the support team by email",
      "view.settings.title": "Settings",
      "view.settings.sub": "Theme, language and account options",
      langChanged: "Language set to English.",
      langChangedTr: "Language set to Turkish.",
      noStore: "No store connected",
    },
  };

  function getLang() {
    const l = localStorage.getItem("lang");
    return l === "en" ? "en" : "tr";
  }

  function t(key) {
    const lang = getLang();
    return (DICT[lang] && DICT[lang][key]) || (DICT.tr[key]) || key;
  }

  function setLang(lang) {
    localStorage.setItem("lang", lang === "en" ? "en" : "tr");
    document.documentElement.lang = getLang();
    applyDom();
    if (typeof global.applyPanelI18n === "function") global.applyPanelI18n();
  }

  function applyDom() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (!key) return;
      const val = t(key);
      if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
        if (el.hasAttribute("data-i18n-placeholder")) el.placeholder = val;
      } else {
        el.textContent = val;
      }
    });
    document.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const key = el.getAttribute("data-i18n-html");
      if (key) el.innerHTML = t(key);
    });
  }

  global.I18N = { DICT, t, getLang, setLang, applyDom };
})(window);
