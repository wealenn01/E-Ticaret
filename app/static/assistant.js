/** Mağaza Asistanı — otomasyonv3 UI mantığı (TrendolAi pastel tema) */
(function () {
  const $ = id => document.getElementById(id);
  const AI = () => window.AssistantInsight;
  const PLATFORMS = [
    { id: "trendyol", label: "Trendyol" },
    { id: "hepsiburada", label: "Hepsiburada" },
    { id: "n11", label: "N11" },
    { id: "amazon", label: "Amazon" },
  ];
  const SECTIONS = ["reviews", "analysis", "cargo", "returns"];
  const HUB_TILES = [
    { id: "reviews", title: "Yorumlar", image: "/static/hub-reviews.svg", preview: "Link veya CSV ile yorum çek, ürün bazlı analiz gör" },
    { id: "returns", title: "İade", image: "/static/hub-returns.svg", preview: "İade CSV yükle, sebep ve adet özetini incele" },
    { id: "cargo", title: "Kargo", image: "/static/hub-cargo.svg", preview: "Sipariş takibi, firma filtresi ve teslimat durumu" },
    { id: "analysis", title: "Danışman", image: "/static/hub-consultant.svg", preview: "AI ile sor, öneri al, mağaza performansını karşılaştır" },
  ];
  const SECTION_TITLES = { reviews: "Yorumlar", analysis: "Danışman", cargo: "Kargo", returns: "İade" };
  let state = {
    view: "hub", platform: "trendyol", sellerModel: null, section: "reviews",
    products: [], allProductCount: 0, stats: null, insights: null,
    expandedSku: null, selectedSku: null,
    askLoading: false, importMode: "link", askResult: null, askError: null,
  };

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch { throw new Error("Sunucu hatası (" + r.status + ")"); }
    if (!r.ok) throw new Error(data.detail || "İstek başarısız");
    return data;
  }

  function toast(msg, type) {
    if (typeof window.toast === "function") window.toast(msg, type || "ok", 6000);
  }

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  }

  function tileImageHtml(p) {
    if (p.image_url) {
      return `<img class="product-tile-img" src="${esc(p.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
    }
    const init = AI().productInitial(p.product_name);
    return `<div class="product-tile-letter" aria-hidden>${esc(init)}</div>`;
  }

  function chipThumbHtml(p) {
    if (p.image_url) {
      return `<img class="chip-img" src="${esc(p.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
    }
    return `<span class="chip-letter">${esc(AI().productInitial(p.product_name))}</span>`;
  }

  function renderEvidenceItems(items, variant) {
    return items.map(item => `
      <article class="evidence-card evidence-${variant}">
        <p class="evidence-title">${esc(item.title)}</p>
        <p class="evidence-detail">${esc(item.detail)}</p>
        ${variant !== "good" && item.evidenceCount ? `<p class="evidence-stat">${item.evidenceCount} yorumda geçiyor · toplamın %${item.evidencePct}'i</p>` : ""}
        ${item.sampleComments?.length ? `<details class="evidence-comments"><summary>Yorumlara bak (${item.sampleComments.length})</summary><ul>${item.sampleComments.map(c => `<li>★${c.rating} — ${esc(c.text)}</li>`).join("")}</ul></details>` : ""}
      </article>`).join("");
  }

  function renderProductInsightPanel(product) {
    const insight = AI().buildProductReviewInsight(product, state.sellerModel);
    return `
      <div class="product-insight-shell">
        <div class="product-insight-panel">
          <header class="insight-header insight-verdict-${insight.verdictTone}">
            <h3>${esc(product.product_name)}</h3>
            <p class="insight-stats-line">${esc(insight.statsLine)}</p>
            <div class="insight-sentiment-pills">
              <span class="insight-pill pos">Olumlu %${insight.chart.positive}</span>
              <span class="insight-pill neu">Nötr %${insight.chart.neutral}</span>
              <span class="insight-pill neg">Olumsuz %${insight.chart.negative}</span>
            </div>
            <p class="insight-verdict-text">${esc(insight.verdict)}</p>
          </header>
          <div class="insight-sections-stack">
            <section class="insight-section insight-section-good">
              <h4>İyi yaptıklarınız</h4>
              ${renderEvidenceItems(insight.goodPractices, "good")}
            </section>
            <section class="insight-section insight-section-do">
              <h4>Yapmanız gerekenler</h4>
              ${renderEvidenceItems(insight.recommendations, "do")}
            </section>
            <section class="insight-section insight-section-warn">
              <h4>Yapmamanız gerekenler</h4>
              ${renderEvidenceItems(insight.avoidActions, "dont")}
            </section>
          </div>
        </div>
      </div>`;
  }

  function renderProductGrid() {
    const el = $("productGrid");
    if (!el) return;
    if (!state.products.length) {
      el.innerHTML = '<p class="hint">Henüz ürün verisi yok — CSV veya link ile yorum ekleyin.</p>';
      return;
    }
    el.innerHTML = state.products.map(p => {
      const open = state.expandedSku === p.sku;
      return `
        <div class="product-review-item${open ? " open" : ""}">
          <div class="product-review-tile-wrap">
            <button type="button" class="product-delete-btn" data-del="${esc(p.sku)}" title="Ürünü sil" aria-label="Ürünü sil">×</button>
            <button type="button" class="product-review-tile${open ? " open" : ""}" data-sku="${esc(p.sku)}">
              ${tileImageHtml(p)}
              <div class="product-tile-title-strip"><span class="product-tile-title-text">${esc(p.product_name)}</span></div>
              ${open ? `<span class="product-review-tile-chevron" aria-hidden>▲</span>` : ""}
            </button>
          </div>
          ${open ? `<div class="product-review-drawer">${renderProductInsightPanel(p)}</div>` : ""}
        </div>`;
    }).join("");

    el.querySelectorAll("[data-sku]").forEach(btn => btn.onclick = () => {
      state.expandedSku = state.expandedSku === btn.dataset.sku ? null : btn.dataset.sku;
      renderProductGrid();
    });
    el.querySelectorAll("[data-del]").forEach(btn => btn.onclick = async e => {
      e.stopPropagation();
      if (!confirm("Bu ürün ve tüm yorumları silinsin mi?")) return;
      try {
        await api("/api/assistant/products/" + encodeURIComponent(btn.dataset.del), { method: "DELETE" });
        if (state.expandedSku === btn.dataset.del) state.expandedSku = null;
        await refreshData();
        toast("Ürün silindi.", "ok");
      } catch (err) { toast(err.message, "err"); }
    });
  }

  function renderImportPanel() {
    const copy = AI().SCRAPE_COPY[state.platform] || AI().SCRAPE_COPY.trendyol;
    const linkPanel = `
      <div class="scrape-zone">
        <strong>${copy.title}</strong>
        <p class="hint">${copy.hint}</p>
        <input id="scrapeUrlInput" type="url" placeholder="${copy.placeholder}">
        <button id="scrapeSubmitBtn" class="primary-sm">Analiz</button>
        <p id="scrapeSuccessHint" class="hint ok-line hidden"></p>
        <p id="scrapeError" class="hint err hidden"></p>
      </div>`;
    const csvPanel = `
      <div class="drop" id="csvDropZone">
        <span class="ic">📄</span>
        <p class="big">Yorum CSV'sini buraya sürükle</p>
        <p>product_name, sku, rating, comment_text, review_date</p>
        <input type="file" id="csvFileInput" accept=".csv" class="hidden">
      </div>`;
    $("importPanel").innerHTML = `
      <div class="import-tabs">
        <button class="import-tab${state.importMode === "link" ? " on" : ""}" data-mode="link">Link ile çek</button>
        <button class="import-tab${state.importMode === "csv" ? " on" : ""}" data-mode="csv">CSV ile</button>
      </div>
      ${state.importMode === "link" ? linkPanel : csvPanel}`;

    $("importPanel").querySelectorAll(".import-tab").forEach(t => t.onclick = () => {
      state.importMode = t.dataset.mode;
      renderImportPanel();
      bindImportEvents();
    });
    bindImportEvents();
  }

  function bindImportEvents() {
    const scrapeBtn = $("scrapeSubmitBtn");
    if (scrapeBtn) {
      scrapeBtn.onclick = async () => {
        const url = ($("scrapeUrlInput")?.value || "").trim();
        const errEl = $("scrapeError");
        const err = AI().validateScrapeUrl(url, state.platform);
        if (err) { errEl.textContent = err; errEl.classList.remove("hidden"); return; }
        errEl.classList.add("hidden");
        scrapeBtn.disabled = true; scrapeBtn.textContent = "Analiz ediliyor…";
        try {
          const r = await api("/api/assistant/scrape-reviews", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
          });
          const hint = $("scrapeSuccessHint");
          if (hint) {
            let msg = r.added + " yorum eklendi";
            const total = r.meta?.total_available;
            if (total && total > r.added) {
              msg += " (sitede " + total.toLocaleString("tr-TR") + " yorum var — en yeniler alındı)";
            }
            if (r.meta?.product_name) msg += " — " + r.meta.product_name;
            hint.textContent = msg;
            hint.classList.remove("hidden");
          }
          const inp = $("scrapeUrlInput");
          if (inp) inp.value = "";
          await refreshData();
        } catch (e) { errEl.textContent = e.message; errEl.classList.remove("hidden"); }
        finally { scrapeBtn.disabled = false; scrapeBtn.textContent = "Analiz"; }
      };
    }
    const drop = $("csvDropZone"), file = $("csvFileInput");
    if (drop && file) {
      drop.onclick = () => file.click();
      drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
      drop.ondragleave = () => drop.classList.remove("over");
      drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) uploadCsv(e.dataTransfer.files[0]); };
      file.onchange = () => file.files[0] && uploadCsv(file.files[0]);
    }
  }

  async function uploadCsv(f) {
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api("/api/assistant/import-reviews", { method: "POST", body: fd });
      toast(r.added + " yorum eklendi (toplam " + r.total + ")", "ok");
      await refreshData();
    } catch (e) { toast(e.message, "err"); }
  }

  async function refreshData() {
    const [products, allProducts, stats, settings, insights] = await Promise.all([
      api("/api/assistant/products?platform=" + state.platform),
      api("/api/assistant/products?platform=all"),
      api("/api/assistant/stats?platform=" + state.platform),
      api("/api/assistant/settings"),
      api("/api/assistant/insights").catch(() => null),
    ]);
    state.products = products.products || [];
    state.allProductCount = allProducts.total_products || 0;
    state.stats = stats;
    state.insights = insights;
    state.platform = settings.platform || state.platform;
    state.sellerModel = settings.seller_model || null;
    renderAll();
  }

  function renderHubMenu() {
    const grid = $("asHubGrid");
    if (!grid) return;
    grid.innerHTML = HUB_TILES.map(t => `
      <button type="button" class="hub-tile" data-sec="${t.id}">
        <div class="hub-tile-img-wrap">
          <img src="${t.image}" alt="">
          <div class="hub-tile-overlay"><span class="hub-tile-preview">${esc(t.preview)}</span></div>
        </div>
        <span class="hub-tile-title">${esc(t.title)}</span>
      </button>`).join("");
    grid.querySelectorAll(".hub-tile").forEach(btn => {
      btn.onclick = () => openSection(btn.dataset.sec);
    });
  }

  function openSection(sec) {
    state.view = "section";
    state.section = sec;
    state.expandedSku = null;
    showHub(false);
    renderAll();
  }

  function goHub() {
    state.view = "hub";
    state.expandedSku = null;
    renderAll();
  }

  function showHub(on) {
    $("asHubMenu")?.classList.toggle("hidden", !on);
    $("asSectionView")?.classList.toggle("hidden", on);
  }

  function renderSectionToolbar() {
    const back = $("asBackBtn");
    const title = $("asBarTitle");
    if (back) back.classList.toggle("hidden", state.view === "hub");
    if (title) {
      title.textContent = state.view === "hub"
        ? "Mağaza Asistanı"
        : (SECTION_TITLES[state.section] || "Mağaza Asistanı");
    }
  }

  function renderSidebar() { /* hub menü kullanılıyor */ }

  function renderPlatformBar() {
    document.querySelectorAll(".platform-select").forEach(sel => {
      sel.innerHTML = PLATFORMS.map(p =>
        `<option value="${p.id}"${p.id === state.platform ? " selected" : ""}>${p.label}</option>`
      ).join("");
      if (sel._bound) return;
      sel._bound = true;
      sel.onchange = async () => {
        state.platform = sel.value;
        document.querySelectorAll(".platform-select").forEach(s => { s.value = state.platform; });
        state.expandedSku = null;
        state.selectedSku = null;
        await api("/api/assistant/settings", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ platform: state.platform }),
        });
        await refreshData();
      };
    });
  }

  function renderReviewsSection() {
    showPanel("reviews");
    renderImportPanel();
    const platLabel = PLATFORMS.find(p => p.id === state.platform)?.label || state.platform;
    if (state.stats) {
      $("revCountHint").textContent = state.products.length
        ? `${state.products.length} ${platLabel} ürün — analiz için kutuya tıklayın`
        : "";
    }
    const hintEl = $("crossPlatformHint");
    if (hintEl) {
      const hidden = state.allProductCount - state.products.length;
      if (hidden > 0) {
        hintEl.textContent = `${hidden} ürün diğer platformdan; yalnızca ${platLabel} mağazasında gösterilir.`;
        hintEl.classList.remove("hidden");
      } else {
        hintEl.classList.add("hidden");
      }
    }
    renderProductGrid();
  }

  function renderAnalysisSection() {
    showPanel("analysis");
    const needsModel = !state.sellerModel;
    $("sellerModelPicker").innerHTML = `
      <strong>Satış modeliniz</strong>
      <p class="hint">Danışman önerileri buna göre değişir.</p>
      <div class="seller-model-options">
        <button type="button" class="seller-model-card${state.sellerModel === "manufacturer" ? " on" : ""}" data-model="manufacturer">
          <span class="sm-title">Kendim üretiyorum</span>
          <span class="sm-desc">Ürünü siz veya atölyeniz üretiyor.</span>
        </button>
        <button type="button" class="seller-model-card${state.sellerModel === "reseller" ? " on" : ""}" data-model="reseller">
          <span class="sm-title">Toptan alıp satıyorum</span>
          <span class="sm-desc">Fabrika/toptancıdan alıp satıyorsunuz.</span>
        </button>
      </div>
      ${needsModel ? '<p class="hint warn">Soru sormak için önce satış modelinizi seçin.</p>' : ""}`;

    $("sellerModelPicker").querySelectorAll("[data-model]").forEach(btn => btn.onclick = async () => {
      state.sellerModel = btn.dataset.model;
      await api("/api/assistant/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seller_model: state.sellerModel }),
      });
      renderAnalysisSection();
      if (state.expandedSku) renderProductGrid();
    });

    const selected = state.selectedSku ? state.products.find(p => p.sku === state.selectedSku) : null;
    $("productPicker").innerHTML = state.products.length ? `
      <strong>Ürün seçin (isteğe bağlı)</strong>
      <p class="hint">${selected ? `Şu an: ${selected.product_name}` : "Hiçbir ürün seçilmedi — genel mağaza analizi."}</p>
      <div class="consultant-product-grid">
        <button type="button" class="consultant-chip${!state.selectedSku ? " on" : ""}" data-sku="">Tüm mağaza</button>
        ${state.products.map(p => `
          <button type="button" class="consultant-chip${state.selectedSku === p.sku ? " on" : ""}" data-sku="${esc(p.sku)}" title="${esc(p.product_name)}">
            ${chipThumbHtml(p)}
            <span>${esc(p.product_name.length > 28 ? p.product_name.slice(0, 26) + "…" : p.product_name)}</span>
          </button>`).join("")}
      </div>` : "";

    $("productPicker").querySelectorAll(".consultant-chip").forEach(btn => btn.onclick = () => {
      state.selectedSku = btn.dataset.sku || null;
      renderAnalysisSection();
      loadPresets();
    });

    loadPresets();
    renderAnalysisFooter();
  }

  function renderAnalysisFooter() {
    const stats = state.stats || {};
    const total = stats.total_reviews || 0;
    const posPct = total ? Math.round(100 * (stats.positive_reviews || 0) / total) : 0;
    const negPct = total ? Math.round(100 * (stats.negative_reviews || 0) / total) : 0;
    const chartsEl = $("storeCharts");
    if (chartsEl) {
      if (total > 0) {
        const breakdown = stats.complaint_breakdown || {};
        chartsEl.innerHTML = `<div class="analysis-charts">
          <div class="chart-card">
            <strong>Mağaza özeti</strong>
            <p class="hint">${total} değerlendirme · %${posPct} olumlu · %${negPct} olumsuz (1–3★)</p>
            ${AI().miniBarHtml("Olumlu", stats.positive_reviews || 0, total, "#34d399")}
            ${AI().miniBarHtml("Olumsuz", stats.negative_reviews || 0, total, "#f87171")}
          </div>
          <div class="chart-card">
            <strong>Şikayet konuları</strong>
            ${Object.entries(breakdown).filter(([k]) => k !== "genel_memnuniyetsizlik").slice(0, 5)
              .map(([k, v]) => AI().miniBarHtml(AI().formatComplaintLabel(k), v, total, "#8b7cf6")).join("")}
          </div>
        </div>`;
      } else {
        chartsEl.innerHTML = "";
      }
    }
    const insightsEl = $("insightsBlock");
    if (insightsEl) {
      insightsEl.innerHTML = "";
      insightsEl.classList.add("hidden");
    }
    renderAnswerSection();
  }

  function renderAnswerSection() {
    const el = $("answerSection");
    if (!el) return;
    if (state.askLoading) {
      el.innerHTML = `<div class="consultant-answer-card"><p class="hint">AI inceliyor, lütfen bekleyin…</p></div>`;
      return;
    }
    if (state.askError) {
      el.innerHTML = `<div class="consultant-answer-card err"><p class="hint err">${esc(state.askError)}</p></div>`;
      return;
    }
    if (!state.askResult) {
      el.innerHTML = "";
      return;
    }
    const d = state.askResult;
    let extra = AI().renderConsultantAnswer(d.answer, d.recommendations, d.sorted_complaints);
    if (d.evidence?.length) {
      extra += `<ul class="consultant-evidence-list">${d.evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul>`;
    }
    if (d.price_comparison?.length) {
      extra += `<div class="price-compare-block"><strong>Hepsiburada fiyat listesi</strong><table class="ship-table"><thead><tr><th>Ürün</th><th>Fiyat</th><th>Satıcı</th></tr></thead><tbody>${d.price_comparison.map(row => `<tr><td>${row.url ? `<a href="${esc(row.url)}" target="_blank" rel="noopener">${esc(row.name)}</a>` : esc(row.name)}</td><td>${Number(row.price).toLocaleString("tr-TR", { minimumFractionDigits: 2 })} TL</td><td>${esc(row.merchant || "—")}</td></tr>`).join("")}</tbody></table></div>`;
    }
    if (d.review_benchmark) {
      const b = d.review_benchmark;
      extra += `<div class="market-benchmark-section"><strong>Rakip yorum analizi</strong><p class="hint">${esc(b.summary || "")}</p></div>`;
    }
    el.innerHTML = `<div class="consultant-answer-card"><strong class="consultant-answer-title">Danışman cevabı</strong>${extra}</div>`;
  }

  async function loadPresets() {
    const selected = state.selectedSku ? state.products.find(p => p.sku === state.selectedSku) : null;
    const groups = await api("/api/assistant/presets" + (selected ? "?product_name=" + encodeURIComponent(selected.product_name) : ""));
    $("presetGroups").innerHTML = groups.map(g => `
      <section class="preset-group">
        <h4>${esc(g.title)}</h4>
        <p class="hint">${esc(g.description)}</p>
        <div class="preset-btns">${g.questions.map(q =>
          `<button type="button" class="preset-btn" data-q="${esc(q.question)}">${esc(q.label)}</button>`).join("")}</div>
      </section>`).join("");
    $("presetGroups").querySelectorAll(".preset-btn").forEach(btn => btn.onclick = () => {
      $("askInput").value = btn.dataset.q;
      submitAsk();
    });
  }

  async function submitAsk() {
    const q = ($("askInput")?.value || "").trim();
    if (!q || state.askLoading || !state.sellerModel) {
      if (!state.sellerModel) toast("Önce satış modelinizi seçin.", "err");
      return;
    }
    state.askLoading = true;
    state.askError = null;
    state.askResult = null;
    $("askBtn").disabled = true;
    renderAnswerSection();
    try {
      const d = await api("/api/assistant/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, sku: state.selectedSku || null }),
      });
      state.askResult = d;
      $("askInput").value = "";
    } catch (e) {
      state.askError = e.message;
    } finally {
      state.askLoading = false;
      $("askBtn").disabled = false;
      renderAnswerSection();
      if (state.section === "analysis") renderAnalysisFooter();
    }
  }

  function renderCargoSection() {
    showPanel("cargo");
    bindCsvDrop("shipDropZone", "shipFileInput", "/api/assistant/import-shipments", async () => {
      await loadShipments();
    });
    loadShipments();
  }

  function renderReturnsSection() {
    showPanel("returns");
    bindCsvDrop("retDropZone", "retFileInput", "/api/assistant/import-returns", async () => {
      const s = state.stats || {};
      $("retSummaryBanner").textContent = (s.total_returns || 0) > 0
        ? `Toplam ${s.total_returns} iade · ${s.return_quantity} adet` : "";
      $("retSummaryBanner").classList.toggle("hidden", !(s.total_returns > 0));
    });
    const s = state.stats || {};
    $("retSummaryBanner").textContent = (s.total_returns || 0) > 0
      ? `Toplam ${s.total_returns} iade · ${s.return_quantity} adet` : "";
    $("retSummaryBanner").classList.toggle("hidden", !(s.total_returns > 0));
  }

  function bindCsvDrop(dropId, fileId, endpoint, cb) {
    const drop = $(dropId), file = $(fileId);
    if (!drop || drop._bound) return;
    drop._bound = true;
    drop.onclick = () => file.click();
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0], endpoint, cb); };
    file.onchange = () => file.files[0] && uploadFile(file.files[0], endpoint, cb);
  }

  async function uploadFile(f, endpoint, cb) {
    const fd = new FormData(); fd.append("file", f);
    try {
      const r = await api(endpoint, { method: "POST", body: fd });
      toast((r.added || r.imported || 0) + " kayıt eklendi", "ok");
      await refreshData();
      if (cb) cb();
    } catch (e) { toast(e.message, "err"); }
  }

  async function loadShipments() {
    const d = await api("/api/assistant/shipments");
    const totals = { total: d.total, in_transit: d.in_transit, delivered: d.delivered };
    $("shipMetrics").innerHTML = `
      <div class="metric-card"><div class="l">Toplam sipariş</div><div class="v">${totals.total}</div></div>
      <div class="metric-card warn"><div class="l">Yolda</div><div class="v">${totals.in_transit}</div></div>
      <div class="metric-card pos"><div class="l">Teslim</div><div class="v">${totals.delivered}</div></div>`;

    if (!d.shipments?.length) {
      $("shipTableWrap").innerHTML = '<p class="hint">Henüz kargo kaydı yok. Kargo CSV yükleyin.</p>';
      return;
    }

    const statusFilter = $("shipStatusFilter")?.value || "all";
    const carrierFilter = $("shipCarrierFilter")?.value || "all";
    let rows = d.shipments;
    if (statusFilter === "active") rows = rows.filter(s => s.status === "shipped" || s.status === "in_transit");
    else if (statusFilter !== "all") rows = rows.filter(s => s.status === statusFilter);
    if (carrierFilter !== "all") rows = rows.filter(s => s.carrier === carrierFilter);

    const carriers = [...new Set(d.shipments.map(s => s.carrier).filter(Boolean))].sort();
    $("shipCarrierFilter").innerHTML = `<option value="all">Tüm firmalar</option>${carriers.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}`;

    $("shipTableWrap").innerHTML = `
      <p class="hint ship-count-line">${rows.length} / ${d.shipments.length} sipariş gösteriliyor</p>
      <table class="ship-table">
        <thead><tr><th>Sipariş</th><th>Ürün</th><th>Firma</th><th>Takip no</th><th>Durum</th><th>Kargoya verildi</th><th>Teslim / süre</th></tr></thead>
        <tbody>${rows.map(s => {
          const url = AI().trackingUrl(s.carrier, s.tracking_number);
          const days = s.shipped_date ? Math.max(0, Math.floor((Date.now() - new Date(s.shipped_date).getTime()) / 86400000)) : null;
          return `<tr>
            <td><strong>${esc(s.order_id)}</strong></td>
            <td>${esc(s.product_name)}<br><span class="hint">${esc(s.sku)}</span></td>
            <td>${esc(s.carrier)}</td>
            <td>${url ? `<a href="${url}" target="_blank" rel="noopener">${esc(s.tracking_number)}</a>` : esc(s.tracking_number)}</td>
            <td><span class="st-badge st-${s.status}">${d.status_labels[s.status] || s.status}</span></td>
            <td>${esc(s.shipped_date) || "—"}</td>
            <td>${s.delivered_date ? esc(s.delivered_date) : (days !== null ? days + " gündür yolda" : "—")}</td>
          </tr>`;
        }).join("") || `<tr><td colspan="7" class="hint">Bu filtreye uygun sipariş yok.</td></tr>`}
        </tbody>
      </table>`;
  }

  function showPanel(name) {
    SECTIONS.forEach(s => {
      const el = $("panel" + s.charAt(0).toUpperCase() + s.slice(1));
      if (el) el.classList.toggle("hidden", s !== name);
    });
  }

  function renderAll() {
    renderPlatformBar();
    renderSectionToolbar();
    if (state.view === "hub") {
      showHub(true);
      renderHubMenu();
      return;
    }
    showHub(false);
    if (state.section === "reviews") renderReviewsSection();
    else if (state.section === "analysis") renderAnalysisSection();
    else if (state.section === "cargo") renderCargoSection();
    else if (state.section === "returns") renderReturnsSection();
  }

  window.loadAssistant = async function () {
    try {
      state.view = "hub";
      const settings = await api("/api/assistant/settings");
      state.platform = settings.platform || "trendyol";
      state.sellerModel = settings.seller_model || null;
      await refreshData();
    } catch (e) { toast(e.message, "err"); }
  };

  window.initAssistantEvents = function () {
    if ($("asBackBtn")) $("asBackBtn").onclick = goHub;
    $("askBtn").onclick = submitAsk;
    $("askInput")?.addEventListener("keydown", e => { if (e.key === "Enter") submitAsk(); });
    $("shipStatusFilter")?.addEventListener("change", loadShipments);
    $("shipCarrierFilter")?.addEventListener("change", loadShipments);
    bindCsvDrop("retDropZone", "retFileInput", "/api/assistant/import-returns", () => {});
    bindCsvDrop("shipDropZone", "shipFileInput", "/api/assistant/import-shipments", loadShipments);
  };
})();
