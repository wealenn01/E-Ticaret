/** Globe Express — Timed Cards Opening (Giulio Cuscito / bilkn/globe-express) */
(function () {
  const $ = id => document.getElementById(id);
  const CARD_STEP = 270;
  const INTERVAL_MS = 5000;

  const FEATURES = [
    {
      id: "product",
      image: "/static/hero-product.png",
      background: "/static/hero-product.png",
      title: "Trendyol AI — Satış Paneli",
      subtitle: "Ürün Oluşturma",
      text: "Fotoğraf yükle, yapay zeka ürünü tanır, fiyat araştırır, kategori ve açıklamayı hazırlar — tek tıkla Trendyol'a gönder.",
      bgPos: "left center",
    },
    {
      id: "reviews",
      image: "https://images.unsplash.com/photo-1556740758-90de374c12ad?auto=format&fit=crop&w=2400&q=80",
      background: "https://images.unsplash.com/photo-1556740758-90de374c12ad?auto=format&fit=crop&w=600&q=80",
      title: "Müşteri Geri Bildirimi",
      subtitle: "Yorum Analiz",
      text: "Trendyol ürün linkinden veya CSV'den yorum çek. Her ürün için ne iyi gitti, ne yapmalısın, ne yapmamalısın — kanıtlı AI önerileri.",
      bgPos: "center",
    },
    {
      id: "returns",
      image: "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=2400&q=80",
      background: "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=600&q=80",
      title: "İade Takibi",
      subtitle: "İade Analiz",
      text: "İade CSV'nizi yükleyin. Sebep dağılımı, en çok iade edilen ürünler ve azaltma önerileri — tek ekranda.",
      bgPos: "center",
    },
    {
      id: "cargo",
      image: "https://images.unsplash.com/photo-1566576912321-d58ddd7a6088?auto=format&fit=crop&w=2400&q=80",
      background: "https://images.unsplash.com/photo-1566576912321-d58ddd7a6088?auto=format&fit=crop&w=600&q=80",
      title: "Teslimat Takibi",
      subtitle: "Kargo Analiz",
      text: "Sipariş ve kargo CSV'nizi yükleyin. Yolda, teslim, geciken gönderiler — firma ve durum filtresiyle takip edin.",
      bgPos: "center",
    },
    {
      id: "analysis",
      image: "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=2400&q=80",
      background: "https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=600&q=80",
      title: "AI Satış Asistanı",
      subtitle: "Genel Danışman",
      text: "Mağazanı sor; fiyat karşılaştırma, yorum analizi, benzer ürün kıyaslaması — kanıtlı, anlaşılır cevaplar al.",
      bgPos: "center",
    },
  ];

  const ASSISTANT_MAP = {
    reviews: "reviews",
    returns: "returns",
    cargo: "cargo",
    analysis: "analysis",
  };

  let currentIndex = 0;
  let translateX = 0;
  let autoPlay = false;
  let autoTimer = null;
  let progressTimer = null;

  function pad(n) { return String(n).padStart(2, "0"); }

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  function setBackground(feat) {
    const bg = $("geBg");
    if (!bg) return;
    bg.style.backgroundImage = `url('${feat.image}')`;
    bg.style.backgroundPosition = feat.bgPos || "center";
  }

  function updateHero(animate) {
    const feat = FEATURES[currentIndex];
    const title = $("geHeroTitle");
    const subtitle = $("geHeroSubtitle");
    const text = $("geHeroText");
    const body = $("geHeroBody");
    if (title) title.textContent = feat.title;
    if (subtitle) subtitle.textContent = feat.subtitle;
    if (text) text.textContent = feat.text;
    if (body) {
      body.classList.remove("anim-in");
      if (animate) {
        void body.offsetWidth;
        body.classList.add("anim-in");
      }
    }
    setBackground(feat);
  }

  function calcProgressWidth() {
    if (currentIndex === 0) return 0;
    return (currentIndex / (FEATURES.length - 1)) * 100;
  }

  function renderCards() {
    const track = $("geCardsTrack");
    if (!track) return;
    const rest = FEATURES.slice(1);
    track.innerHTML = rest.map((f, i) => {
      const featIndex = i + 1;
      const opened = currentIndex >= 1 && featIndex === currentIndex;
      return `
        <button type="button" class="ge-card${opened ? " opened" : ""}"
          data-idx="${featIndex}"
          style="background-image:url('${f.background}');background-position:${f.bgPos || "center"}">
          <h2 class="ge-card-title">${esc(f.title)}</h2>
          <h3 class="ge-card-subtitle">${esc(f.subtitle)}</h3>
        </button>`;
    }).join("");

    track.style.transform = `translateX(${translateX}px)`;
    track.querySelectorAll(".ge-card").forEach(card => {
      card.onclick = () => goToIndex(+card.dataset.idx, false);
    });
  }

  function updateControls() {
    const counter = $("geCounter");
    const bar = $("geProgressBar");
    if (counter) counter.textContent = pad(currentIndex + 1);
    if (bar) bar.style.width = calcProgressWidth() + "%";
  }

  function goToIndex(index, fromAuto) {
    if (index < 0 || index >= FEATURES.length || index === currentIndex) return;
    currentIndex = index;
    translateX = -currentIndex * CARD_STEP;
    updateHero(true);
    renderCards();
    updateControls();
    if (!fromAuto) restartAuto();
  }

  function step(dir) {
    const next = currentIndex + dir;
    if (next < 0 || next >= FEATURES.length) return;
    goToIndex(next, false);
  }

  function onTopProgressDone() {
    if (currentIndex < FEATURES.length - 1) {
      goToIndex(currentIndex + 1, true);
    }
    restartAuto();
  }

  function restartAuto() {
    clearTimeout(autoTimer);
    clearTimeout(progressTimer);
    const topBar = $("geTopProgressBar");
    if (!autoPlay || !topBar) return;

    topBar.classList.remove("running");
    void topBar.offsetWidth;

    if (currentIndex < FEATURES.length - 1) {
      topBar.classList.add("running");
      progressTimer = setTimeout(onTopProgressDone, INTERVAL_MS);
    }
  }

  function startAuto() {
    autoPlay = true;
    restartAuto();
  }

  function stopAuto() {
    autoPlay = false;
    clearTimeout(autoTimer);
    clearTimeout(progressTimer);
    $("geTopProgressBar")?.classList.remove("running");
  }

  window.openModule = function openModule(id) {
    stopAuto();
    const feat = FEATURES.find(f => f.id === id);
    if (!feat) return;
    $("appHome")?.classList.add("hidden");
    $("appModule")?.classList.remove("hidden");
    document.body.classList.remove("ge-home-active");
    document.body.classList.add("in-module");
    const title = $("moduleTitle");
    if (title) title.textContent = feat.subtitle;

    if (id === "product") {
      $("viewListing")?.classList.remove("hidden");
      $("viewAssistant")?.classList.add("hidden");
    } else {
      $("viewListing")?.classList.add("hidden");
      $("viewAssistant")?.classList.remove("hidden");
      $("asHubMenu")?.classList.add("hidden");
      $("asSectionView")?.classList.remove("hidden");
      const sec = ASSISTANT_MAP[id] || "reviews";
      const open = () => {
        if (window.AssistantShell) window.AssistantShell.openSection(sec);
      };
      if (typeof loadAssistant === "function") loadAssistant().then(open);
      else open();
    }
    window.scrollTo(0, 0);
  };

  window.goAppHome = function goAppHome() {
    $("appModule")?.classList.add("hidden");
    $("appHome")?.classList.remove("hidden");
    document.body.classList.add("ge-home-active");
    document.body.classList.remove("in-module");
    startAuto();
    window.scrollTo(0, 0);
  };

  window.initAppShell = function initAppShell() {
    document.body.classList.add("ge-home-active");
    updateHero(false);
    renderCards();
    updateControls();

    $("gePrev")?.addEventListener("click", () => step(-1));
    $("geNext")?.addEventListener("click", () => step(1));
    $("geDiscoverBtn")?.addEventListener("click", () => openModule(FEATURES[currentIndex].id));
    $("appBackBtn")?.addEventListener("click", goAppHome);
    $("navHome")?.addEventListener("click", e => { e.preventDefault(); goAppHome(); });

    const home = $("appHome");
    home?.addEventListener("mouseenter", stopAuto);
    home?.addEventListener("mouseleave", startAuto);

    document.addEventListener("keydown", e => {
      if ($("appHome")?.classList.contains("hidden")) return;
      if (e.key === "ArrowLeft") step(-1);
      if (e.key === "ArrowRight") step(1);
      if (e.key === "Enter") openModule(FEATURES[currentIndex].id);
    });

    window.addEventListener("load", startAuto);
    startAuto();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAppShell);
  } else {
    initAppShell();
  }
})();
