(() => {
  "use strict";

  const API = { status: "/api/status", health: "/api/health", clock: "/api/clock", scanner: "/api/scanner", analyze: "/api/analyze" };
  const routeLabels = {
    dashboard: "Dashboard",
    scanner: "Market Scanner",
    analyst: "AI Analyst",
    risk: "Analysis & Risk",
    activity: "Activity & Settings"
  };
  const view = document.getElementById("view");
  const toastRegion = document.getElementById("toast-region");
  const state = { route: "dashboard", status: null, statusError: null, online: navigator.onLine, installPrompt: null, clock: null, lastCoordinator: null };
  let requestSequence = 0;

  const icon = (name) => {
    const paths = {
      scanner: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.3 4.3M7.8 11.2l1.9-2 2.2 2.5 2.3-3"/>',
      message: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5h-6L6 20v-4.4a2.5 2.5 0 0 1-2-2.5v-7.6Z"/><path d="M8 8h8M8 11.5h5"/>',
      clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.2 2"/>',
      shield: '<path d="M12 3 20 6v5.4c0 4.6-3.2 7.8-8 9.6-4.8-1.8-8-5-8-9.6V6l8-3Z"/><path d="m8.5 12.2 2.5 2.5 4.7-5"/>',
      info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
      arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
      reload: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9a7 7 0 0 1 11.8-2L20 12M4 12l2.7 5a7 7 0 0 0 11.8-2"/>',
      book: '<path d="M5 4h13a1 1 0 0 1 1 1v15H6a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1Z"/><path d="M7 4v14a2 2 0 0 0 2 2M9 8h7M9 11h7"/>',
      wifi: '<path d="M4 9a12 12 0 0 1 16 0M7.5 12.5a7 7 0 0 1 9 0M11 16a2 2 0 0 1 2 0"/>',
      check: '<path d="m5 12 4 4L19 6"/>'
    };
    return `<svg class="notice-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  const safeJson = (value) => {
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
  };
  const statusTag = (status) => {
    const label = status || "unknown";
    return `<span class="action-state ${esc(label)}">${esc(label.replaceAll("_", " "))}</span>`;
  };
  const shortDate = (value) => {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };
  const toast = (message, isError = false) => {
    const item = document.createElement("div");
    item.className = `toast${isError ? " error" : ""}`;
    item.setAttribute("role", isError ? "alert" : "status");
    item.textContent = message;
    toastRegion.append(item);
    window.setTimeout(() => item.remove(), 4600);
  };

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
    });
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const message = data?.error?.message || data?.message || `Request failed (${response.status}).`;
      const error = new Error(message);
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function setConnection(isConnected, label) {
    const dot = document.getElementById("connection-dot");
    const labelNode = document.getElementById("connection-label");
    const sideDot = document.getElementById("side-status-dot");
    const sideTitle = document.getElementById("side-status-title");
    const sideCopy = document.getElementById("side-status-copy");
    dot.className = `status-dot ${isConnected ? "" : state.online ? "warn" : "offline"}`;
    sideDot.className = `status-dot ${isConnected ? "" : state.online ? "warn" : "offline"}`;
    labelNode.textContent = label;
    sideTitle.textContent = isConnected ? "System reachable" : state.online ? "Service unavailable" : "Offline";
    sideCopy.textContent = isConnected ? "Existing Python tools are ready to check." : "Live operations need the local service.";
  }

  function providerState(provider) {
    if (provider.connection_verified) return { text: "Verified by request", className: "verified" };
    if (provider.configured) return { text: "Configured · unverified", className: "" };
    return { text: "Needs configuration", className: "needs" };
  }

  function providerLines(providers = []) {
    if (!providers.length) return '<p class="muted-detail">Provider information is not available from the status endpoint.</p>';
    return `<div class="provider-list">${providers.map((provider) => {
      const badge = providerState(provider);
      return `<div class="provider-line"><div class="provider-meta"><span class="provider-name">${esc(provider.name)}</span><span class="provider-tag">Priority ${esc(provider.priority)}</span></div><span class="badge ${badge.className}">${esc(badge.text)}</span></div>`;
    }).join("")}</div>`;
  }

  function pageHead(kicker, title, subtitle, action = "") {
    return `<div class="page-head"><div><p class="eyebrow">${esc(kicker)}</p><h1>${esc(title)}</h1><p class="page-subtitle">${esc(subtitle)}</p></div>${action}</div>`;
  }

  function setView(html, busy = false) {
    view.innerHTML = html;
    view.setAttribute("aria-busy", String(busy));
  }

  function renderDashboard() {
    if (!state.status) {
      if (state.statusError) {
        setView(`${pageHead("Research desk", "The service is not responding.", "The app shell is ready, but current system status could not be loaded.")}<div class="notice error">${icon("info")}<span>${esc(state.statusError)}</span><button class="text-button" type="button" data-refresh>Try again</button></div><div class="hero-panel loading-hero"><div class="hero-content"><div class="hero-overline">GE Nexus · Python tools</div><h2>Research first. Decisions stay yours.</h2><p>When the local service is available, existing deterministic workflows and the optional coordinator will appear here.</p><div class="hero-actions"><button class="button" data-go="scanner" type="button">Open Market Scanner ${icon("arrow")}</button></div></div></div>`);
      } else {
        setView(`${pageHead("Research desk", "Your market work, in one place.", "Connecting to the local service for system status, the exchange clock, and saved research.")}<div class="dashboard-grid"><div class="hero-panel"><div class="hero-content"><div class="hero-overline">GE Nexus · Python tools</div><h2>Research first. Decisions stay yours.</h2><p>Run deterministic workflows from one focused desk, with an optional AI coordinator.</p><div class="hero-actions"><button class="button" data-go="scanner" type="button">Open Market Scanner ${icon("arrow")}</button></div></div></div><div class="panel dashboard-status"><div class="skeleton skeleton-w40 skeleton-h13"></div><div class="skeleton skeleton-w70 skeleton-h20 skeleton-gap20"></div><div class="skeleton skeleton-w85 skeleton-h11 skeleton-gap12"></div></div></div>`, true);
      }
      return;
    }
    const status = state.status;
    const engineOk = Boolean(status.engine?.available);
    const watchlist = status.watchlist || {};
    let watchContent = "";
    if (watchlist.available && Array.isArray(watchlist.candidates) && watchlist.candidates.length) {
      watchContent = `<table class="watchlist-table"><thead><tr><th>Symbol</th><th>Setups reported</th></tr></thead><tbody>${watchlist.candidates.map((item) => `<tr><td>${esc(item.symbol)}</td><td><div class="setup-pills">${(item.setups || []).length ? item.setups.map((setup) => `<span class="setup-pill">${esc(setup)}</span>`).join("") : '<span class="muted-detail">No setups listed</span>'}</div></td></tr>`).join("")}</tbody></table>`;
    } else {
      const reasonText = watchlist.reason === "no_saved_watchlist" ? "No saved watchlist was reported by the API." :
        watchlist.reason === "watchlist_unreadable" ? "A saved watchlist is present but could not be read." :
        watchlist.reason ? "No valid saved watchlist is currently available." : "The API has not reported a saved watchlist.";
      watchContent = `<div class="watchlist-empty"><span class="empty-mark">${icon("book")}</span><p>${esc(reasonText)}<br><span class="muted-detail">This view never invents symbols or market data.</span></p></div>`;
    }
    const clockDisplay = state.clock?.data ? safeJson(state.clock.data) : "Request the existing clock tool to see its returned exchange status.";
    const clockSummary = state.clock?.data ? `<p class="clock-value">${esc(state.clock.data.status || state.clock.status || "Clock result received")}</p><p class="clock-note">${esc(state.clock.data.message || state.clock.data.exchange || "Returned by the existing clock tool.")}</p>` : `<p class="clock-value">Clock not checked</p><p class="clock-note">No exchange time is shown until the existing tool responds.</p>`;
    const providerHint = status.provider_checks_are_live === false
      ? "Configuration presence is not a live connectivity check. Only a successful real request can establish provider use."
      : "Provider verification is shown only when reported by the API.";
    const systemLabel = engineOk ? "Python tools available" : "Tool engine unavailable";
    const latest = state.lastCoordinator;
    const recentAnalysis = latest
      ? `<div class="notice">${icon("info")}<span>${latest.summary ? esc(latest.summary) : `Latest coordinator status: ${esc(latest.status || "not reported")}. No explanation was returned.`}<br><span class="muted-detail">${latest.intent ? `Intent: ${esc(latest.intent)}. ` : ""}Held in page memory only; reload clears it.</span></span></div>`
      : `<div class="watchlist-empty"><span class="empty-mark">${icon("book")}</span><p>No coordinator response in this page session.<br><span class="muted-detail">Analysis history is not persisted.</span></p></div>`;
    setView(`
      ${pageHead("Research desk", "Your market work, in one place.", "A focused interface for deterministic research workflows, risk planning, and optional AI coordination.", `<button class="button button-secondary" data-refresh type="button">${icon("reload")} Refresh status</button>`)}
      <div class="dashboard-grid">
        <section class="hero-panel" aria-label="GE Nexus research desk">
          <div class="hero-content"><div class="hero-overline">GE Nexus · Research desk</div><h2>Research first. Decisions stay yours.</h2><p>Run existing Python workflows directly. Bring in the coordinator when a natural-language request is useful.</p>
            <div class="hero-actions"><button class="button" data-go="scanner" type="button">${icon("scanner")} Open Market Scanner</button><button class="button" data-go="analyst" type="button">${icon("message")} Ask AI Analyst</button></div>
          </div>
        </section>
        <section class="panel dashboard-status" aria-labelledby="sys-title">
          <div class="panel-heading"><div><p class="section-kicker">System status</p><h2 id="sys-title">${esc(status.service || "GE Nexus")}</h2></div><span class="inline-state"><span class="status-dot ${engineOk ? "" : "warn"}"></span>${engineOk ? "Available" : "Unavailable"}</span></div>
          <div class="status-main">${esc(systemLabel)}</div><p class="status-description">Interface: ${esc(status.engine?.interface || "Not reported")}</p>
          <div class="status-rule"></div><div class="status-foot"><span>Broker execution</span><strong>${status.capabilities?.broker_execution ? "Capability reported" : "Not available"}</strong></div>
          <div class="status-foot status-foot-spaced"><span>Analysis history</span><strong>${status.analysis_history?.persistent ? "Persistent" : "Not persisted"}</strong></div>
        </section>
      </div>
      <div class="lower-grid">
        <section class="panel" aria-labelledby="clock-title"><div class="panel-heading"><div><p class="section-kicker">Exchange clock</p><h2 id="clock-title">Market time, from the tool</h2><p>Live result from the existing clock action; not inferred from your device.</p></div><button class="text-button" type="button" data-clock>Check clock</button></div>
          <div class="panel-body"><div class="clock-row"><div class="clock-icon">${icon("clock")}</div><div class="clock-result">${clockSummary}</div></div>${state.clock ? `<details class="clock-details"><summary class="text-button">View returned clock data</summary><pre class="clock-data">${esc(clockDisplay)}</pre></details>` : ""}<p class="form-status" id="clock-status" role="status"></p></div>
        </section>
        <section class="panel"><div class="panel-heading"><div><p class="section-kicker">Provider configuration</p><h2>Coordinator providers</h2></div><button class="text-button" type="button" data-go="activity">Details</button></div><div class="panel-body">${providerLines(status.providers)}<p class="provider-footnote">${esc(providerHint)}</p></div></section>
        <section class="panel"><div class="panel-heading"><div><p class="section-kicker">Saved research</p><h2>Watchlist</h2><p>Shown only when the API reports saved candidates.</p></div></div><div class="panel-body">${watchContent}${watchlist.as_of ? `<p class="provider-footnote">As of ${esc(shortDate(watchlist.as_of))}${watchlist.source ? ` · Source ${esc(watchlist.source)}` : ""}</p>` : ""}</div></section>
        <section class="panel"><div class="panel-heading"><div><p class="section-kicker">Recent analysis</p><h2>Latest coordinator response</h2><p>Only available for this page session; no history is persisted.</p></div></div><div class="panel-body">${recentAnalysis}</div></section>
      </div>
      <div class="quick-links" aria-label="Shortcuts">
        <button class="quick-link" type="button" data-go="scanner"><span class="quick-link-icon">${icon("scanner")}</span><span><strong>Run a scan</strong><small>Daily or opening range</small></span></button>
        <button class="quick-link" type="button" data-go="analyst"><span class="quick-link-icon">${icon("message")}</span><span><strong>Coordinate a workflow</strong><small>Natural-language request</small></span></button>
        <button class="quick-link" type="button" data-go="risk"><span class="quick-link-icon">${icon("shield")}</span><span><strong>Review prerequisites</strong><small>Analysis &amp; risk</small></span></button>
      </div>`);
  }

  function renderScanner() {
    setView(`
      ${pageHead("Existing Python workflows", "Market Scanner", "Submit symbols to the configured deterministic scan tools. Results appear only after the service returns them.")}
      <div class="content-grid">
        <section class="panel form-panel" aria-labelledby="scan-title">
          <p class="section-kicker">Run a scan</p><h2 id="scan-title">Choose a scan workflow</h2><p class="form-intro">Enter 1–12 ticker symbols. Duplicates and invalid formats are rejected by the service.</p>
          <form id="scanner-form">
            <fieldset class="scan-mode-fieldset">
              <legend class="field-label scan-mode-legend">Scan type</legend>
              <div class="radio-row">
                <label class="radio-card"><input type="radio" name="mode" value="daily" checked><span><strong>Daily scan</strong><small>Run the existing daily scanner workflow.</small></span></label>
                <label class="radio-card"><input type="radio" name="mode" value="orb"><span><strong>Opening-range scan</strong><small>Run the existing ORB workflow.</small></span></label>
              </div>
            </fieldset>
            <div class="form-field"><label for="scan-symbols">Symbols</label><input class="text-input" id="scan-symbols" name="symbols" maxlength="100" autocomplete="off" placeholder="Enter symbols separated by commas" aria-describedby="symbols-help" required><p class="field-help" id="symbols-help">Example format: SPY, QQQ. Examples are format guidance only, not recommendations. Maximum 12.</p></div>
            <div class="form-actions"><button id="scan-submit" class="button" type="submit">${icon("scanner")} Run scan</button><button class="button button-secondary" type="reset">Clear form</button></div>
            <p id="scan-status" class="form-status" role="status" aria-live="polite"></p>
          </form>
          <div id="scan-result" aria-live="polite"></div>
        </section>
        <aside class="aside-stack">
          <section class="panel info-card"><p class="section-kicker">What this does</p><h3>Existing tool, direct request</h3><p>Your request is sent to <code>/api/scanner</code> as a same-origin JSON POST. GE Nexus does not calculate or supplement scan values in the browser.</p><ul class="info-list"><li><span class="bullet"></span><span>Daily and ORB modes are the only supported options in this interface.</span></li><li><span class="bullet"></span><span>Returned tool data and status are displayed as received.</span></li><li><span class="bullet"></span><span>Scanner tools remain available independently of AI provider configuration.</span></li></ul></section>
          <div class="notice">${icon("info")}<span><strong>Research, not execution.</strong> A scan is not an order, recommendation, or guarantee. Validate output against the source tool and your own risk process.</span></div>
        </aside>
      </div>`);
    const form = document.getElementById("scanner-form");
    form.addEventListener("submit", submitScan);
    form.addEventListener("reset", () => {
      document.getElementById("scan-result").innerHTML = "";
      document.getElementById("scan-status").textContent = "";
    });
  }

  function renderAnalyst() {
    setView(`
      ${pageHead("Optional coordination", "AI Analyst", "Describe a research task in plain language. The coordinator may plan and execute supported Python tools; its actual output and provider attribution are shown below.")}
      <div class="content-grid">
        <section class="panel form-panel" aria-labelledby="analyst-title">
          <p class="section-kicker">Coordinator request</p><h2 id="analyst-title">What would you like to examine?</h2><p class="form-intro">Use a specific, bounded request. The coordinator can only use supported tools and may return a clarification or unavailable result.</p>
          <form id="analyst-form"><div class="form-field"><label for="analysis-request">Your request</label><textarea class="text-area" id="analysis-request" name="request" maxlength="4000" placeholder="Describe the research workflow or risk question you want the coordinator to handle." aria-describedby="analysis-help" required></textarea><p class="field-help" id="analysis-help">Avoid including credentials, API keys, or other secrets. Requests are sent to this local service and are not shown as persisted history in this app.</p></div>
            <div class="form-actions"><button id="analyst-submit" class="button" type="submit">${icon("message")} Send request</button><button class="button button-secondary" type="reset">Clear request</button></div><p id="analyst-status" class="form-status" role="status" aria-live="polite"></p>
          </form>
          <div id="analyst-result" aria-live="polite"></div>
        </section>
        <aside class="aside-stack">
          <section class="panel info-card"><p class="section-kicker">Provider transparency</p><h3>Configuration is not connectivity</h3><p>A provider marked configured has not necessarily been reached. Status checks do not test live provider connectivity. Actual <code>planning_provider</code> and <code>interpretation_provider</code> values are shown only when this request reports them.</p></section>
          <div class="notice warning">${icon("info")}<span><strong>AI providers unavailable?</strong> Python tools remain independently usable from Market Scanner. This coordinator is optional and does not replace deterministic tools.</span></div>
          <section class="panel info-card"><p class="section-kicker">Boundaries</p><h3>Human review stays in the loop</h3><p>Outputs can be incomplete or limited by tool data. No broker execution is offered. Treat interpretations as assistance, not financial advice.</p></section>
        </aside>
      </div>`);
    document.getElementById("analyst-form").addEventListener("submit", submitAnalysis);
    document.getElementById("analyst-form").addEventListener("reset", () => {
      document.getElementById("analyst-result").innerHTML = "";
      document.getElementById("analyst-status").textContent = "";
    });
  }

  function renderRisk() {
    const caps = state.status?.capabilities || {};
    const canScan = Boolean(caps.market_scanner);
    const canCoordinate = Boolean(caps.natural_language_coordination);
    setView(`
      ${pageHead("Tool map & prerequisites", "Analysis & Risk", "Know what this interface can run, what it needs, and what remains unverified before you rely on a result.")}
      <div class="content-grid">
        <section class="panel form-panel"><p class="section-kicker">Supported workflows</p><h2>Available through this service</h2><p class="form-intro">Capability values below are reported by the status API. Results still depend on valid inputs and the underlying tools.</p>
          <div class="workflow-list">
            <article class="workflow-item"><span class="workflow-index">01</span><div><h3>Daily market scanner <span class="provider-tag">${canScan ? "Reported available" : "Not reported available"}</span></h3><p>Uses the daily scanner action through <code>POST /api/scanner</code>. Requires 1–12 valid, unique symbols and a responding local service.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">02</span><div><h3>Opening-range scanner <span class="provider-tag">${canScan ? "Reported available" : "Not reported available"}</span></h3><p>Uses the ORB scanner action through the same endpoint. The browser does not define or modify its calculations.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">03</span><div><h3>Natural-language coordination <span class="provider-tag">${canCoordinate ? "Reported available" : "Not reported available"}</span></h3><p>Uses <code>POST /api/analyze</code>. Requires a usable coordinator/provider for interpretation and planning. A 503 means AI providers are unavailable; scanners remain independently usable.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">04</span><div><h3>Exchange clock</h3><p>Uses the existing clock action via <code>GET /api/clock</code>. The desk displays a clock only after that tool returns a result.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">05</span><div><h3>Entry analysis</h3><p>Available through AI Analyst using the existing entry tool. Requires an explicit ticker; a level setup also needs a supplied price level. Market bars must be returned by the source tool.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">06</span><div><h3>Exit planning</h3><p>Available through AI Analyst. Requires a saved entry result or explicit entry and stop inputs, plus any setup-specific range values.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">07</span><div><h3>Historical simulation</h3><p>Available through AI Analyst when a valid plan is available. The existing Time Machine tool supplies the simulation; historical results are not predictions.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">08</span><div><h3>Position sizing</h3><p>Available through AI Analyst when a compatible plan, budget configuration, and any required price inputs are available to the coordinator.</p></div></article>
            <article class="workflow-item"><span class="workflow-index">09</span><div><h3>Risk checks</h3><p>Available through AI Analyst when proposed-order and risk configuration files are available. A risk result is not broker approval and does not submit an order.</p></div></article>
          </div>
        </section>
        <aside class="aside-stack">
          <div class="notice warning">${icon("shield")}<span><strong>Risk planning is not order execution.</strong> Broker execution is ${state.status?.capabilities?.broker_execution ? "reported by the API, but is not implemented in this frontend." : "not reported as available."} GE Nexus does not submit, route, or manage trades.</span></div>
          <section class="panel info-card"><p class="section-kicker">Before you rely on output</p><h3>Prerequisites to confirm</h3><ul class="info-list"><li><span class="bullet"></span><span>Local API reachable and the intended deterministic tool available.</span></li><li><span class="bullet"></span><span>Input symbols and request scope are accurate and appropriate.</span></li><li><span class="bullet"></span><span>Source data freshness and tool limitations are reviewed.</span></li><li><span class="bullet"></span><span>Provider connectivity is evidenced only by a real successful request.</span></li><li><span class="bullet"></span><span>Any resulting decision is reviewed against your independent risk process.</span></li></ul></section>
          <section class="panel info-card"><p class="section-kicker">No invented state</p><h3>Data-first by design</h3><p>No positions, prices, exposure, performance, or account-specific risk values are invented or estimated by the interface. Only API-reported data is presented.</p></section>
        </aside>
      </div>`);
  }

  function renderActivity() {
    const status = state.status || {};
    const providers = status.providers || [];
    const history = status.analysis_history || {};
    const historyText = history.available && history.persistent
      ? "The API reports persistent history, but this frontend version does not display a history feed."
      : "No persisted analysis history is available in this app version. Requests and results are not saved in browser storage.";
    const providerRows = providers.length ? providers.map((provider) => {
      const tag = providerState(provider);
      return `<div class="config-row"><div><strong>${esc(provider.name)}</strong><small>Priority ${esc(provider.priority)} · ${esc(provider.state || "State not reported")}</small></div><span class="badge ${tag.className}">${esc(tag.text)}</span></div>`;
    }).join("") : '<p class="muted-detail">Provider configuration state is unavailable until the status API responds.</p>';
    setView(`
      ${pageHead("Local app state", "Activity & Settings", "A clear view of what this app retains and what the service reports about its environment.")}
      <div class="settings-grid">
        <div class="aside-stack">
          <section class="panel form-panel"><p class="section-kicker">Environment status</p><h2>Provider configuration</h2><p class="form-intro">Configuration state only. Secret values are never requested or displayed here.</p>
            <div class="provider-config">${providerRows}</div>
            <div class="notice warning settings-warning">${icon("info")}<span><strong>Not a connectivity test.</strong> ${status.provider_checks_are_live === false ? "The API explicitly reports that provider checks are not live. Configured status means required configuration appears present; the connection remains unverified until a real request succeeds." : "Provider configuration does not establish a connection. Treat it as unverified until a real request succeeds."}</span></div>
          </section>
          <section class="panel form-panel"><p class="section-kicker">History</p><h2>Activity is not persisted here</h2><p class="form-intro">${esc(historyText)}</p><div class="notice">${icon("book")}<span>This frontend does not use localStorage or sessionStorage for market results, analysis requests, or sensitive data. Reloading the page clears displayed request output.</span></div></section>
        </div>
        <aside class="aside-stack">
          <section class="panel install-card"><span class="empty-mark">${icon("wifi")}</span><div><h3>Installable research desk</h3><p>GE Nexus can be installed when your browser supports the web app install prompt. The service worker caches only static app-shell files, never API responses.</p><p id="install-help" class="muted-detail install-help">${window.matchMedia("(display-mode: standalone)").matches ? "Running as an installed app." : "Use your browser menu to install when the option is available."}</p></div></section>
          <section class="panel info-card"><p class="section-kicker">Service</p><h3>${esc(status.service || "Status not available")}</h3><p>Python tool interface: <strong>${esc(status.engine?.interface || "not reported")}</strong></p><ul class="info-list"><li><span class="bullet"></span><span>Natural-language coordination: ${status.capabilities?.natural_language_coordination ? "reported available" : "not reported available"}.</span></li><li><span class="bullet"></span><span>Market scanner: ${status.capabilities?.market_scanner ? "reported available" : "not reported available"}.</span></li><li><span class="bullet"></span><span>Broker execution: ${status.capabilities?.broker_execution ? "reported available" : "not reported available"}.</span></li></ul></section>
          <section class="panel info-card"><p class="section-kicker">This page session</p><h3>Most recent coordinator response</h3><p>${state.lastCoordinator ? `Status: ${esc(state.lastCoordinator.status || "not reported")}.` : "No coordinator response has completed in this page session."}</p><p>${state.lastCoordinator?.planning_provider ? `Planning provider: ${esc(state.lastCoordinator.planning_provider)}.` : "No successful planning provider reported."} ${state.lastCoordinator?.interpretation_provider ? `Interpretation provider: ${esc(state.lastCoordinator.interpretation_provider)}.` : ""}</p><p class="muted-detail">This attribution is held in page memory only and is cleared when you reload.</p></section>
          <div class="notice">${icon("shield")}<span><strong>Settings are environment-owned.</strong> Manage provider credentials in the deployment environment, not in this browser. Never paste secrets into analysis requests.</span></div>
        </aside>
      </div>`);
  }

  function render() {
    document.getElementById("current-section").textContent = routeLabels[state.route];
    document.querySelectorAll(".nav-item").forEach((item) => {
      if (item.dataset.route === state.route) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    if (!state.online) document.getElementById("offline-banner").classList.add("visible");
    else document.getElementById("offline-banner").classList.remove("visible");
    if (state.route === "dashboard") renderDashboard();
    else if (state.route === "scanner") renderScanner();
    else if (state.route === "analyst") renderAnalyst();
    else if (state.route === "risk") renderRisk();
    else renderActivity();
  }

  function navigate(route, focus = false) {
    if (!routeLabels[route]) return;
    state.route = route;
    render();
    if (focus) {
      document.getElementById("main-content").focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  async function loadStatus() {
    state.statusError = null;
    if (state.route === "dashboard" && !state.status) render();
    setConnection(false, state.online ? "Connecting to service" : "Offline");
    try {
      const [status] = await Promise.all([api(API.status), api(API.health)]);
      state.status = status;
      state.statusError = null;
      setConnection(true, "Local service reachable");
      render();
    } catch (error) {
      state.status = null;
      state.statusError = error.message;
      setConnection(false, state.online ? "Service unavailable" : "Offline");
      render();
      if (state.online) toast(`Could not load system status: ${error.message}`, true);
    }
  }

  async function checkClock(button) {
    if (button) button.disabled = true;
    const status = document.getElementById("clock-status");
    if (status) status.textContent = "Requesting exchange clock from the existing tool…";
    try {
      state.clock = await api(API.clock);
      if (state.route === "dashboard") renderDashboard();
      toast("Exchange clock result received.");
    } catch (error) {
      if (status) status.textContent = error.message;
      toast(`Clock request failed: ${error.message}`, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function resultCard(title, description, content) {
    return `<section class="panel result-panel"><div class="result-top"><div><h3>${esc(title)}</h3><p>${esc(description)}</p></div></div><div class="result-content">${content}</div></section>`;
  }

  async function submitScan(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = document.getElementById("scan-submit");
    const statusNode = document.getElementById("scan-status");
    const resultNode = document.getElementById("scan-result");
    const mode = new FormData(form).get("mode");
    const symbols = document.getElementById("scan-symbols").value.split(",").map((symbol) => symbol.trim()).filter(Boolean);
    if (!symbols.length || symbols.length > 12) {
      statusNode.textContent = "Enter between 1 and 12 symbols.";
      document.getElementById("scan-symbols").focus();
      return;
    }
    submit.disabled = true;
    statusNode.textContent = "Running the existing scanner tool…";
    resultNode.innerHTML = resultCard("Scan in progress", "Waiting for the local Python tool response.", '<div class="skeleton skeleton-h14 skeleton-w50"></div><div class="skeleton skeleton-h14 skeleton-w83 skeleton-gap12"></div>');
    const seq = ++requestSequence;
    try {
      const result = await api(API.scanner, { method: "POST", body: JSON.stringify({ mode, symbols }) });
      if (seq !== requestSequence) return;
      statusNode.textContent = result.status === "completed" ? "Tool returned a result." : `Tool status: ${result.status || "not reported"}.`;
      resultNode.innerHTML = resultCard("Tool response", `${result.tool || "Scanner"} · ${result.status || "status not reported"}`, `<div class="result-callout">This is the returned action result. No additional market data has been calculated in the browser.</div><pre>${esc(safeJson(result))}</pre>`);
    } catch (error) {
      const unavailableMessage = error.status === 503 ? "AI provider unavailability does not affect these Python scanner tools." : "";
      statusNode.textContent = error.message;
      resultNode.innerHTML = resultCard("Scan could not complete", "The service returned an error or is unreachable.", `<div class="notice error">${icon("info")}<span>${esc(error.message)} ${esc(unavailableMessage)}</span></div><button class="button button-secondary" type="button" data-retry-scan>Retry scan</button>`);
      resultNode.querySelector("[data-retry-scan]")?.addEventListener("click", () => form.requestSubmit());
    } finally {
      submit.disabled = false;
    }
  }

  function renderAnalysisOutput(result) {
    const parts = [];
    if (result.status === "unavailable" || result.error || result.interpretation_error) {
      const message = result.error?.message || result.error || result.interpretation_error?.message || result.interpretation_error;
      parts.push(`<div class="notice ${result.status === "unavailable" ? "warning" : "error"}">${icon("info")}<span><strong>${result.status === "unavailable" ? "AI providers unavailable." : "Coordinator note."}</strong> ${esc(typeof message === "string" ? message : safeJson(message))} ${result.status === "unavailable" ? "Python tools remain independently usable in Market Scanner." : ""}</span></div>`);
    }
    if (result.interpretation?.summary) parts.push(`<div class="result-summary">${esc(result.interpretation.summary)}</div>`);
    const facts = result.interpretation?.reported_facts;
    if (Array.isArray(facts) && facts.length) parts.push(`<h4 class="result-section">Reported facts</h4><ul class="result-list">${facts.map((fact) => `<li>${esc(typeof fact === "string" ? fact : safeJson(fact))}</li>`).join("")}</ul>`);
    if (result.interpretation?.interpretation) parts.push(`<h4 class="result-section">Interpretation</h4><div class="result-summary">${esc(result.interpretation.interpretation)}</div>`);
    if (Array.isArray(result.interpretation?.limitations) && result.interpretation.limitations.length) parts.push(`<h4 class="result-section">Limitations</h4><ul class="result-list">${result.interpretation.limitations.map((item) => `<li>${esc(typeof item === "string" ? item : safeJson(item))}</li>`).join("")}</ul>`);
    else if (typeof result.interpretation?.limitations === "string" && result.interpretation.limitations) parts.push(`<h4 class="result-section">Limitations</h4><div class="result-summary">${esc(result.interpretation.limitations)}</div>`);
    if (result.plan) parts.push(`<h4 class="result-section">Coordinator plan</h4><pre>${esc(safeJson(result.plan))}</pre>`);
    if (Array.isArray(result.actions) && result.actions.length) {
      parts.push(`<h4 class="result-section">Executed actions</h4><div class="result-actions">${result.actions.map((action) => `<article class="action-row"><div class="action-head"><span class="action-name">${esc(action.tool || action.id || "Action")}</span>${statusTag(action.status)}</div>${action.category ? `<p class="muted-detail action-category">Category: ${esc(action.category)}</p>` : ""}<pre>${esc(safeJson(action.data ?? action))}</pre></article>`).join("")}</div>`);
    }
    if (!parts.length) parts.push(`<div class="notice">${icon("info")}<span>The coordinator returned a response without interpretation details or action data.</span></div>`);
    const providers = [
      result.planning_provider ? `<span class="provider-tag">Planning: ${esc(result.planning_provider)}</span>` : "",
      result.interpretation_provider ? `<span class="provider-tag">Interpretation: ${esc(result.interpretation_provider)}</span>` : ""
    ].filter(Boolean).join(" ");
    parts.push(`<details class="raw-output"><summary class="text-button">View complete coordinator response</summary><pre>${esc(safeJson(result))}</pre></details>`);
    return resultCard("Coordinator output", `Status: ${result.status || "not reported"}${providers ? ` · ${result.planning_provider ? `Planning: ${result.planning_provider}` : ""}${result.planning_provider && result.interpretation_provider ? " · " : ""}${result.interpretation_provider ? `Interpretation: ${result.interpretation_provider}` : ""}` : " · Provider attribution not reported"}`, parts.join(""));
  }

  async function submitAnalysis(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = document.getElementById("analyst-submit");
    const statusNode = document.getElementById("analyst-status");
    const resultNode = document.getElementById("analyst-result");
    const request = document.getElementById("analysis-request").value.trim();
    if (!request) {
      statusNode.textContent = "Enter a request before sending.";
      document.getElementById("analysis-request").focus();
      return;
    }
    submit.disabled = true;
    statusNode.textContent = "Coordinator is planning and running supported workflows…";
    resultNode.innerHTML = resultCard("Request in progress", "Waiting for actual coordinator output.", '<div class="skeleton skeleton-h14 skeleton-w47"></div><div class="skeleton skeleton-h14 skeleton-w78 skeleton-gap12"></div>');
    const seq = ++requestSequence;
    try {
      const result = await api(API.analyze, { method: "POST", body: JSON.stringify({ request }) });
      if (seq !== requestSequence) return;
      state.lastCoordinator = {
        status: result.status,
        planning_provider: result.planning_provider || null,
        interpretation_provider: result.interpretation_provider || null,
        summary: result.interpretation?.summary || null,
        intent: result.intent || null
      };
      statusNode.textContent = `Coordinator response received${result.status ? ` · ${result.status}` : ""}.`;
      resultNode.innerHTML = renderAnalysisOutput(result);
    } catch (error) {
      const isUnavailable = error.status === 503;
      if (isUnavailable && error.data && typeof error.data === "object" && error.data.status) {
        state.lastCoordinator = {
          status: error.data.status,
          planning_provider: error.data.planning_provider || null,
          interpretation_provider: error.data.interpretation_provider || null,
          summary: error.data.interpretation?.summary || null,
          intent: error.data.intent || null
        };
        statusNode.textContent = `Coordinator response received · ${error.data.status}`;
        resultNode.innerHTML = renderAnalysisOutput(error.data);
        return;
      }
      statusNode.textContent = error.message;
      const content = `<div class="notice ${isUnavailable ? "warning" : "error"}">${icon("info")}<span><strong>${isUnavailable ? "AI providers unavailable." : "Request failed."}</strong> ${esc(error.message)} ${isUnavailable ? "Python scanner tools remain independently usable from Market Scanner." : ""}</span></div><button class="button button-secondary" type="button" data-retry-analysis>Retry request</button>`;
      resultNode.innerHTML = resultCard("No coordinator output", "The request did not return a coordinator result.", content);
      resultNode.querySelector("[data-retry-analysis]")?.addEventListener("click", () => form.requestSubmit());
    } finally {
      submit.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-route]");
    if (nav) navigate(nav.dataset.route, true);
    const go = event.target.closest("[data-go]");
    if (go) navigate(go.dataset.go, true);
    if (event.target.closest("[data-refresh]")) loadStatus();
    if (event.target.closest("[data-clock]")) checkClock(event.target.closest("[data-clock]"));
  });
  document.querySelector("#refresh-button").addEventListener("click", loadStatus);
  window.addEventListener("online", () => {
    state.online = true;
    document.getElementById("offline-banner").classList.remove("visible");
    setConnection(Boolean(state.status), state.status ? "Local service reachable" : "Connecting to service");
    loadStatus();
  });
  window.addEventListener("offline", () => {
    state.online = false;
    render();
    setConnection(false, "Offline");
  });

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPrompt = event;
    document.getElementById("install-button").hidden = false;
  });
  document.getElementById("install-button").addEventListener("click", async () => {
    if (!state.installPrompt) {
      toast("Use your browser menu to install GE Nexus when installation is available.");
      return;
    }
    state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    document.getElementById("install-button").hidden = true;
  });
  window.addEventListener("appinstalled", () => toast("GE Nexus is installed."));

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      toast("Offline app-shell caching could not be enabled in this browser.", true);
    }));
  }

  render();
  loadStatus();
})();
