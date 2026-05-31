const store = new URLSearchParams(location.search).get("store") || "ST1008";
document.getElementById("storeId").textContent = store;

const history = { visitors: [], conv: [], pos: [], queue: [], aband: [] };
const MAX_HISTORY = 30;

function pushHist(key, val) {
  const arr = history[key];
  arr.push(Number(val) || 0);
  if (arr.length > MAX_HISTORY) arr.shift();
}
function sparkPath(arr) {
  if (!arr.length) return "";
  const min = Math.min(...arr), max = Math.max(...arr);
  const range = max - min || 1;
  return arr.map((v, i) => {
    const x = (i / (MAX_HISTORY - 1)) * 100;
    const y = 30 - ((v - min) / range) * 28;
    return (i === 0 ? "M" : "L") + x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
}
function renderSpark(id, arr) {
  const path = document.querySelector(`#${id} path`);
  if (path) path.setAttribute("d", sparkPath(arr));
}

function fmtPct(n) {
  if (n === null || n === undefined) return "—";
  return Math.min(100, Number(n)).toFixed(1);
}

function handleData(data) {
  const m = data.metrics;
  const f = data.funnel;
  const h = data.heatmap;
  const a = data.anomalies;
  const hp = data.health;

  // Health pill update from backend
  const pill = document.getElementById("healthPill");
  const status = hp?.status || "down";
  pill.textContent = status.toUpperCase();
  pill.className = "health-pill " + (status === "ok" ? "ok" : status === "degraded" ? "degraded" : "down");

  // Cache for smooth UI updates
  window._domCache = window._domCache || {};
  const renderIfChanged = (id, html) => {
    if (window._domCache[id] !== html) {
      document.getElementById(id).innerHTML = html;
      window._domCache[id] = html;
    }
  };

  // KPIs
  if (m) {
    document.getElementById("kVisitors").textContent = m.unique_visitors ?? 0;
    document.getElementById("kConv").textContent     = fmtPct(m.conversion_rate);
    document.getElementById("kPos").textContent      = m.pos_transactions ?? 0;
    document.getElementById("kQueue").textContent    = m.current_queue_depth ?? 0;
    document.getElementById("kAband").textContent    = fmtPct(m.abandonment_rate);

    pushHist("visitors", m.unique_visitors);
    pushHist("conv",     m.conversion_rate);
    pushHist("pos",      m.pos_transactions);
    pushHist("queue",    m.current_queue_depth);
    pushHist("aband",    m.abandonment_rate);
    renderSpark("sparkVisitors", history.visitors);
    renderSpark("sparkConv",     history.conv);
    renderSpark("sparkPos",      history.pos);
    renderSpark("sparkQueue",    history.queue);
    renderSpark("sparkAband",    history.aband);

    const dwellStr = Object.entries(m.avg_dwell_per_zone_ms || {})
      .map(([k, v]) => `${k.replace(/^ZONE_/, "")}: ${(v/1000).toFixed(1)}s`)
      .join(" · ");
    document.getElementById("kVisitorsSub").textContent = dwellStr || "today";

    // Sales Insights
    const renderBar = (dict, elemId) => {
      const keys = Object.keys(dict || {});
      if (!keys.length) return;
      const max = Math.max(...Object.values(dict));
      const html = keys.map(k => `
        <div class="funnel-row fade-in">
          <div class="stage" style="width: 140px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${k}">${k}</div>
          <div class="funnel-bar"><span style="width:${(dict[k]/max)*100}%;"></span></div>
          <div class="count" style="width: 50px">${dict[k]}</div>
        </div>
      `).join("");
      renderIfChanged(elemId, html);
    };
    renderBar(m.top_departments, "departments");
    renderBar(m.top_brands, "brands");
  }

  // Funnel
  if (f?.stages) {
    const max = Math.max(1, ...f.stages.map(s => s.count));
    document.getElementById("funnelBadge").textContent = `${f.total_sessions} sessions`;
    const html = f.stages.map(s => `
      <div class="funnel-row fade-in">
        <div class="stage">${s.stage}</div>
        <div class="funnel-bar"><span style="width:${(s.count/max)*100}%;"></span></div>
        <div class="count">${s.count}</div>
        <div class="drop ${s.drop_off_from_prev_pct > 0 ? '' : 'zero'}">${s.drop_off_from_prev_pct > 0 ? '↓ ' + s.drop_off_from_prev_pct.toFixed(1) + '%' : '—'}</div>
      </div>
    `).join("");
    renderIfChanged("funnel", html);
  }

  // Heatmap
  if (h?.zones) {
    document.getElementById("heatBadge").textContent =
      `${h.total_sessions} sessions · ${h.data_confidence || "?"}`;
    const html = h.zones.map(z => `
      <div class="heat-cell fade-in" style="--i:${(z.intensity||0)/100};">
        <div class="bg"></div>
        <div class="intensity">${z.intensity.toFixed(0)}</div>
        <div class="zone">${z.zone_id.replace(/^ZONE_/, "")}</div>
        <div class="stat">${z.visit_count} visits</div>
        <div class="stat">${(z.avg_dwell_ms/1000).toFixed(1)}s avg dwell</div>
      </div>
    `).join("");
    renderIfChanged("heat", html);
  }

  // Anomalies
  if (a?.anomalies) {
    document.getElementById("anomBadge").textContent = `${a.count} active`;
    const rows = a.anomalies.length ? a.anomalies.map(x => `
      <tr class="fade-in">
        <td><strong>${x.type.replace(/_/g, " ")}</strong></td>
        <td><span class="sev ${x.severity}">${x.severity}</span></td>
        <td><code style="font-size:11px;color:var(--text-2);">${Object.entries(x.detail).map(([k,v]) => `${k}: ${JSON.stringify(v)}`).join(" · ")}</code></td>
        <td style="color:var(--text-2);">${x.suggested_action || ""}</td>
      </tr>
    `).join("") : `<tr><td colspan="4" style="color:var(--text-3); padding:24px 14px;">✓ No active anomalies</td></tr>`;
    renderIfChanged("anomalies", rows);
  }

  // Camera stale indicator
  if (hp?.stores) {
    const store0 = hp.stores.find(s => s.store_id === store);
    if (store0) {
      const ts = new Date(store0.last_event_timestamp);
      const ageMin = ((Date.now() - ts.getTime()) / 60000).toFixed(0);
      document.querySelectorAll(".cam .meta").forEach(el => {
        el.textContent = ageMin + "m ago";
        const dot = el.parentElement.querySelector(".dot");
        if (store0.stale) { dot.style.background = "var(--amber)"; dot.style.color = "var(--amber)"; }
        else { dot.style.background = "var(--emerald)"; dot.style.color = "var(--emerald)"; }
      });
    }
  }
}

function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${location.host}/ws/stores/${store}`;
  
  const pill = document.getElementById("healthPill");
  pill.textContent = "CONNECTING...";
  pill.className = "health-pill degraded";

  const ws = new WebSocket(wsUrl);
  
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleData(data);
    } catch (e) {
      console.error("Error parsing websocket data", e);
    }
  };
  
  ws.onclose = () => {
    console.log("WebSocket disconnected. Reconnecting in 3s...");
    const pill = document.getElementById("healthPill");
    pill.textContent = "DISCONNECTED";
    pill.className = "health-pill down";
    setTimeout(connectWebSocket, 3000);
  };
}

connectWebSocket();

// Smooth clock ticker
setInterval(() => {
  document.getElementById("clock").textContent = new Date().toLocaleTimeString("en-US", { hour12: false });
}, 1000);
