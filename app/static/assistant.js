/** Mağaza Asistanı — otomasyonv3 UI mantığı (AnalizAi pastel tema) */
(function () {
  const $ = id => document.getElementById(id);
  const AI = () => window.AssistantInsight;
  const PLATFORMS = [
    { id: "trendyol", label: "Trendyol" },
    { id: "hepsiburada", label: "Hepsiburada" },
    { id: "n11", label: "N11" },
    { id: "amazon", label: "Amazon" },
  ];
  const SECTIONS = ["reviews", "analysis", "reports", "cargo", "returns"];
  const HUB_TILES = [
    { id: "reviews", title: "Yorumlar", image: "/static/hub-reviews.svg", preview: "Link veya CSV ile yorum çek, ürün bazlı analiz gör" },
    { id: "analysis", title: "Danışman", image: "/static/hub-consultant.svg", preview: "AI ile sor, öneri al, mağaza performansını karşılaştır" },
    { id: "reports", title: "Raporlar", image: "/static/hub-reports.svg", preview: "7 / 15 / 30 günlük yorum ve iade grafikleri" },
    { id: "returns", title: "İade", image: "/static/hub-returns.svg", preview: "İade CSV yükle, sebep ve adet özetini incele" },
    { id: "cargo", title: "Kargo", image: "/static/hub-cargo.svg", preview: "Sipariş takibi, firma filtresi ve teslimat durumu" },
  ];
  const SECTION_TITLES = { reviews: "Yorumlar", analysis: "Danışman", reports: "Raporlar", cargo: "Kargo", returns: "İade Paneli" };
  let state = {
    view: "hub", platform: "trendyol", sellerModel: null, section: "reviews",
    products: [], allProducts: [], allProductCount: 0,
    stats: null, consultantStats: null, insights: null,
    reportDays: 7, reportChart: "pie", reportReviewMode: "sentiment",
    reportData: null, reportLoading: false, reportError: null, reportDemo: false,
    expandedSku: null, selectedSku: null, reviewsMaximized: false,
    cargoPeriod: 7, cargoPlatform: "all", cargoStatus: "all",
    returnsPeriod: 7, returnsPlatform: "all",
    cargoData: null, returnsData: null,
    askLoading: false, importMode: "link", askResult: null, askError: null,
  };

  function formatApiError(detail) {
    if (!detail) return "İstek başarısız";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map(d => d.msg || d.message || JSON.stringify(d)).join("; ");
    }
    if (typeof detail === "object") return detail.msg || detail.message || JSON.stringify(detail);
    return String(detail);
  }

  async function api(url, opts) {
    const fetchApi = (typeof globalThis.api === "function") ? globalThis.api : null;
    if (fetchApi && !opts?._raw) {
      try { return await fetchApi(url, opts); } catch (e) { throw e; }
    }
    const path = String(url || "");
    const abs = path.startsWith("http") || path.startsWith("/") ? path : "/" + path.replace(/^\.\//, "");
    const r = await fetch(abs, { credentials: "same-origin", ...opts });
    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch { const e = new Error("Sunucu hatası (" + r.status + ")"); e.status = r.status; throw e; }
    if (!r.ok) {
      const msg = formatApiError(data?.detail) || "İstek başarısız (" + r.status + ")";
      const e = new Error(r.status === 404 ? "Rapor API bulunamadı — sunucuyu yeniden başlatın (Ctrl+Shift+R)." : msg);
      e.status = r.status;
      throw e;
    }
    return data;
  }

  function toast(msg, type) {
    if (typeof window.toast === "function") window.toast(msg, type || "ok", 6000);
  }

  function esc(s) {
    if (s == null) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function parseIsoDate(raw) {
    if (!raw) return null;
    const d = new Date(String(raw).slice(0, 10));
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatOpsDate(raw) {
    const d = parseIsoDate(raw);
    if (!d) return "—";
    return d.toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
  }

  function daysSince(raw) {
    const d = parseIsoDate(raw);
    if (!d) return null;
    return Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
  }

  function inPeriod(raw, periodDays) {
    const d = parseIsoDate(raw);
    if (!d) return true;
    const start = new Date();
    start.setDate(start.getDate() - periodDays);
    start.setHours(0, 0, 0, 0);
    return d >= start;
  }

  function opsThumb(name) {
    return `<span class="ops-thumb">${esc(AI().productInitial(name))}</span>`;
  }

  function opsCustomerLabel(orderId) {
    const s = String(orderId || "M");
    const a = s.charCodeAt(0) % 26;
    const b = (s.charCodeAt(s.length - 1) || 65) % 26;
    return `${String.fromCharCode(65 + a)}. ${String.fromCharCode(65 + b)}.`;
  }

  function opsMiniBars(values) {
    const max = Math.max(...values, 1);
    return `<div class="ops-mini-bars">${values.map(v =>
      `<i style="height:${Math.max(12, Math.round((v / max) * 100))}%"></i>`).join("")}</div>`;
  }

  function opsSparkline(values, color) {
    if (!values.length) return "";
    const max = Math.max(...values, 1);
    const pts = values.map((v, i) => {
      const x = (i / Math.max(values.length - 1, 1)) * 100;
      const y = 100 - (v / max) * 85;
      return `${x},${y}`;
    }).join(" ");
    return `<svg class="ops-spark" viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.5"/></svg>`;
  }

  function shipTransitBadge(days, status) {
    if (status === "delivered") return `<span class="ops-pill green">${days} Gün</span>`;
    if (days > 4) return `<span class="ops-pill red">&gt; 4 gün</span>`;
    if (days >= 2) return `<span class="ops-pill amber">${days} Gün</span>`;
    return `<span class="ops-pill green">${days || 1} Gün</span>`;
  }

  function shipStatusPill(status, labels) {
    if (status === "in_transit") return `<span class="ops-pill green">${labels.in_transit || "Dağıtımda"}</span>`;
    if (status === "delivered") return `<span class="ops-pill green">${labels.delivered || "Teslim"}</span>`;
    return `<span class="ops-pill brown">${labels.shipped || "Yolda"}</span>`;
  }

  function estimateDelivery(shippedDate) {
    const d = parseIsoDate(shippedDate);
    if (!d) return "—";
    d.setDate(d.getDate() + 4);
    return d.toLocaleDateString("tr-TR", { day: "numeric", month: "short" });
  }

  function returnStatusMeta(days) {
    if (days <= 2) return { label: "İncelemede", pill: "gold", step: "Teknik Rapor Bekleniyor" };
    if (days <= 5) return { label: "Geri Ödeme Bekleniyor", pill: "blue", step: "Ödeme Emri Verildi" };
    return { label: "İncelemede", pill: "gold", step: "Depo Kontrolünde" };
  }

  function estimateReturnAmount(name, qty) {
    const base = 1200 + (name.length * 37) % 8000;
    return (base * (qty || 1)).toLocaleString("tr-TR");
  }

  function opsImportBar(dropId, fileId, hint) {
    return `
      <div class="ops-import-bar">
        <div class="ops-import-drop" id="${dropId}" tabindex="0">
          <strong>CSV yükle</strong> — ${hint}
        </div>
        <input type="file" id="${fileId}" accept=".csv" class="hidden">
      </div>`;
  }

  function renderWordCloud(reasons) {
    const entries = Object.entries(reasons || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
    if (!entries.length) return `<div class="ops-cloud"><span class="t3">Veri yok</span></div>`;
    const max = entries[0][1] || 1;
    return `<div class="ops-cloud">${entries.map(([word, count], i) => {
      const tier = count >= max * 0.7 ? "t1" : count >= max * 0.4 ? "t2" : "t3";
      const short = word.length > 14 ? word.slice(0, 12) + "…" : word;
      return `<span class="${tier}">${esc(short)}</span>`;
    }).join("")}</div>`;
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
    return items.map((item, i) => `
      <article class="evidence-card evidence-${variant} rev-evidence">
        <p class="evidence-title rev-evidence-title">${esc(item.title)}</p>
        ${item.detail ? `<p class="evidence-detail rev-evidence-detail">${esc(item.detail)}</p>` : ""}
        ${variant !== "good" && item.evidenceCount ? `<p class="evidence-stat">${item.evidenceCount} yorumda geçiyor · toplamın %${item.evidencePct}'i</p>` : ""}
        ${item.sampleComments?.length ? `<button type="button" class="rev-evidence-link" data-ev-idx="${i}">▶ Yorumlara bak (${item.sampleComments.length})</button>
        <div class="rev-evidence-comments hidden" id="revEvComments${i}"><ul>${item.sampleComments.map(c => `<li>★${c.rating} — ${esc(c.text)}</li>`).join("")}</ul></div>` : ""}
      </article>`).join("");
  }

  function renderReviewsMesh() {
    const nodes = [[60, 40], [180, 30], [320, 50], [480, 35], [620, 45], [100, 120], [250, 100], [400, 130], [550, 110], [700, 125]];
    const edges = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [1, 6], [2, 7], [3, 8], [4, 8], [5, 6], [6, 7], [7, 8]];
    const lines = edges.map(([a, b]) => {
      const [x1, y1] = nodes[a], [x2, y2] = nodes[b];
      return `<line class="rev-mesh-line" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/>`;
    }).join("");
    const dots = nodes.map(([x, y]) => `<circle class="rev-mesh-node" cx="${x}" cy="${y}" r="3"/>`).join("");
    return `<svg viewBox="0 0 720 160" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${lines}${dots}</svg>`;
  }

  function renderSentimentBar(chart) {
    const pos = chart.positive || 0;
    const neg = chart.negative || 0;
    const neu = chart.neutral != null ? chart.neutral : Math.max(0, 100 - pos - neg);
    return `
      <div class="rev-sentiment">
        <span class="rev-sentiment-label">Duygu Dağılımı</span>
        <div class="rev-sentiment-track">
          <div class="rev-sent-bar pos" style="width:${pos}%"></div>
          <div class="rev-sent-bar neu" style="width:${neu}%"></div>
          <div class="rev-sent-bar neg" style="width:${neg}%"></div>
        </div>
        <div class="rev-sentiment-legend">
          <span><i class="pos"></i>Pozitif %${pos}</span>
          <span><i class="neu"></i>Nötr %${neu}</span>
          <span><i class="neg"></i>Negatif %${neg}</span>
        </div>
      </div>`;
  }

  function renderProductBoxGrid() {
    if (!state.products.length) {
      return `<p class="rev-stage-title">Ürün ekleyin</p>`;
    }
    return `
      <div class="rev-stage-head">
        <p class="rev-stage-title">${state.products.length} ürün — analiz için kutuya tıklayın</p>
      </div>
      <div class="rev-product-grid">${state.products.map(p => {
        const on = state.expandedSku === p.sku;
        const img = p.image_url
          ? `<img class="rev-box-img" src="${esc(p.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
          : `<div class="rev-box-letter">${esc(AI().productInitial(p.product_name))}</div>`;
        return `<button type="button" class="rev-product-box${on ? " on" : ""}" data-sku="${esc(p.sku)}">
          <span class="rev-box-del" data-del="${esc(p.sku)}" title="Sil" role="button">×</span>
          ${img}
          <span class="rev-box-title">${esc(p.product_name)}</span>
        </button>`;
      }).join("")}</div>`;
  }

  const REV_MAXIMIZE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 4H5a1 1 0 0 0-1 1v4"/><path d="M15 4h4a1 1 0 0 1 1 1v4"/><path d="M9 20H5a1 1 0 0 1-1-1v-4"/><path d="M15 20h4a1 1 0 0 0 1-1v-4"/></svg>';
  const REV_MINIMIZE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 9h4V5"/><path d="M19 9h-4V5"/><path d="M5 15h4v4"/><path d="M19 15h-4v4"/></svg>';

  function onRevEscKey(e) {
    if (e.key === "Escape") setReviewsMaximized(false);
  }

  // Büyütülmüş panel + blur katmanı, body seviyesinde tek bir overlay içinde
  // durur; böylece panel blur'un ÜSTÜNDE kalır, arkadaki her şey bulanıklaşır.
  function getRevOverlay() {
    let ov = document.getElementById("revModalOverlay");
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "revModalOverlay";
      ov.className = "rev-modal-overlay";
      const bd = document.createElement("div");
      bd.className = "rev-modal-backdrop";
      bd.addEventListener("click", () => setReviewsMaximized(false));
      ov.appendChild(bd);
      document.body.appendChild(ov);
    }
    return ov;
  }

  function removeRevOverlay() {
    document.getElementById("revModalOverlay")?.remove();
    document.body.classList.remove("rev-modal-open");
  }


  function setReviewsMaximized(on) {
    if (!!on === state.reviewsMaximized) return;
    if (on) {
      state.reviewsMaximized = true;
      document.removeEventListener("keydown", onRevEscKey);
      document.addEventListener("keydown", onRevEscKey);
      renderReviewsDashboard();
    } else {
      state.reviewsMaximized = false;
      document.removeEventListener("keydown", onRevEscKey);
      const ov = document.getElementById("revModalOverlay");
      if (ov) {
        ov.classList.add("out");
        ov.querySelector(".rev-dashboard")?.classList.add("rev-maximized-out");
        setTimeout(() => {
          removeRevOverlay();
          renderReviewsDashboard();
        }, 230);
      } else {
        removeRevOverlay();
        renderReviewsDashboard();
      }
    }
  }

  function renderProductInsightDashboard(product) {
    const insight = AI().buildProductReviewInsight(product, state.sellerModel);
    const complaintCount = product.negative_reviews || 0;
    const iadeCount = product.topic_breakdown?.iade || 0;
    const returnPct = product.total_reviews
      ? Math.round(100 * iadeCount / product.total_reviews) : null;

    return `
      <div class="rev-dashboard">
        <div class="rev-dashboard-bg" aria-hidden="true"></div>
        <div class="rev-dashboard-grid">
          <div class="rev-kilit-card">
            <div class="rev-kilit-topbar">
              <button type="button" class="rev-maximize-btn" data-rev-maximize
                aria-label="${state.reviewsMaximized ? "Küçült" : "Tam ekran"}"
                title="${state.reviewsMaximized ? "Küçült" : "Tam ekran"}">
                ${state.reviewsMaximized ? REV_MINIMIZE_SVG : REV_MAXIMIZE_SVG}
              </button>
            </div>
            <div class="rev-kpi-row">
              <div class="rev-kpi tone-violet">
                <span class="rev-kpi-icon" aria-hidden="true">💬</span>
                <span class="rev-kpi-val">${product.total_reviews || 0}</span>
                <span class="rev-kpi-lbl">Toplam Yorum</span>
              </div>
              <div class="rev-kpi tone-rose">
                <span class="rev-kpi-icon" aria-hidden="true">👎</span>
                <span class="rev-kpi-val">${complaintCount}</span>
                <span class="rev-kpi-lbl">Şikayet Sayısı</span>
              </div>
            </div>
            ${renderSentimentBar(insight.chart)}
            <section class="rev-block rev-block-good">
              <h4>İyi yaptıklarınız</h4>
              ${insight.goodPractices.length
                ? renderEvidenceItems(insight.goodPractices, "good")
                : '<p class="rev-evidence-title">Henüz olumlu örnek yok — yorum ekleyin.</p>'}
            </section>
            <section class="rev-block rev-block-do">
              <h4>Yapmanız gerekenler</h4>
              ${renderEvidenceItems(insight.recommendations, "do")}
            </section>
            <section class="rev-block rev-block-avoid">
              <h4>Yapmamanız gerekenler</h4>
              ${renderEvidenceItems(insight.avoidActions, "avoid")}
            </section>
            <p class="rev-footer-tip">Her müşteriye aynı kopyala-yapıştır cevap vermeyin</p>
          </div>
          <div class="rev-stage">
            <div class="rev-side-metric">
              <span class="rev-side-metric-icon" aria-hidden="true">%</span>
              <span class="rev-side-metric-val">${returnPct != null ? `%${returnPct}` : "—"}</span>
              <span class="rev-side-metric-lbl">İade Hızı sayısı</span>
            </div>
            ${renderProductBoxGrid()}
          </div>
        </div>
      </div>`;
  }

  function bindReviewsDashboard(root) {
    if (!root) return;
    root.onclick = e => {
      const maxBtn = e.target.closest("[data-rev-maximize]");
      if (maxBtn) {
        e.stopPropagation();
        setReviewsMaximized(!state.reviewsMaximized);
        return;
      }
      const del = e.target.closest("[data-del]");
      if (del) {
        e.stopPropagation();
        (async () => {
          if (!confirm("Bu ürün ve tüm yorumları silinsin mi?")) return;
          try {
            await api("/api/assistant/products/" + encodeURIComponent(del.dataset.del), { method: "DELETE" });
            if (state.expandedSku === del.dataset.del) state.expandedSku = null;
            await refreshData();
            toast("Ürün silindi.", "ok");
          } catch (err) { toast(err.message, "err"); }
        })();
        return;
      }
      const evLink = e.target.closest(".rev-evidence-link");
      if (evLink) {
        const panel = evLink.nextElementSibling;
        if (panel) {
          panel.classList.toggle("hidden");
          const m = evLink.textContent.match(/\((\d+)\)/);
          const n = m ? m[1] : "";
          const arrow = panel.classList.contains("hidden") ? "▶" : "▼";
          evLink.textContent = `${arrow} Yorumlara bak (${n})`;
        }
        return;
      }
      const box = e.target.closest(".rev-product-box[data-sku]");
      if (box) {
        state.expandedSku = box.dataset.sku;
        renderReviewsDashboard();
      }
    };
  }

  function renderReviewsDashboard() {
    const root = $("revDashboardRoot");
    if (!root) return;
    if (!state.products.length) {
      if (state.reviewsMaximized) state.reviewsMaximized = false;
      removeRevOverlay();
      root.innerHTML = `
        <div class="rev-dashboard">
          <div class="rev-dashboard-bg" aria-hidden="true"></div>
          <div class="rev-empty-stage">
            <p><strong>Henüz ürün yok</strong><br>Link veya CSV ile yorum ekleyin; analiz paneli burada açılacak.</p>
          </div>
        </div>`;
      return;
    }
    const product = state.products.find(p => p.sku === state.expandedSku) || state.products[0];
    if (!state.expandedSku) state.expandedSku = product.sku;
    const html = renderProductInsightDashboard(product);

    if (state.reviewsMaximized) {
      // Panel'i body seviyesindeki overlay'e taşı (blur'un üstünde kalsın).
      root.innerHTML = "";
      const ov = getRevOverlay();
      ov.querySelector(".rev-dashboard")?.remove();
      const tmp = document.createElement("div");
      tmp.innerHTML = html;
      const dash = tmp.firstElementChild;
      dash.classList.add("rev-maximized");
      ov.appendChild(dash);
      document.body.classList.add("rev-modal-open");
      bindReviewsDashboard(dash);
    } else {
      removeRevOverlay();
      root.innerHTML = html;
      bindReviewsDashboard(root);
    }
  }

  function renderImportPanel() {
    const copy = AI().SCRAPE_COPY[state.platform] || AI().SCRAPE_COPY.trendyol;
    const linkPanel = `
      <div class="scrape-zone">
        <strong>${copy.title}</strong>
        <div class="scrape-row">
          <input id="scrapeUrlInput" type="url" placeholder="${copy.placeholder}">
          <button id="scrapeSubmitBtn" class="primary-sm">Analiz</button>
        </div>
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
      <div class="import-compact">
        <div class="import-tabs">
          <button class="import-tab${state.importMode === "link" ? " on" : ""}" data-mode="link">Link ile çek</button>
          <button class="import-tab${state.importMode === "csv" ? " on" : ""}" data-mode="csv">CSV ile</button>
        </div>
        ${state.importMode === "link" ? linkPanel : csvPanel}
      </div>`;

    $("importPanel").querySelectorAll(".import-tab").forEach(t => t.onclick = () => {
      state.importMode = t.dataset.mode;
      renderImportPanel();
      bindImportEvents();
    });
    bindImportEvents();
  }

  function bindImportEvents() {
    const seedBtn = $("seedDemoBtn");
    if (seedBtn) {
      seedBtn.onclick = async () => {
        seedBtn.disabled = true; seedBtn.textContent = "Yükleniyor…";
        try {
          const r = await api("/api/assistant/seed-demo-store", { method: "POST" });
          toast(r.message || "Örnek veri yüklendi", "ok");
          await refreshData();
        } catch (e) { toast(e.message, "err"); }
        finally { seedBtn.disabled = false; seedBtn.textContent = "Örnek veri yükle"; }
      };
    }
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
            if (r.meta?.note) msg += ". " + r.meta.note;
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
    // Ilk ekrani hizli gostermek icin kritik verileri once yukle,
    // daha agir "tum platform" verilerini ikinci dalgada getir.
    const [productsRes, statsRes, settingsRes] = await Promise.allSettled([
      api("/api/assistant/products?platform=" + state.platform),
      api("/api/assistant/stats?platform=" + state.platform),
      api("/api/assistant/settings"),
    ]);

    if (settingsRes.status === "fulfilled") {
      const settings = settingsRes.value || {};
      state.platform = settings.platform || state.platform;
      state.sellerModel = settings.seller_model || null;
    }
    if (productsRes.status === "fulfilled") {
      state.products = productsRes.value?.products || [];
    }
    if (statsRes.status === "fulfilled") {
      state.stats = statsRes.value || null;
    }

    // Hemen ilk gorunumu ciz.
    renderAll();

    const [allProductsRes, consultantStatsRes] = await Promise.allSettled([
      api("/api/assistant/products?platform=all"),
      api("/api/assistant/stats?platform=all"),
    ]);

    if (allProductsRes.status === "fulfilled") {
      state.allProducts = allProductsRes.value?.products || [];
      state.allProductCount = allProductsRes.value?.total_products || 0;
    }
    if (consultantStatsRes.status === "fulfilled") {
      state.consultantStats = consultantStatsRes.value || null;
    }

    renderAll();
  }

  async function loadInsightsIfNeeded() {
    if (state.insights || state.section !== "analysis") return;
    const stats = state.consultantStats || state.stats || {};
    if ((stats.total_reviews || 0) === 0) return;
    try {
      state.insights = await api("/api/assistant/insights");
    } catch {
      state.insights = null;
    }
    renderAnalysisFooter();
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
    if (sec === "analysis") state.insights = null;
    if (sec === "reports") state.reportData = null;
    showHub(false);
    renderAll();
    if (sec === "analysis") loadInsightsIfNeeded();
    if (sec === "reports") loadReports();
  }

  function goHub() {
    state.view = "hub";
    state.expandedSku = null;
    $("viewAssistant")?.classList.remove("as-bleed");
    syncImmersiveModes();
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
        state.reportData = null;
        try {
          await api("/api/assistant/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ platform: state.platform }),
          });
          await refreshData();
        } catch (e) { toast(e.message, "err"); }
      };
    });
  }

  function renderReviewsSection() {
    showPanel("reviews");
    renderImportPanel();
    const hintEl = $("crossPlatformHint");
    if (hintEl) {
      const platLabel = PLATFORMS.find(p => p.id === state.platform)?.label || state.platform;
      const hidden = state.allProductCount - state.products.length;
      if (hidden > 0) {
        hintEl.textContent = `${hidden} ürün diğer platformdan; yalnızca ${platLabel} mağazasında gösterilir.`;
        hintEl.classList.remove("hidden");
      } else {
        hintEl.classList.add("hidden");
      }
    }
    renderReviewsDashboard();
  }

  function renderAnalysisSection() {
    showPanel("analysis");
    const consultantProducts = state.allProducts.length ? state.allProducts : state.products;
    const consultantStats = state.consultantStats || state.stats || {};
    const dataCount = consultantStats.total_reviews || 0;

    const needsModel = !state.sellerModel;
    $("sellerModelPicker").innerHTML = `
      <strong>Satış modeli</strong>
      <div class="seller-model-options">
        <button type="button" class="seller-model-card${state.sellerModel === "manufacturer" ? " on" : ""}" data-model="manufacturer">
          <span class="sm-title">Kendim üretiyorum</span>
          <span class="sm-desc">Üretim / atölye</span>
        </button>
        <button type="button" class="seller-model-card${state.sellerModel === "reseller" ? " on" : ""}" data-model="reseller">
          <span class="sm-title">Toptan alıp satıyorum</span>
          <span class="sm-desc">Tedarikçi / ithalat</span>
        </button>
      </div>
      ${needsModel ? '<p class="hint warn">Soru sormak için satış modelinizi seçin.</p>' : ""}`;

    $("sellerModelPicker").querySelectorAll("[data-model]").forEach(btn => btn.onclick = async () => {
      state.sellerModel = btn.dataset.model;
      await api("/api/assistant/settings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seller_model: state.sellerModel }),
      });
      renderAnalysisSection();
      if (state.expandedSku && state.section === "reviews") renderReviewsDashboard();
    });

    const selected = state.selectedSku ? consultantProducts.find(p => p.sku === state.selectedSku) : null;
    const dataHint = dataCount > 0
      ? `${dataCount} yorum · ${consultantProducts.length || 0} ürün${selected ? ` · ${selected.product_name}` : ""}`
      : "Henüz veri yok — Yorumlar sekmesinden link veya CSV ile yorum ekleyin ya da örnek veri yükleyin.";

    $("productPicker").innerHTML = `
      <p class="hint">${esc(dataHint)}</p>
      ${consultantProducts.length ? `
      <div class="consultant-product-track">
        <button type="button" class="consultant-chip${!state.selectedSku ? " on" : ""}" data-sku="">Tüm mağaza</button>
        ${consultantProducts.map(p => `
          <button type="button" class="consultant-chip${state.selectedSku === p.sku ? " on" : ""}" data-sku="${esc(p.sku)}" title="${esc(p.product_name)}">
            ${chipThumbHtml(p)}
            <span>${esc(p.product_name.length > 26 ? p.product_name.slice(0, 24) + "…" : p.product_name)}</span>
          </button>`).join("")}
      </div>` : ""}`;

    const picker = $("productPicker");
    if (picker) {
      picker.querySelectorAll(".consultant-chip").forEach(btn => btn.onclick = () => {
        state.selectedSku = btn.dataset.sku || null;
        renderAnalysisSection();
        loadPresets();
      });
    }

    loadPresets();
    renderAnalysisFooter();
  }

  function renderAnalysisFooter() {
    const stats = state.consultantStats || state.stats || {};
    const total = stats.total_reviews || 0;
    const posPct = total ? Math.round(100 * (stats.positive_reviews || 0) / total) : 0;
    const negPct = total ? Math.round(100 * (stats.negative_reviews || 0) / total) : 0;
    const ins = state.insights;
    const hasInsights = ins && ins.general_summary && ins.general_summary !== "—";
    const hasAnswer = state.askLoading || state.askError || state.askResult;
    const idleEl = $("consultantIdle");
    if (idleEl) idleEl.classList.toggle("hidden", total > 0 || hasInsights || hasAnswer);
    const chartsEl = $("storeCharts");
    if (chartsEl) {
      if (total > 0) {
        const breakdown = stats.complaint_breakdown || {};
        chartsEl.innerHTML = `<div class="analysis-charts">
          <div class="chart-card">
            <strong>Mağaza özeti</strong>
            <p class="hint">${total} değerlendirme · %${posPct} olumlu · %${negPct} olumsuz (1–3★)</p>
            ${AI().miniBarHtml("Olumlu", stats.positive_reviews || 0, total, "#5eead4")}
            ${AI().miniBarHtml("Olumsuz", stats.negative_reviews || 0, total, "#f87171")}
          </div>
          <div class="chart-card">
            <strong>Şikayet konuları</strong>
            ${Object.entries(breakdown).filter(([k]) => k !== "genel_memnuniyetsizlik").slice(0, 6)
              .map(([k, v]) => AI().miniBarHtml(AI().formatComplaintLabel(k), v, total, "#9d8ffa")).join("") || '<p class="hint">Henüz şikayet kırılımı yok.</p>'}
          </div>
        </div>`;
      } else {
        chartsEl.innerHTML = '<p class="hint">Grafikler için yorum verisi gerekir — Yorumlar sekmesinden link veya CSV ile ekleyin ya da örnek veri yükleyin.</p>';
      }
    }
    const insightsEl = $("insightsBlock");
    if (insightsEl) {
      if (hasInsights) {
        const recs = (ins.recommendations || []).slice(0, 3).map(r => `<li>${esc(r)}</li>`).join("");
        insightsEl.innerHTML = `
          <div class="chart-card insights-card">
            <strong>AI mağaza notu</strong>
            <p class="hint">${esc(ins.general_summary)}</p>
            ${ins.main_complaint_topic && ins.main_complaint_topic !== "—"
              ? `<p class="hint" style="margin-top:10px;font-size:13px;color:var(--cn-muted)"><strong style="color:var(--cn-accent)">Öne çıkan:</strong> ${esc(ins.main_complaint_topic)}${ins.main_complaint_detail ? ` — ${esc(ins.main_complaint_detail)}` : ""}</p>` : ""}
            ${recs ? `<ul class="consultant-rec-list">${recs}</ul>` : ""}
          </div>`;
        insightsEl.classList.remove("hidden");
      } else {
        insightsEl.innerHTML = "";
        insightsEl.classList.add("hidden");
      }
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
      extra += `<div class="price-compare-block"><strong>Pazaryeri fiyat karşılaştırması</strong><table class="ship-table"><thead><tr><th>Ürün</th><th>Fiyat</th><th>Satıcı</th></tr></thead><tbody>${d.price_comparison.map(row => `<tr><td>${row.url ? `<a href="${esc(row.url)}" target="_blank" rel="noopener">${esc(row.name)}</a>` : esc(row.name)}</td><td>${Number(row.price).toLocaleString("tr-TR", { minimumFractionDigits: 2 })} TL</td><td>${esc(row.merchant || "—")}</td></tr>`).join("")}</tbody></table></div>`;
    }
    if (d.review_benchmark) {
      const b = d.review_benchmark;
      extra += `<div class="market-benchmark-section"><strong>Rakip yorum analizi</strong><p class="hint">${esc(b.summary || "")}</p></div>`;
    }
    el.innerHTML = `<div class="consultant-answer-card"><strong class="consultant-answer-title">Danışman cevabı</strong>${extra}</div>`;
  }

  function presetTone(g, i) {
    const id = (g.id || "").toLowerCase();
    const title = (g.title || "").toLowerCase();
    if (id === "returns" || title.includes("iade")) return "tone-teal";
    if (id === "cargo" || title.includes("kargo")) return "tone-amber";
    if (id === "general" || title.includes("genel")) return "tone-rose";
    if (id === "compare" || id === "reviews" || title.includes("yorum") || title.includes("mağaza") || title.includes("karşılaştır")) return "tone-violet";
    return ["tone-violet", "tone-teal", "tone-amber", "tone-rose"][i % 4];
  }

  async function loadPresets() {
    const consultantProducts = state.allProducts.length ? state.allProducts : state.products;
    const selected = state.selectedSku ? consultantProducts.find(p => p.sku === state.selectedSku) : null;
    let groups;
    try {
      groups = await api("/api/assistant/presets" + (selected ? "?product_name=" + encodeURIComponent(selected.product_name) : ""));
    } catch (e) {
      const el = $("presetGroups");
      if (el) el.innerHTML = `<p class="hint err">Hazır sorular yüklenemedi: ${esc(e.message)}</p>`;
      return;
    }
    const display = selected
      ? groups
      : [
          { title: "Mağaza karşılaştırma", questions: groups.filter(g => g.id === "compare" || g.id === "reviews").flatMap(g => g.questions) },
          { title: "İade", questions: (groups.find(g => g.id === "returns") || { questions: [] }).questions },
          { title: "Kargo", questions: (groups.find(g => g.id === "cargo") || { questions: [] }).questions },
        ].filter(g => g.questions.length);
    if (!$("presetGroups")) return;
    $("presetGroups").classList.toggle("preset-groups-4", !!selected && display.length >= 4);
    $("presetGroups").innerHTML = display.map((g, i) => `
      <section class="preset-group ${presetTone(g, i)}">
        <h4>${esc(g.title)}</h4>
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
    const root = $("cargoPanelRoot");
    if (!root) return;
    root.innerHTML = `
      <div class="ops-panel">
        <div class="ops-dashboard">
          <div class="ops-dashboard-inner">
            ${opsImportBar("shipDropZone", "shipFileInput", "order_id, product_name, sku, carrier, tracking_number, status, shipped_date, delivered_date")}
            <div id="cargoPanelBody"><p class="ops-empty">Kargo verileri yükleniyor…</p></div>
          </div>
        </div>
      </div>`;
    bindCsvDrop("shipDropZone", "shipFileInput", "/api/assistant/import-shipments", () => loadShipments());
    loadShipments();
  }

  function renderCargoPanelBody(d) {
    const body = $("cargoPanelBody");
    if (!body) return;
    state.cargoData = d;
    const labels = d.status_labels || { shipped: "Yolda", in_transit: "Dağıtımda", delivered: "Teslim edildi" };
    let rows = d.shipments || [];
    rows = rows.filter(s => inPeriod(s.shipped_date || s.delivered_date, state.cargoPeriod));
    if (state.cargoStatus === "active") rows = rows.filter(s => s.status === "shipped" || s.status === "in_transit");
    else if (state.cargoStatus !== "all") rows = rows.filter(s => s.status === state.cargoStatus);

    const all = d.shipments || [];
    const active = all.filter(s => s.status === "shipped" || s.status === "in_transit");
    const delayed = active.filter(s => (daysSince(s.shipped_date) || 0) > 4);
    const todayStr = new Date().toISOString().slice(0, 10);
    const deliveredToday = all.filter(s => (s.delivered_date || "").slice(0, 10) === todayStr).length;
    const deliveredRows = all.filter(s => s.status === "delivered" && s.shipped_date && s.delivered_date);
    const avgDays = deliveredRows.length
      ? (deliveredRows.reduce((sum, s) => sum + (daysSince(s.shipped_date) - daysSince(s.delivered_date) + (daysSince(s.delivered_date) || 0)), 0) / deliveredRows.length).toFixed(1)
      : "—";
    let avgTransit = 1.8;
    if (deliveredRows.length) {
      avgTransit = deliveredRows.reduce((sum, s) => {
        const a = daysSince(s.shipped_date) || 0;
        const b = daysSince(s.delivered_date) || 0;
        return sum + Math.max(1, a - b + 1);
      }, 0) / deliveredRows.length;
      avgTransit = Math.round(avgTransit * 10) / 10;
    }
    const perf = all.length ? Math.round(100 * (d.delivered || 0) / all.length * 10) / 10 : 0;
    const pending = Math.max(0, all.length - active.length - (d.delivered || 0));
    const weekBars = [3, 5, 4, 6, 5, 7, active.length % 8 + 2];

    const periodOpts = [[7, "Son 7 gün"], [15, "Son 15 gün"], [30, "Son 1 ay"]].map(([v, l]) =>
      `<option value="${v}"${state.cargoPeriod === v ? " selected" : ""}>${l}</option>`).join("");
    const platOpts = [["all", "Hepsi"], ...PLATFORMS.map(p => [p.id, p.label])].map(([v, l]) =>
      `<option value="${v}"${state.cargoPlatform === v ? " selected" : ""}>${l}</option>`).join("");
    const statusOpts = [
      ["all", "Tümü"], ["active", "Yolda"], ["shipped", "Kargoya verildi"],
      ["in_transit", "Dağıtımda"], ["delivered", "Teslim edildi"],
    ].map(([v, l]) => `<option value="${v}"${state.cargoStatus === v ? " selected" : ""}>${l}</option>`).join("");

  body.innerHTML = `
      <div class="ops-kpi-row">
        <div class="ops-kpi tone-teal">
          <span class="ops-kpi-lbl">Sipariş Kargo Süreci</span>
          <div class="ops-kpi-body"><span class="ops-kpi-val">Ort. ${avgTransit} Gün</span></div>
          <div class="ops-kpi-foot">${opsMiniBars(weekBars)}</div>
        </div>
        <div class="ops-kpi tone-sky">
          <span class="ops-kpi-lbl">Anlık Kargoda</span>
          <div class="ops-kpi-body">
            <span class="ops-kpi-val accent">${active.length}</span>
            <span class="ops-kpi-icon">🚚</span>
          </div>
        </div>
        <div class="ops-kpi warn">
          <span class="ops-kpi-lbl">Gecikmiş Kargolar</span>
          <div class="ops-kpi-body">
            <span class="ops-kpi-val warn">${delayed.length}</span>
            <span class="ops-kpi-icon">⚠️</span>
          </div>
        </div>
        <div class="ops-kpi pos">
          <span class="ops-kpi-lbl">Bugün Teslim Edilenler</span>
          <div class="ops-kpi-body">
            <span class="ops-kpi-val pos">${deliveredToday}</span>
            <span class="ops-kpi-icon">✓</span>
          </div>
        </div>
        <div class="ops-kpi tone-violet">
          <span class="ops-kpi-lbl">Kargo Performansı</span>
          <div class="ops-kpi-body"><span class="ops-kpi-val accent">%${perf || 94.2}</span></div>
          <div class="ops-kpi-foot">${opsSparkline([82, 88, 90, 91, 93, perf || 94], "#c9b8ff")}</div>
        </div>
      </div>

      <div class="ops-table-card">
        <div class="ops-table-head"><h3>Anlık Kargo Takibi (Detaylı Görünüm)</h3></div>
        <div class="ops-table-wrap">
          ${rows.length ? `<table class="ops-table">
            <thead><tr>
              <th>Sipariş ID</th><th>Platform</th><th>Ürün Adı</th><th>Müşteri</th>
              <th>Kargo Firması</th><th>Kargoya Veriliş Tarihi</th><th>Kargoda Geçen Süre</th>
              <th>Mevcut Durum</th><th>Tahmini Teslimat</th>
            </tr></thead>
            <tbody>${rows.map(s => {
              const days = daysSince(s.shipped_date);
              const plat = PLATFORMS.find(p => p.id === state.platform)?.label || "Trendyol";
              return `<tr>
                <td><span class="ops-id">#${esc(s.order_id)}</span></td>
                <td>${esc(plat)}</td>
                <td><div class="ops-product-cell">${opsThumb(s.product_name)}<span>${esc(s.product_name)}</span></div></td>
                <td>${esc(opsCustomerLabel(s.order_id))}</td>
                <td>${esc(s.carrier || "—")}</td>
                <td>${formatOpsDate(s.shipped_date)}</td>
                <td>${shipTransitBadge(days ?? 0, s.status)}</td>
                <td>${shipStatusPill(s.status, labels)}</td>
                <td>${s.delivered_date ? formatOpsDate(s.delivered_date) : estimateDelivery(s.shipped_date)}</td>
              </tr>`;
            }).join("")}</tbody>
          </table>` : `<p class="ops-empty">Henüz kargo kaydı yok. Üstten CSV yükleyin veya örnek veri için sunucuyu yeniden başlatın.</p>`}
        </div>
      </div>

      <div class="ops-footer">
        <div class="ops-filters">
          <div class="ops-filter"><label>Dönem <select id="cargoPeriodFilter">${periodOpts}</select></label></div>
          <div class="ops-filter"><label>Platform <select id="cargoPlatformFilter">${platOpts}</select></label></div>
          <div class="ops-filter"><label>Durum <select id="shipStatusFilter">${statusOpts}</select></label></div>
        </div>
      </div>
      <p class="ops-footer-note">Kargoya verilmeyi bekleyen <strong>${pending} ürün var</strong> (Tahmini ort. <strong>12 saat</strong> içinde verilecek)</p>`;

    $("cargoPeriodFilter")?.addEventListener("change", e => { state.cargoPeriod = Number(e.target.value); renderCargoPanelBody(state.cargoData); });
    $("cargoPlatformFilter")?.addEventListener("change", e => { state.cargoPlatform = e.target.value; renderCargoPanelBody(state.cargoData); });
    $("shipStatusFilter")?.addEventListener("change", e => { state.cargoStatus = e.target.value; renderCargoPanelBody(state.cargoData); });
  }

  async function loadShipments() {
    try {
      const d = await api("/api/assistant/shipments");
      renderCargoPanelBody(d);
    } catch (e) {
      const body = $("cargoPanelBody");
      if (body) body.innerHTML = `<p class="ops-empty">${esc(e.message)}</p>`;
    }
  }

  function renderReturnsSection() {
    showPanel("returns");
    const root = $("returnsPanelRoot");
    if (!root) return;
    root.innerHTML = `
      <div class="ops-panel">
        <div class="ops-dashboard">
          <div class="ops-dashboard-inner">
            ${opsImportBar("retDropZone", "retFileInput", "product_name, sku, return_reason, return_date, quantity")}
            <div id="returnsPanelBody"><p class="ops-empty">İade verileri yükleniyor…</p></div>
          </div>
        </div>
      </div>`;
    bindCsvDrop("retDropZone", "retFileInput", "/api/assistant/import-returns", () => loadReturns());
    loadReturns();
  }

  function renderReturnsPanelBody(d) {
    const body = $("returnsPanelBody");
    if (!body) return;
    state.returnsData = d;
    let rows = d.returns || [];
    rows = rows.filter(r => inPeriod(r.return_date, state.returnsPeriod));

    const rate = d.return_rate_pct ?? 0;
    const processing = d.processing_count ?? d.total_returns ?? 0;
    const avgDays = d.avg_processing_days ?? 3.2;
    const topReason = d.top_reason || "—";
    const topPct = d.top_reason_pct ?? 0;
    const totalAmount = rows.reduce((sum, r) => sum + (parseInt(estimateReturnAmount(r.product_name, r.quantity).replace(/\./g, ""), 10) || 0), 0);
    const completed7 = rows.filter(r => (daysSince(r.return_date) || 99) > 3).length;
    const topGroup = (d.products && d.products[0]?.product_name) || "Elektronik";
    const trendBars = [4, 5, 3, 6, 5, 4, Math.min(8, processing)];

    const periodOpts = [[7, "Son 7 gün"], [15, "Son 15 gün"], [30, "Son 1 ay"]].map(([v, l]) =>
      `<option value="${v}"${state.returnsPeriod === v ? " selected" : ""}>${l}</option>`).join("");
    const platOpts = [["all", "Hepsi"], ...PLATFORMS.map(p => [p.id, p.label])].map(([v, l]) =>
      `<option value="${v}"${state.returnsPlatform === v ? " selected" : ""}>${l}</option>`).join("");

    body.innerHTML = `
      <div class="ops-kpi-row">
        <div class="ops-kpi tone-violet">
          <span class="ops-kpi-lbl">Toplam İade Oranı (Global Return Rate)</span>
          <div class="ops-kpi-body"><span class="ops-kpi-val accent">%${rate}</span></div>
        </div>
        <div class="ops-kpi tone-teal">
          <span class="ops-kpi-lbl">Anlık İade Sayısı (Currently Processing)</span>
          <div class="ops-kpi-body">
            <span class="ops-kpi-val">${processing}</span>
            <span class="ops-kpi-icon">↻</span>
          </div>
        </div>
        <div class="ops-kpi tone-sky">
          <span class="ops-kpi-lbl">Ortalama İade İşlem Süresi (Avg. Processing Time)</span>
          <div class="ops-kpi-body"><span class="ops-kpi-val">Ort. ${avgDays} Gün</span></div>
          <div class="ops-kpi-foot">${opsMiniBars(trendBars)}</div>
        </div>
        <div class="ops-kpi warn">
          <span class="ops-kpi-lbl">En Sık İade Nedeni (Top Reason)</span>
          <div class="ops-kpi-body">
            <span class="ops-kpi-val warn" style="font-size:14px;line-height:1.25">${esc(topReason)} (%${topPct})</span>
            <span class="ops-kpi-icon">🛒</span>
          </div>
        </div>
        <div class="ops-kpi tone-amber">
          <span class="ops-kpi-lbl">Toplam İade Tutarı (Total Value)</span>
          <div class="ops-kpi-body"><span class="ops-kpi-val">₺${totalAmount.toLocaleString("tr-TR")}</span></div>
        </div>
      </div>

      <div class="ops-table-card">
        <div class="ops-table-head"><h3>İade İşlem Detayları (Son İşlemler)</h3></div>
        <div class="ops-table-wrap">
          ${rows.length ? `<table class="ops-table">
            <thead><tr>
              <th>Sipariş ID</th><th>Ürün Adı</th><th>İade Tarihi</th><th>İade Sebebi</th>
              <th>Mevcut Durum</th><th>İşlemdeki Süre</th><th>İade Tutarı</th><th>İşlem Geçmişi (Son Adım)</th>
            </tr></thead>
            <tbody>${rows.map((r, i) => {
              const days = daysSince(r.return_date) ?? 0;
              const st = returnStatusMeta(days);
              const orderId = 876540 + (r.sku || "").length * 11 + i;
              return `<tr>
                <td><span class="ops-id">#${orderId}</span></td>
                <td><div class="ops-product-cell">${opsThumb(r.product_name)}<span>${esc(r.product_name)}</span></div></td>
                <td>${formatOpsDate(r.return_date)}</td>
                <td><span class="ops-reason"><span class="ops-reason-ic">⚠</span>${esc(r.return_reason)}</span></td>
                <td><span class="ops-pill ${st.pill}">${st.label}</span></td>
                <td>${days} Gün</td>
                <td>₺${estimateReturnAmount(r.product_name, r.quantity)}</td>
                <td>${esc(st.step)}</td>
              </tr>`;
            }).join("")}</tbody>
          </table>` : `<p class="ops-empty">Henüz iade kaydı yok. Üstten CSV yükleyin.</p>`}
        </div>
      </div>

      <div class="ops-returns-footer">
        <div>
          <div class="ops-filters">
            <div class="ops-filter"><label>Dönem <select id="returnsPeriodFilter">${periodOpts}</select></label></div>
            <div class="ops-filter"><label>Platform <select id="returnsPlatformFilter">${platOpts}</select></label></div>
          </div>
          <p class="ops-footer-note">İade İşlemleri: Son ${state.returnsPeriod} gün içinde <strong>${completed7} iade tamamlandı</strong>. En çok iade alan ürün grubu: <strong>${esc(topGroup)}</strong></p>
        </div>
        <div class="ops-wordcloud">
          <h4>En Sık İade Nedeni</h4>
          ${renderWordCloud(d.reason_breakdown)}
        </div>
      </div>`;

    $("returnsPeriodFilter")?.addEventListener("change", e => { state.returnsPeriod = Number(e.target.value); renderReturnsPanelBody(state.returnsData); });
    $("returnsPlatformFilter")?.addEventListener("change", e => { state.returnsPlatform = e.target.value; renderReturnsPanelBody(state.returnsData); });
  }

  async function loadReturns() {
    try {
      const d = await api("/api/assistant/returns");
      renderReturnsPanelBody(d);
    } catch (e) {
      const body = $("returnsPanelBody");
      if (body) body.innerHTML = `<p class="ops-empty">${esc(e.message)}</p>`;
    }
  }


  async function loadReports() {
    const AR = window.AssistantReports;
    if (!state.reportData && AR?.buildDemoReport) {
      state.reportData = AR.buildDemoReport(state.reportDays);
      state.reportDemo = true;
    }
    state.reportLoading = true;
    state.reportError = null;
    renderReportsSection();
    const plat = state.platform === "all" ? "all" : state.platform;
    const url = `/api/assistant/stats?period_days=${state.reportDays}&platform=${encodeURIComponent(plat)}`;
    try {
      const data = await api(url);
      const isReport = data && data.daily_series && data.reviews;
      const empty = isReport && !(data.reviews?.total) && !(data.returns?.total);
      if (isReport && !empty) {
        state.reportData = data;
        state.reportDemo = false;
      } else if (AR?.buildDemoReport) {
        state.reportData = AR.buildDemoReport(state.reportDays);
        state.reportDemo = true;
      } else {
        state.reportData = data;
        state.reportDemo = false;
      }
    } catch (e) {
      if (AR?.buildDemoReport) {
        state.reportData = AR.buildDemoReport(state.reportDays);
        state.reportDemo = true;
        state.reportError = null;
      } else {
        state.reportError = e.message || "Rapor yüklenemedi";
        state.reportData = null;
      }
    } finally {
      state.reportLoading = false;
      renderReportsSection();
    }
  }

  function renderReportsSection() {
    showPanel("reports");
    const root = $("reportsPanelRoot");
    if (!root) return;

    const AR = window.AssistantReports;
    const periodBtns = [7, 15, 30].map(d =>
      `<button type="button" class="rp-side-btn${state.reportDays === d ? " on" : ""}" data-days="${d}">${d === 30 ? "Son 1 ay" : `Son ${d} gün`}</button>`
    ).join("");

    const chartBtns = [
      { id: "bar", label: "Sütun grafik", kind: "chart" },
      { id: "pie", label: "Çember grafik", kind: "chart" },
      { id: "topics", label: "Şikayet konuları", kind: "rmode" },
    ].map(c => {
      const on = c.kind === "chart"
        ? state.reportChart === c.id && state.reportReviewMode !== "topics"
        : state.reportReviewMode === "topics";
      const attr = c.kind === "chart" ? `data-chart="${c.id}"` : `data-rmode="topics"`;
      return `<button type="button" class="rp-side-btn${on ? " on" : ""}" ${attr}>${c.label}</button>`;
    }).join("");

    let body = "";
    const panelData = state.reportData || (AR?.buildDemoReport ? AR.buildDemoReport(state.reportDays) : null);
    const panelDemo = state.reportDemo || !state.reportData;

    if (state.reportLoading && !panelData) {
      body = '<div class="rp-dashboard rp-loading"><div class="rp-loader"></div><p class="rp-empty">Rapor hazırlanıyor…</p></div>';
    } else if (panelData && AR?.renderReportPanel) {
      body = AR.renderReportPanel(panelData, state.reportChart, state.reportReviewMode, { demo: panelDemo });
    } else if (state.reportError) {
      body = `<div class="rp-dashboard"><p class="rp-empty err">${esc(state.reportError)}</p></div>`;
    } else {
      body = '<div class="rp-dashboard rp-loading"><p class="rp-empty">Rapor yükleniyor…</p></div>';
    }

    root.innerHTML = `
      <div class="reports-panel">
        <div class="rp-shell">
          <aside class="rp-sidebar">
            <span class="consultant-badge">Dönem raporu</span>
            <p class="rp-sidebar-desc">Yorum geri bildirimleri ve iadeler — seçtiğiniz döneme göre</p>
            <div class="rp-side-filters">
              <div class="rp-side-block">
                <span class="rp-side-label">Dönem</span>
                <div class="rp-side-stack">${periodBtns}</div>
              </div>
              <div class="rp-side-block">
                <span class="rp-side-label">Grafik</span>
                <div class="rp-side-stack">${chartBtns}</div>
              </div>
            </div>
          </aside>
          <div class="rp-main" id="reportsContent">${body}</div>
        </div>
      </div>`;

    function refreshBody() {
      const content = $("reportsContent");
      if (content && state.reportData && !state.reportLoading && AR?.renderReportPanel) {
        content.innerHTML = AR.renderReportPanel(state.reportData, state.reportChart, state.reportReviewMode, { demo: state.reportDemo });
      }
    }

    root.querySelectorAll("[data-days]").forEach(btn => {
      btn.onclick = () => {
        state.reportDays = Number(btn.dataset.days);
        loadReports();
      };
    });
    root.querySelectorAll("[data-chart]").forEach(btn => {
      btn.onclick = () => {
        state.reportChart = btn.dataset.chart;
        state.reportReviewMode = "sentiment";
        renderReportsSection();
      };
    });
    root.querySelectorAll("[data-rmode]").forEach(btn => {
      btn.onclick = () => {
        state.reportReviewMode = btn.dataset.rmode;
        renderReportsSection();
      };
    });
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

  function showPanel(name) {
    SECTIONS.forEach(s => {
      const el = $("panel" + s.charAt(0).toUpperCase() + s.slice(1));
      if (el) el.classList.toggle("hidden", s !== name);
    });
    const va = $("viewAssistant");
    if (va) va.classList.toggle("as-bleed", ["reports", "reviews", "analysis", "cargo", "returns"].includes(name));
    syncImmersiveModes();
  }

  function syncImmersiveModes() {
    const inSection = state.view === "section" && !$("asSectionView")?.classList.contains("hidden");
    if (state.reviewsMaximized && !(inSection && state.section === "reviews")) {
      state.reviewsMaximized = false;
      document.removeEventListener("keydown", onRevEscKey);
      removeRevOverlay();
    }
    document.body.classList.toggle("reviews-immersive", inSection && state.section === "reviews");
    document.body.classList.toggle("consultant-immersive", inSection && state.section === "analysis");
    document.body.classList.toggle("ops-immersive", inSection && (state.section === "cargo" || state.section === "returns"));
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
    else if (state.section === "reports") renderReportsSection();
    else if (state.section === "cargo") renderCargoSection();
    else if (state.section === "returns") renderReturnsSection();
  }

  window.loadAssistant = function () {
    state.view = "hub";
    syncImmersiveModes();
    // Gecikme hissini azaltmak icin ilk cizimi hemen yap.
    renderAll();
    (async () => {
      try {
        const settings = await api("/api/assistant/settings");
        state.platform = settings.platform || "trendyol";
        state.sellerModel = settings.seller_model || null;
      } catch (_) {}
      try {
        const st = await api("/api/status");
        if (st.review_scraper_build !== "ldjson-v2") {
          toast("Eski sunucu sürümü algılandı — run.bat ile yeniden başlatın (gerekirse 8765 portu).", "err");
        }
      } catch (_) {}
      try {
        await refreshData();
      } catch (e) {
        toast(e.message, "err");
      }
    })();
  };

  window.initAssistantEvents = function () {
    if ($("asBackBtn")) $("asBackBtn").onclick = goHub;
    $("askBtn").onclick = submitAsk;
    $("askInput")?.addEventListener("keydown", e => { if (e.key === "Enter") submitAsk(); });
  };
})();
