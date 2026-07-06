/** Mağaza Asistanı — premium rapor dashboard (mockup uyumlu) */
(function (global) {
  const COLORS = {
    positive: "#5eead4",
    negative: "#f87171",
    neutral: "#fbbf24",
    lilac: "#9d8ffa",
    peach: "#f9b17c",
    violet: "#8b7cf6",
    reviewBar: "#9d8ffa",
    returnBar: "#f87171",
    palette: ["#9d8ffa", "#8b7cf6", "#f9b17c", "#5eead4", "#f87171", "#c4b5fd", "#fcd34d", "#67e8f9"],
  };

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  }

  const DEMO_DAILY_7 = [
    { reviews: 18, returns: 2, negative: 3, avg_rating: 4.2 },
    { reviews: 22, returns: 1, negative: 4, avg_rating: 4.0 },
    { reviews: 15, returns: 3, negative: 2, avg_rating: 4.3 },
    { reviews: 24, returns: 2, negative: 3, avg_rating: 3.9 },
    { reviews: 19, returns: 1, negative: 2, avg_rating: 4.1 },
    { reviews: 21, returns: 2, negative: 3, avg_rating: 4.2 },
    { reviews: 18, returns: 1, negative: 2, avg_rating: 4.0 },
  ];

  function buildDemoReport(days) {
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - (days - 1));
    const daily = [];
    for (let i = 0; i < days; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const seed = DEMO_DAILY_7[i % DEMO_DAILY_7.length];
      const scale = days <= 7 ? 1 : days <= 15 ? 1.6 : 2.4;
      const rev = Math.max(4, Math.round(seed.reviews * scale * (0.92 + (i % 3) * 0.04)));
      const ret = Math.max(0, Math.round(seed.returns * scale * 0.85));
      daily.push({
        date: d.toISOString().slice(0, 10),
        label: d.toLocaleDateString("tr-TR", { day: "2-digit", month: "2-digit" }),
        reviews: rev,
        returns: ret,
        negative: Math.max(1, Math.round(rev * 0.14)),
        avg_rating: seed.avg_rating,
      });
    }
    const totalR = daily.reduce((a, d) => a + d.reviews, 0);
    const pos = Math.round(totalR * 0.57);
    const neg = Math.round(totalR * 0.14);
    const neu = totalR - pos - neg;
    return {
      period_days: days,
      period_label: days === 30 ? "Son 1 ay" : `Son ${days} gün`,
      start_date: start.toISOString().slice(0, 10),
      end_date: end.toISOString().slice(0, 10),
      _demo: true,
      reviews: {
        total: totalR,
        positive: pos,
        negative: neg,
        neutral: neu,
        average_rating: 4.1,
        topics: [
          { key: "kargo", label: "Kargo gecikmesi", count: 18 },
          { key: "beden", label: "Beden uyumsuz", count: 14 },
          { key: "kalite", label: "Kalite", count: 11 },
          { key: "foto", label: "Fotoğraf farkı", count: 8 },
          { key: "diger", label: "Diğer", count: 6 },
        ],
      },
      returns: {
        total: 12,
        quantity: 19,
        reasons: [
          { label: "Beden uyumsuz", count: 8 },
          { label: "Beğenmedim", count: 5 },
          { label: "Hasarlı", count: 3 },
        ],
      },
      daily_series: daily,
      recent_feedback: [
        { product_name: "Dantel detaylı elbise", rating: 5, sentiment: "positive", text: "Kumaşı harika, tam bedenime uydu.", review_date: end.toISOString().slice(0, 10), initial: "D", topic: "" },
        { product_name: "Oversize sweatshirt", rating: 2, sentiment: "negative", text: "Renk fotoğraftakinden çok farklı geldi.", review_date: new Date(end.getTime() - 86400000).toISOString().slice(0, 10), initial: "O", topic: "Fotoğraf farkı" },
        { product_name: "Şık abiye", rating: 4, sentiment: "positive", text: "Hızlı kargo, paketleme özenli.", review_date: new Date(end.getTime() - 2 * 86400000).toISOString().slice(0, 10), initial: "Ş", topic: "" },
        { product_name: "Kot pantolon", rating: 1, sentiment: "negative", text: "Beden tablosu yanıltıcı, iade ettim.", review_date: new Date(end.getTime() - 3 * 86400000).toISOString().slice(0, 10), initial: "K", topic: "Beden" },
        { product_name: "Basic tişört", rating: 5, sentiment: "positive", text: "Kaliteli kumaş, rengi tam istediğim gibi.", review_date: new Date(end.getTime() - 4 * 86400000).toISOString().slice(0, 10), initial: "B", topic: "" },
        { product_name: "Yazlık elbise", rating: 3, sentiment: "neutral", text: "Fena değil ama kargo biraz gecikti.", review_date: new Date(end.getTime() - 5 * 86400000).toISOString().slice(0, 10), initial: "Y", topic: "Kargo" },
      ],
      metrics: {
        complaint_count: neg,
        complaint_trend_pct: -8,
        review_trend_pct: -12,
        complaint_sparkline: daily.map(d => d.negative),
        review_count: totalR,
        review_sparkline: daily.map(d => d.reviews),
        return_quantity: 19,
        return_sparkline: daily.map(d => d.returns),
        average_rating: 4.1,
        rating_sparkline: daily.map(d => d.avg_rating || 4),
        positive_pct: 57,
      },
      summary: {
        title: "Dönem özeti",
        text: `${totalR} yorum analiz edildi; ortalama ${4.1}★, olumsuz oran %${Math.round(100 * neg / totalR)}.`,
        highlights: [`${totalR} yorum (${pos} olumlu, ${neg} olumsuz)`, "En sık şikayet: kargo gecikmesi"],
        actions: ["Kargo süreçlerini gözden geçirin", "Beden tablolarını güncelleyin"],
      },
    };
  }

  function pieSlices(series) {
    const items = (series || []).filter(s => s.count > 0);
    const total = items.reduce((a, s) => a + s.count, 0) || 1;
    let angle = -90;
    const cx = 100, cy = 100, r = 78, ir = 52;
    const paths = [];
    items.forEach((s, i) => {
      const pct = s.count / total;
      const sweep = pct * 360;
      if (sweep <= 0) return;
      const a1 = (angle * Math.PI) / 180;
      const a2 = ((angle + sweep) * Math.PI) / 180;
      const x1o = cx + r * Math.cos(a1), y1o = cy + r * Math.sin(a1);
      const x2o = cx + r * Math.cos(a2), y2o = cy + r * Math.sin(a2);
      const x1i = cx + ir * Math.cos(a2), y1i = cy + ir * Math.sin(a2);
      const x2i = cx + ir * Math.cos(a1), y2i = cy + ir * Math.sin(a1);
      const large = sweep > 180 ? 1 : 0;
      const color = s.color || COLORS[s.key] || COLORS.palette[i % COLORS.palette.length];
      paths.push({
        d: `M ${x1o} ${y1o} A ${r} ${r} 0 ${large} 1 ${x2o} ${y2o} L ${x1i} ${y1i} A ${ir} ${ir} 0 ${large} 0 ${x2i} ${y2i} Z`,
        color, label: s.label, count: s.count, pct: Math.round(pct * 100),
      });
      angle += sweep;
    });
    return { paths, total };
  }

  function renderNetworkMesh() {
    const nodes = [
      [40, 40], [120, 25], [220, 35], [340, 28], [480, 42], [620, 38], [700, 55],
      [80, 120], [200, 110], [320, 125], [460, 115], [580, 130], [680, 118],
      [50, 200], [180, 210], [300, 195], [420, 220], [540, 205], [650, 215],
      [100, 260], [250, 250], [400, 265], [550, 255], [700, 270],
    ];
    const edges = [
      [0,1],[1,2],[2,3],[3,4],[4,5],[5,6],
      [0,7],[1,8],[2,9],[3,10],[4,11],[5,12],[6,12],
      [7,8],[8,9],[9,10],[10,11],[11,12],
      [7,13],[8,14],[9,15],[10,16],[11,17],[12,18],
      [13,14],[14,15],[15,16],[16,17],[17,18],
      [13,19],[14,20],[15,21],[16,22],[17,23],
      [19,20],[20,21],[21,22],[22,23],
      [8,15],[9,16],[14,21],[2,10],[5,17],
    ];
    const lines = edges.map(([a, b], i) => {
      const [x1, y1] = nodes[a], [x2, y2] = nodes[b];
      return `<line class="rp-mesh-line" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" style="animation-delay:${(i * 0.08).toFixed(2)}s"/>`;
    }).join("");
    const dots = nodes.map(([x, y], i) =>
      `<circle class="rp-mesh-node" cx="${x}" cy="${y}" r="${i % 4 === 0 ? 3.5 : 2.5}" style="animation-delay:${(i * 0.14).toFixed(2)}s"/>`
    ).join("");
    return `<svg class="rp-mesh" viewBox="0 0 720 300" preserveAspectRatio="xMidYMid slice" aria-hidden="true">${lines}${dots}</svg>`;
  }

  function renderDonut(series, emptyLabel) {
    const { paths, total } = pieSlices(series);
    if (!paths.length) return `<p class="rp-empty">${esc(emptyLabel)}</p>`;
    const legend = paths.map(p =>
      `<li><span class="rp-legend-dot" style="background:${p.color};color:${p.color}"></span><span>${esc(p.label)}</span><em>%${p.pct}</em></li>`
    ).join("");
    const gradDefs = paths.map((p, i) =>
      `<linearGradient id="rpDonutG${i}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="${p.color}"/>
        <stop offset="100%" stop-color="${p.color}" stop-opacity="0.65"/>
      </linearGradient>`
    ).join("");
    const svgPaths = paths.map((p, i) =>
      `<path class="rp-donut-seg" style="--i:${i}" d="${p.d}" fill="url(#rpDonutG${i})" filter="url(#rpDonutGlow)"/>`
    ).join("");
    let angleAcc = -90;
    const callouts = paths.map((p) => {
      const mid = angleAcc + (p.pct * 3.6) / 2;
      angleAcc += p.pct * 3.6;
      const rad = (mid * Math.PI) / 180;
      const ox = 100 + 96 * Math.cos(rad);
      const oy = 100 + 96 * Math.sin(rad);
      const anchor = Math.cos(rad) > 0.12 ? "start" : Math.cos(rad) < -0.12 ? "end" : "middle";
      const tx = ox + (Math.cos(rad) > 0.12 ? 4 : Math.cos(rad) < -0.12 ? -4 : 0);
      return `<text class="rp-donut-pct" x="${tx}" y="${oy + 3}" text-anchor="${anchor}" dominant-baseline="middle">%${p.pct}</text>`;
    }).join("");
    return `<div class="rp-donut-wrap">
      <div class="rp-donut-chart">
        <svg class="rp-donut" viewBox="0 0 200 200" aria-hidden="true">
          <defs>${gradDefs}
            <filter id="rpDonutGlow" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="3" result="blur"/>
              <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
          </defs>
          ${svgPaths}${callouts}
        </svg>
        <div class="rp-donut-center"><span class="rp-donut-total">${total}</span><span class="rp-donut-lbl">yorum</span></div>
      </div>
      <ul class="rp-legend rp-legend-donut">${legend}</ul>
    </div>`;
  }

  function renderSentimentBars(series, emptyLabel) {
    const items = (series || []).filter(s => s.count > 0);
    if (!items.length) return `<p class="rp-empty">${esc(emptyLabel)}</p>`;
    const max = Math.max(...items.map(s => s.count), 1);
    const sum = items.reduce((a, x) => a + x.count, 0) || 1;
    return items.map((s, i) => {
      const w = Math.round((100 * s.count) / max);
      const color = s.color || COLORS[s.key] || COLORS.palette[i % COLORS.palette.length];
      const pct = Math.round((100 * s.count) / sum);
      return `<div class="rp-vbar-row">
        <span class="rp-vbar-label">${esc(s.label)}</span>
        <div class="rp-vbar-track"><div class="rp-vbar-fill" style="--w:${w}%;background:${color}"></div></div>
        <span class="rp-vbar-meta"><em>%${pct}</em></span>
      </div>`;
    }).join("");
  }

  function renderWeeklyBars(daily) {
    const rows = (daily || []).slice(-14);
    if (!rows.length) return `<p class="rp-empty">Bu dönemde günlük veri yok.</p>`;
    const max = Math.max(...rows.map(d => Math.max(d.reviews || 0, d.returns || 0)), 1);
    const chartH = 168, chartW = Math.max(380, rows.length * 38);
    const padL = 36, padB = 28, padT = 12;
    const plotW = chartW - padL - 8;
    const plotH = chartH - padB - padT;
    const yTicks = [0, Math.round(max / 2), max];
    const yAxis = yTicks.map(t => {
      const y = padT + plotH - (t / max) * plotH;
      return `<text class="rp-axis-lbl" x="4" y="${y + 4}">${t}</text>
        <line class="rp-axis-line" x1="${padL}" y1="${y}" x2="${chartW}" y2="${y}"/>`;
    }).join("");
    const step = plotW / rows.length;
    const bars = rows.map((d, i) => {
      const x = padL + i * step + step * 0.15;
      const revH = Math.max(2, (d.reviews || 0) / max * plotH);
      const retH = Math.max(2, (d.returns || 0) / max * plotH);
      const bw = step * 0.32;
      const glow = i === rows.length - 2 ? ' filter="url(#rpGlow)"' : "";
      return `<g class="rp-wk-group" style="--gi:${i}">
        <rect class="rp-wk-bar rp-wk-rev"${glow} x="${x}" y="${padT + plotH - revH}" width="${bw}" height="${revH}" rx="3"/>
        <rect class="rp-wk-bar rp-wk-ret" x="${x + bw + 3}" y="${padT + plotH - retH}" width="${bw}" height="${retH}" rx="3"/>
        <text class="rp-wk-lbl" x="${x + bw}" y="${chartH - 6}" text-anchor="middle">${esc(d.label)}</text>
      </g>`;
    }).join("");
    return `<div class="rp-weekly-wrap">
      <svg class="rp-weekly-chart" viewBox="0 0 ${chartW} ${chartH}" preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id="rpBarRev" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#b49aff"/><stop offset="100%" stop-color="#7c5ce0"/>
          </linearGradient>
          <linearGradient id="rpBarRet" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#f9b17c"/><stop offset="100%" stop-color="#e87830"/>
          </linearGradient>
          <filter id="rpGlow"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        ${yAxis}${bars}
      </svg>
      <div class="rp-weekly-legend">
        <span><i class="rp-dot" style="background:${COLORS.reviewBar}"></i> Yorum</span>
        <span><i class="rp-dot" style="background:${COLORS.returnBar}"></i> İade Hızı</span>
      </div>
    </div>`;
  }

  function renderSparkline(values, color) {
    const pts = values || [];
    if (pts.length < 2) return "";
    const max = Math.max(...pts, 0.1);
    const w = 80, h = 28;
    const coords = pts.map((v, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - (v / max) * (h - 6) - 3;
      return [x, y];
    });
    const line = coords.map(c => c.join(",")).join(" ");
    const area = `0,${h} ${line} ${w},${h}`;
    return `<svg class="rp-spark" viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <polygon class="rp-spark-area" points="${area}" fill="${color}"/>
      <polyline class="rp-spark-line" fill="none" stroke="${color}" stroke-width="2" points="${line}"/>
    </svg>`;
  }

  function trendBadge(pct) {
    if (pct == null) return "";
    if (pct > 0) return `<span class="rp-trend up">+${pct}%</span>`;
    if (pct < 0) return `<span class="rp-trend down">${pct}%</span>`;
    return `<span class="rp-trend flat">0%</span>`;
  }

  function renderMetricCards(metrics, ret, daily) {
    const m = metrics || {};
    const days = (daily || []).length || 1;
    const dailyReviews = days ? Math.round((m.review_count || 0) / days) : 0;
    const reviewTrend = m.review_trend_pct != null ? m.review_trend_pct
      : (m.review_sparkline?.length >= 2
        ? Math.round(((m.review_sparkline.at(-1) - m.review_sparkline[0]) / Math.max(m.review_sparkline[0], 1)) * 100)
        : null);
    const retPct = m.return_quantity && m.review_count
      ? Math.round(100 * (m.return_quantity || 0) / Math.max(m.review_count, 1)) : 0;
    const posPct = m.positive_pct != null ? m.positive_pct : 0;
    const cards = [
      { title: "Günlük Yorum Artışı", value: dailyReviews || m.review_count || 0, color: COLORS.pos, spark: m.review_sparkline, trend: reviewTrend },
      { title: "Anlık Şikayet Sayısı", value: m.complaint_count || 0, color: COLORS.lilac, spark: m.complaint_sparkline, trend: m.complaint_trend_pct },
      { title: "Ortalama Puan", value: `${(m.average_rating || 4.1).toFixed(1)}★`, color: COLORS.neutral, spark: m.rating_sparkline, trend: null },
      { title: "İade Hızı", value: `%${retPct}`, color: "#67e8f9", spark: m.return_sparkline, trend: null },
      { title: "Pozitif Oran", value: `%${posPct}`, color: COLORS.positive, spark: m.review_sparkline, trend: null },
      { title: "Toplam İade", value: m.return_quantity || ret?.quantity || 0, color: COLORS.peach, spark: m.return_sparkline, trend: null },
    ];
    return cards.map((c, i) => `
      <div class="rp-metric-card" style="--mc:${c.color};--mi:${i}">
        <div class="rp-metric-top">
          <span class="rp-metric-title">${esc(c.title)}</span>
          ${trendBadge(c.trend)}
        </div>
        <div class="rp-metric-row">
          <div class="rp-metric-value">${esc(c.value)}</div>
          ${renderSparkline(c.spark, c.color)}
        </div>
      </div>`).join("");
  }

  function renderTopicsCombo(topics) {
    const items = (topics || []).filter(t => t.count > 0);
    if (!items.length) return `<p class="rp-empty">Şikayet konusu verisi yok.</p>`;
    return `<div class="rp-topics-combo">
      <div class="rp-topics-cloud">${renderTopicCloud(items)}</div>
      <div class="rp-topics-map">${renderTopicTreemap(items, true)}</div>
    </div>`;
  }

  function renderTopicTreemap(topics, compact) {
    const items = (topics || []).filter(t => t.count > 0).slice(0, compact ? 4 : 6);
    if (!items.length) return `<p class="rp-empty">Şikayet konusu verisi yok.</p>`;
    const total = items.reduce((a, t) => a + t.count, 0) || 1;
    const W = compact ? 150 : 300;
    const H = compact ? 110 : 130;
    let x = 0;
    const row1 = items.slice(0, 3);
    const row2 = items.slice(3);
    const r1Total = row1.reduce((a, t) => a + t.count, 0) || 1;
    const r2Total = row2.reduce((a, t) => a + t.count, 0) || 1;
    const h1 = row2.length ? 72 : H;
    const h2 = H - h1 - 4;

    function rowRects(row, y, h, rowTotal, startIdx) {
      let cx = 0;
      const maxCount = Math.max(...items.map(x => x.count));
      return row.map((t, i) => {
        const w = Math.max(24, (t.count / rowTotal) * W);
        const idx = startIdx + i;
        const isLead = t.count === maxCount;
        const color = isLead ? COLORS.peach : COLORS.palette[idx % COLORS.palette.length];
        const pct = Math.round((100 * t.count) / total);
        const opacity = isLead ? 0.95 : 0.55 + (0.35 * t.count) / maxCount;
        const out = `<g class="rp-tm-cell${isLead ? " rp-tm-lead" : ""}" style="--ti:${idx}">
          <rect x="${cx}" y="${y}" width="${w - 2}" height="${h}" rx="6" fill="${color}" opacity="${opacity.toFixed(2)}"/>
          <text x="${cx + (w - 2) / 2}" y="${y + h / 2 - 4}" text-anchor="middle" class="rp-tm-label">${esc(t.label)}</text>
          <text x="${cx + (w - 2) / 2}" y="${y + h / 2 + 10}" text-anchor="middle" class="rp-tm-pct">%${pct}</text>
        </g>`;
        cx += w;
        return out;
      }).join("");
    }

    return `<div class="rp-treemap-wrap">
      <svg class="rp-treemap" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
        ${rowRects(row1, 0, h1, r1Total, 0)}
        ${row2.length ? rowRects(row2, h1 + 4, h2, r2Total, row1.length) : ""}
      </svg>
    </div>`;
  }

  function renderTopicCloud(topics) {
    const items = (topics || []).filter(t => t.count > 0).slice(0, 7);
    if (!items.length) return `<p class="rp-empty">Şikayet konusu verisi yok.</p>`;
    const max = Math.max(...items.map(t => t.count), 1);
    const slots = [
      { x: 48, y: 52, rot: -8 },
      { x: 22, y: 38, rot: 5 },
      { x: 76, y: 35, rot: -4 },
      { x: 28, y: 72, rot: 3 },
      { x: 72, y: 70, rot: -6 },
      { x: 50, y: 22, rot: 2 },
      { x: 85, y: 58, rot: 4 },
    ];
    return `<div class="rp-wordcloud-visual">${items.map((t, i) => {
      const slot = slots[i % slots.length];
      const sz = 0.75 + (1.1 * t.count) / max;
      const c = COLORS.palette[i % COLORS.palette.length];
      return `<span class="rp-word-float" style="--wi:${i};--c:${c};left:${slot.x}%;top:${slot.y}%;font-size:${(11 * sz).toFixed(1)}px;color:${c};transform:translate(-50%,-50%) rotate(${slot.rot}deg)">${esc(t.label)}</span>`;
    }).join("")}</div>`;
  }

  function sentimentEmoji(sent) {
    if (sent === "positive") return "😊";
    if (sent === "negative") return "😞";
    return "😐";
  }

  function renderFeedbackStream(recent) {
    const rows = (recent || []).slice(0, 3);
    if (!rows.length) return `<p class="rp-empty">Bu dönemde geri bildirim yok.</p>`;
    return `<div class="rp-feed-strip">${rows.map((f, i) => {
      const sent = f.sentiment || "neutral";
      const cls = sent === "positive" ? "pos" : sent === "negative" ? "neg" : "neu";
      const text = f.text ? esc(f.text) : "Yorum metni yok";
      const mood = sent === "positive" ? "😊🙂😐" : sent === "negative" ? "😐😕😞" : "🙂😐😶";
      return `<article class="rp-feed-strip-item" style="--fi:${i}">
        <div class="rp-feed-avatar ${cls}" aria-hidden="true">${esc(f.initial || "?")}</div>
        <div class="rp-feed-body">
          <div class="rp-feed-emojis" aria-hidden="true">${mood}</div>
          <p class="rp-feed-text">${text}</p>
          <span class="rp-feed-product">${esc(f.product_name)}</span>
          <span class="rp-feed-date">${esc(f.review_date)}</span>
        </div>
      </article>`;
    }).join("")}</div>`;
  }

  function reviewSeries(data, mode) {
    const r = data.reviews || {};
    if (mode === "topics") {
      return (r.topics || []).map((s, i) => ({
        key: s.key || s.label, label: s.label, count: s.count,
        color: COLORS.palette[i % COLORS.palette.length],
      }));
    }
    return [
      { key: "positive", label: "Pozitif", count: r.positive || 0, color: COLORS.positive },
      { key: "neutral", label: "Nötr", count: r.neutral || 0, color: COLORS.neutral },
      { key: "negative", label: "Negatif", count: r.negative || 0, color: COLORS.negative },
    ];
  }

  function renderReportPanel(data, chartType, reviewMode, opts) {
    opts = opts || {};
    const r = data.reviews || {};
    const ret = data.returns || {};
    const daily = data.daily_series || [];
    const metrics = data.metrics || {};
    const recent = data.recent_feedback || [];
    const reviewViz = reviewSeries(data, reviewMode);
    const sentimentTitle = reviewMode === "topics" ? "Şikayet konuları" : "Duygu dağılımı";
    const sentimentViz = chartType === "pie"
      ? renderDonut(reviewViz.map(s => ({ ...s, key: s.label })), "Bu dönemde yorum verisi yok.")
      : renderSentimentBars(reviewViz, "Bu dönemde yorum verisi yok.");

    const demoBanner = (data._demo || opts.demo)
      ? `<p class="rp-demo-banner">Örnek veri gösteriliyor — yorum/iade CSV yükleyin veya API bağlantısını kontrol edin.</p>`
      : "";

    return `
      <div class="rp-dashboard" data-rp-build="premium-v4">
        ${renderNetworkMesh()}
        <div class="rp-bg-grid" aria-hidden="true"></div>
        <div class="rp-flow-lines" aria-hidden="true"></div>
        ${demoBanner}
        <p class="rp-period-meta">${esc(data.period_label)} · ${esc(data.start_date)} — ${esc(data.end_date)}</p>

        <div class="rp-main-grid">
          <section class="rp-widget rp-widget-donut">
            <header class="rp-widget-head"><strong>${esc(sentimentTitle)}</strong><span>${r.total || 0} kayıt</span></header>
            <div class="rp-widget-body">${sentimentViz}</div>
          </section>

          <section class="rp-widget rp-widget-weekly">
            <header class="rp-widget-head"><strong>Haftalık Yorum &amp; İade Sütun Grafik</strong><span>Günlük karşılaştırma</span></header>
            <div class="rp-widget-body">${renderWeeklyBars(daily)}</div>
          </section>

          <aside class="rp-metrics-rail" aria-label="Anlık metrikler">
            ${renderMetricCards(metrics, ret, daily)}
          </aside>

          <section class="rp-widget rp-widget-topics">
            <header class="rp-widget-head"><strong>Popüler Şikayet Konuları</strong><span>Kelime bulutu</span></header>
            <div class="rp-widget-body">${renderTopicsCombo(r.topics)}</div>
          </section>

          <section class="rp-widget rp-widget-feed">
            <header class="rp-widget-head"><strong>Son Geri Bildirimler</strong><span>${Math.min(recent.length, 3)} kayıt</span></header>
            <div class="rp-widget-body">${renderFeedbackStream(recent)}</div>
          </section>
        </div>
        <div class="rp-connectors" aria-hidden="true"></div>
      </div>`;
  }

  function refreshPanel(data, chartType, reviewMode, opts) {
    const el = document.getElementById("reportsContent");
    if (el && data) el.innerHTML = renderReportPanel(data, chartType, reviewMode, opts);
  }

  global.AssistantReports = {
    COLORS,
    renderReportPanel,
    refreshPanel,
    buildDemoReport,
    reviewSeries,
  };
})(window);
