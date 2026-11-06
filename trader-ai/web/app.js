(() => {
  "use strict";

  const API = {
    status: "/api/status",
    universe: "/api/universe",
    scanner: "/api/scanner",
    instrument: "/api/instrument",
    risk: "/api/risk",
    providerCheck: "/api/providers/check"
  };
  const routeLabels = { home: "Home", analysis: "Analysis", settings: "Settings" };
  const STORAGE_KEY = "ge-nexus-default-symbols";
  const view = document.getElementById("view");
  const toastRegion = document.getElementById("toast-region");
  const state = {
    route: "home",
    status: null,
    statusError: null,
    universe: null,
    universeError: null,
    online: navigator.onLine,
    installPrompt: null,
    scans: [],
    latestScan: null,
    scanInput: "",
    scanError: null,
    instrumentSymbol: "",
    instrumentResult: null,
    instrumentError: null,
    riskResult: null,
    riskError: null,
    providerTest: null,
    providerTestError: null,
    providerTestBusy: false,
    settingsSymbols: null,
    settingsError: null
  };

  const icon = (name) => {
    const paths = {
      scan: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.3 4.3M7.8 11.2l1.9-2 2.2 2.5 2.3-3"/>',
      home: '<path d="M4 11.2 12 4l8 7.2v8.3a.5.5 0 0 1-.5.5h-5v-6h-5v6h-5a.5.5 0 0 1-.5-.5v-8.3Z"/>',
      settings: '<circle cx="12" cy="12" r="3.2"/><path d="m19.3 13.5 1 1.1-1.4 2.4-1.5-.4a7 7 0 0 1-1.5.9l-.3 1.6h-2.8l-.3-1.6a7 7 0 0 1-1.5-.9l-1.5.4-1.4-2.4 1-1.1a7 7 0 0 1 0-1.8l-1-1.1 1.4-2.4 1.5.4a7 7 0 0 1 1.5-.9l.3-1.6h2.8l.3 1.6a7 7 0 0 1 1.5.9l1.5-.4 1.4 2.4-1 1.1a7 7 0 0 1 0 1.8Z"/>',
      info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
      reload: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9a7 7 0 0 1 11.8-2L20 12M4 12l2.7 5a7 7 0 0 0 11.8-2"/>',
      arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
      shield: '<path d="M12 3 20 6v5.4c0 4.6-3.2 7.8-8 9.6-4.8-1.8-8-5-8-9.6V6l8-3Z"/><path d="m8.5 12.2 2.5 2.5 4.7-5"/>'
    };
    return `<svg class="notice-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
  };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
  const safeJson = (value) => {
    try { return JSON.stringify(value, null, 2); } catch { return String(value); }
  };
  const shortDate = (value) => {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  };
  const asText = (value) => typeof value === "string" ? value : safeJson(value);
  const toast = (message, isError = false) => {
    const item = document.createElement("div");
    item.className = `toast${isError ? " error" : ""}`;
    item.setAttribute("role", isError ? "alert" : "status");
    item.textContent = message;
    toastRegion.append(item);
    window.setTimeout(() => item.remove(), 4300);
  };

  async function api(url, options = {}) {
    let response;
    try {
      response = await fetch(url, {
        ...options,
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
      });
    } catch (cause) {
      const error = new Error("The local research service could not be reached.");
      error.cause = cause;
      error.url = url;
      throw error;
    }
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const message = response.status === 503
        ? "This tool is temporarily unavailable."
        : response.status === 404
          ? "This tool is not available from the service."
          : response.status >= 500
            ? "The service could not complete this request."
            : "Review the request inputs and try again.";
      const error = new Error(message);
      error.status = response.status;
      error.data = data;
      error.url = url;
      throw error;
    }
    return data;
  }

  function errorDetails(error) {
    const details = {
      message: error?.message || "Request failed",
      status: error?.status || null,
      endpoint: error?.url || null,
      response: error?.data || null,
      technical: error?.cause?.message || null
    };
    return `<details class="details-block"><summary>Technical details</summary><pre>${esc(safeJson(details))}</pre></details>`;
  }

  function pageHead(kicker, title, subtitle, action = "") {
    return `<div class="page-head"><div><p class="eyebrow">${esc(kicker)}</p><h1>${esc(title)}</h1><p class="page-subtitle">${esc(subtitle)}</p></div>${action}</div>`;
  }

  function setView(html, busy = false) {
    view.innerHTML = html;
    view.setAttribute("aria-busy", String(busy));
  }

  function setConnection(connected, label) {
    const dot = document.getElementById("connection-dot");
    const labelNode = document.getElementById("connection-label");
    const sideDot = document.getElementById("side-status-dot");
    const sideTitle = document.getElementById("side-status-title");
    const sideCopy = document.getElementById("side-status-copy");
    const stateClass = connected ? "" : state.online ? "warn" : "offline";
    dot.className = `status-dot ${stateClass}`;
    sideDot.className = `status-dot ${stateClass}`;
    labelNode.textContent = label;
    sideTitle.textContent = connected ? "Service responding" : state.online ? "Service unavailable" : "Offline";
    sideCopy.textContent = connected ? "Python research tools can be requested." : "Live research needs the local service.";
  }

  function savedSymbols() {
    if (state.settingsSymbols) return state.settingsSymbols;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved !== null) return saved;
    } catch { /* Storage is optional; keep the in-memory defaults. */ }
    return (state.universe?.default_symbols || []).join(", ");
  }

  function parseSymbols(value) {
    return value.split(/[,\n\r]+/).map((symbol) => symbol.trim().toUpperCase()).filter(Boolean);
  }

  function symbolsError(symbols) {
    const max = Number(state.universe?.max_symbols) || 20;
    if (symbols.length > max) return `Use no more than ${max} ticker symbols.`;
    if (symbols.some((symbol) => !/^[A-Z][A-Z0-9.-]{0,9}$/.test(symbol))) return "Use valid ticker symbols separated by commas or new lines.";
    if (new Set(symbols).size !== symbols.length) return "Remove duplicate ticker symbols.";
    return "";
  }

  function formSymbols(value) {
    const symbols = parseSymbols(value);
    return symbolsError(symbols) ? null : symbols;
  }

  function providerStatus(provider) {
    const tested = state.providerTest?.verified &&
      String(state.providerTest.provider || "").toLowerCase() === String(provider.name || "").toLowerCase();
    if (tested) return { text: "Tested this session", className: "verified" };
    return provider.configured ? { text: "Configured · not tested", className: "" } : { text: "Needs setup", className: "needs" };
  }

  function providerRows(providers = []) {
    if (!providers.length) return '<div class="empty-state"><p>Provider configuration is not available until service status responds.</p></div>';
    return providers.map((provider) => {
      const setup = providerStatus(provider);
      const envs = [provider.key_env, provider.model_env, provider.endpoint_env].filter(Boolean);
      const endpoint = provider.endpoint || provider.base_url || provider.api_base || null;
      const presence = [
        provider.key_env ? `${provider.key_env}: ${provider.key_configured ? "present" : "missing"}` : "",
        provider.model_env ? `${provider.model_env}: ${provider.model_configured ? "present" : "missing"}` : "",
        provider.endpoint_env || ""
      ].filter(Boolean);
      return `<article class="config-row">
        <div><strong>${esc(provider.name || "Provider")}</strong><small>Priority ${esc(provider.priority ?? "not reported")} · ${presence.length ? presence.map(esc).join(" · ") : `Required: ${envs.length ? envs.map(esc).join(", ") : "environment names not reported"}`}</small></div>
        <span class="badge ${setup.className}">${esc(setup.text)}</span>
        ${endpoint ? `<details class="details-block"><summary>Endpoint details</summary><pre>${esc(endpoint)}</pre></details>` : ""}
      </article>`;
    }).join("");
  }

  function diagnosticsSnapshot() {
    const status = state.status ? { ...state.status } : null;
    if (status && Array.isArray(status.providers)) {
      status.providers = status.providers.map((provider) => {
        const setup = { ...provider };
        delete setup.connection_verified;
        delete setup.state;
        return setup;
      });
    }
    return { status, universe: state.universe };
  }

  function rawDetails(label, value) {
    return `<details class="raw-output"><summary>${esc(label)}</summary><pre>${esc(safeJson(value))}</pre></details>`;
  }

  function interpretationPanel(result) {
    const interpretation = result?.interpretation;
    if (!interpretation) {
      if (result?.interpretation_error) {
        return `<section class="panel result-panel"><div class="result-top"><div><h3>Optional AI interpretation</h3><p>Deterministic tool output is unaffected.</p></div></div><div class="result-content"><div class="notice warning">${icon("info")}<span>Interpretation was not available for this result.</span></div>${rawDetails("Interpretation error details", result.interpretation_error)}</div></section>`;
      }
      return "";
    }
    const parts = [];
    if (interpretation.summary) parts.push(`<p class="result-summary">${esc(interpretation.summary)}</p>`);
    if (Array.isArray(interpretation.reported_facts) && interpretation.reported_facts.length) {
      parts.push(`<h4 class="result-section">Reported facts</h4><ul class="result-list">${interpretation.reported_facts.map((fact) => `<li>${esc(asText(fact))}</li>`).join("")}</ul>`);
    }
    if (interpretation.interpretation) parts.push(`<h4 class="result-section">Interpretation</h4><p class="result-summary">${esc(interpretation.interpretation)}</p>`);
    if (Array.isArray(interpretation.limitations) && interpretation.limitations.length) {
      parts.push(`<h4 class="result-section">Limitations</h4><ul class="result-list">${interpretation.limitations.map((item) => `<li>${esc(asText(item))}</li>`).join("")}</ul>`);
    } else if (typeof interpretation.limitations === "string" && interpretation.limitations) {
      parts.push(`<h4 class="result-section">Limitations</h4><p class="result-summary">${esc(interpretation.limitations)}</p>`);
    }
    if (!parts.length) parts.push('<p class="muted-detail">The provider returned no interpretation details.</p>');
    return `<section class="panel result-panel interpretation-panel"><div class="result-top"><div><h3>Optional AI interpretation</h3><p>${result.interpretation_provider ? `Returned by ${esc(result.interpretation_provider)}` : "No provider attribution returned"}</p></div><span class="provider-tag">Interpretation only</span></div><div class="result-content">${parts.join("")}${rawDetails("Full interpretation response", interpretation)}</div></section>`;
  }

  function metrics(result) {
    const data = result?.data || {};
    const cells = [
      ["As of", data.as_of && shortDate(data.as_of)],
      ["Source", data.source],
      ["Scanned", data.scanned],
      ["Failed", data.failed],
      ["Universe", data.universe],
      ["Matches", Array.isArray(data.hits) ? data.hits.length : null],
      ["Created", data.created_at && shortDate(data.created_at)],
      ["Return code", result?.returncode]
    ].filter(([, value]) => value !== undefined && value !== null && value !== "");
    const counts = data.counts && typeof data.counts === "object"
      ? Object.entries(data.counts).map(([key, value]) => [key, value])
      : [];
    return `<div class="data-grid">${[...cells, ...counts].map(([key, value]) => `<div class="data-cell"><span>${esc(key)}</span><strong>${esc(typeof value === "object" ? safeJson(value) : value)}</strong></div>`).join("")}</div>`;
  }

  function scanTable(result) {
    const hits = result?.data?.hits;
    if (!Array.isArray(hits) || !hits.length) {
      return `<div class="empty-state"><span class="empty-mark">${icon("info")}</span><p>No matches were returned by this scan. This is the tool's reported outcome, not a browser-generated result.</p></div>`;
    }
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Symbol</th><th>Price</th><th>RSI</th><th>Setups</th><th>At</th><th>OR low</th><th>OR high</th><th>% over</th></tr></thead><tbody>${hits.map((hit) => `<tr>
      <td>${esc(hit.symbol || "—")}</td><td>${hit.price == null ? "—" : esc(hit.price)}</td><td>${hit.rsi == null ? "—" : esc(hit.rsi)}</td>
      <td>${hit.setups == null ? "—" : esc(Array.isArray(hit.setups) ? hit.setups.join(", ") : hit.setups)}</td><td>${hit.at == null ? "—" : esc(hit.at)}</td>
      <td>${hit.or_low == null ? "—" : esc(hit.or_low)}</td><td>${hit.or_high == null ? "—" : esc(hit.or_high)}</td><td>${hit.pct_over == null ? "—" : esc(hit.pct_over)}</td>
    </tr>`).join("")}</tbody></table></div>`;
  }

  function scanOutput(result, index = "") {
    const data = result?.data || {};
    const modeLabel = data.mode === "orb" ? "Opening-range scan" : data.mode === "daily" ? "Daily scan" : "Market scan";
    const status = result?.status || "status not reported";
    const title = `${modeLabel}${index ? ` · ${index}` : ""}`;
    const meta = [result?.tool, status, data.as_of ? `As of ${shortDate(data.as_of)}` : ""].filter(Boolean).join(" · ");
    const sourceLine = [data.source, data.created_by].filter(Boolean).join(" · ");
    return `<section class="panel result-panel">
      <div class="result-top"><div><h3>${esc(title)}</h3><p>${esc(meta)}</p></div><span class="badge ${status === "completed" ? "verified" : status === "failed" ? "failed" : ""}">${esc(status.replaceAll("_", " "))}</span></div>
      <div class="result-content">
        <div class="result-callout">Deterministic tool result${sourceLine ? ` · ${esc(sourceLine)}` : ""}${data.mode === "daily" ? " · Prior completed daily closes; suited to pre/post-market review." : data.mode === "orb" ? " · Intraday five-minute bars; only useful during the session." : ""}</div>
        ${metrics(result)}${scanTable(result)}
        ${data.filters ? `<h4 class="result-section">Tool filters</h4><pre>${esc(safeJson(data.filters))}</pre>` : ""}
        ${rawDetails("Complete scanner response", result)}
      </div>
    </section>${interpretationPanel(result)}`;
  }

  function errorPanel(title, error, retryAttr) {
    return `<section class="panel result-panel"><div class="result-top"><div><h3>${esc(title)}</h3><p>No result was returned.</p></div></div><div class="result-content"><div class="notice error">${icon("info")}<span>${esc(error?.message || "The request could not be completed.")}</span></div>${retryAttr ? `<button class="button button-secondary" type="button" ${retryAttr}>Try again</button>` : ""}${errorDetails(error)}</div></section>`;
  }

  function scanForm(id, title) {
    const symbols = state.scanInput || savedSymbols();
    return `<section class="panel scan-controls" aria-labelledby="${id}-heading">
      <p class="section-kicker">Deterministic scanner</p><h2 id="${id}-heading">${esc(title)}</h2>
      <p class="form-intro">Runs the existing Python scanner. Daily uses prior completed closes; opening range uses intraday five-minute bars during the session.</p>
      <form id="${id}">
        <fieldset class="scan-mode-fieldset"><legend class="field-label">Scan mode</legend><div class="radio-row">
          <label class="radio-card"><input type="radio" name="mode" value="daily" checked><span><strong>Daily</strong><small>Pre / post-market overview</small></span></label>
          <label class="radio-card"><input type="radio" name="mode" value="orb"><span><strong>Opening range</strong><small>Intraday session only</small></span></label>
        </div></fieldset>
        <div class="scan-inline"><div class="form-field"><label for="${id}-symbols">Ticker universe</label><input class="text-input" id="${id}-symbols" name="symbols" value="${esc(symbols)}" autocomplete="off" placeholder="Leave blank to use server defaults" aria-describedby="${id}-help"><p class="field-help" id="${id}-help">Comma-separated or newline-separated symbols. Up to ${esc(state.universe?.max_symbols || 20)}. Blank uses server defaults.</p></div><button class="button" type="submit" data-scan-submit>${icon("scan")} Run scan</button></div>
        <p class="form-status" data-scan-status role="status" aria-live="polite"></p>
      </form>
      <div data-scan-result aria-live="polite">${state.scanError ? errorPanel("Scan could not complete", state.scanError, "data-retry-scan") : ""}</div>
    </section>`;
  }

  function renderHome() {
    if (!state.status && !state.statusError) {
      setView(`${pageHead("Research desk", "Research starts with evidence.", "A focused desk for deterministic market tools. AI interpretation is optional; results come from the tools.")}<div class="home-grid"><section class="hero-panel"><div class="hero-content"><p class="hero-overline">GE Nexus · Python research tools</p><h2>Evidence first. Interpretation second.</h2><p>Run existing scanners and quote analysis directly. No chat layer, simulated data, or broker activity.</p></div></section><section class="panel system-card"><div class="skeleton skeleton-h14 skeleton-w40"></div><div class="skeleton skeleton-h14 skeleton-w70 skeleton-gap12"></div></section></div><div class="home-lower">${scanForm("home-scan-form", "Run a market scan")}<section class="panel"><div class="panel-heading"><div><p class="section-kicker">Latest research</p><h2>Completed scans</h2></div></div><div class="panel-body"><div class="empty-state"><p>Loading the local service. Research appears here only after a tool returns it.</p></div></div></section></div>`, true);
      return;
    }
    const status = state.status || {};
    const engineOK = Boolean(status.engine?.available);
    const latest = state.latestScan;
    const latestContent = latest ? scanOutput(latest, "latest") : state.scanError
      ? errorPanel("Latest scan", state.scanError, 'data-go="analysis"')
      : `<div class="empty-state"><span class="empty-mark">${icon("info")}</span><p>No completed scan in this page session. Results are held in memory only; reload clears them.</p></div>`;
    setView(`
      ${pageHead("GE Nexus · Research desk", "Research starts with evidence.", "Deterministic Python tools return the evidence. Optional AI can interpret actual results.", `<button class="button button-secondary" type="button" data-refresh>${icon("reload")} Refresh status</button>`)}
      <div class="home-grid">
        <section class="hero-panel"><div class="hero-content"><p class="hero-overline">Research desk · ${esc(status.engine?.interface || "Python interface")}</p><h2>Evidence first. Interpretation second.</h2><p>Run scanners and quote analysis directly. No simulated market data and no broker execution.</p>
          <div class="hero-actions"><button class="button" type="button" data-go="analysis">${icon("scan")} Open Analysis ${icon("arrow")}</button></div>
        </div></section>
        <section class="panel system-card"><div class="panel-heading"><div><p class="section-kicker">Service status</p><h2>${esc(status.service || "GE Nexus")}</h2></div><span class="inline-state"><span class="status-dot ${engineOK ? "" : "warn"}"></span>${engineOK ? "Available" : "Engine unavailable"}</span></div>
          <div class="status-main">${engineOK ? "Python tools available" : "Tool engine unavailable"}</div><p class="status-description">Interface: ${esc(status.engine?.interface || "Not reported")}</p>
          <div class="status-rule"></div><div class="status-foot"><span>Market scanner</span><strong>${status.capabilities?.market_scanner ? "Available" : "Not reported"}</strong></div>
          <div class="status-foot status-foot-spaced"><span>Risk inputs</span><strong>${status.risk_inputs_available ? "Available" : "Not available"}</strong></div>
          <div class="status-foot status-foot-spaced"><span>Broker execution</span><strong>Not available</strong></div>
          ${state.statusError ? `<div class="notice error">${icon("info")}<span>${esc(state.statusError.message || state.statusError)}${errorDetails(state.statusError)}</span></div>` : ""}
        </section>
      </div>
      <div class="home-lower">
        ${scanForm("home-scan-form", "Run a market scan")}
        <section class="panel" aria-labelledby="latest-title"><div class="panel-heading"><div><p class="section-kicker">Page session</p><h2 id="latest-title">Latest completed research</h2><p>Only returned tool results appear here.</p></div></div><div class="panel-body">${latestContent}</div></section>
      </div>`);
  }

  function returnedFactsPanel(title, result, error, options = {}) {
    if (error) return errorPanel(title, error, options.retryAttr);
    if (!result) return `<div class="empty-state"><span class="empty-mark">${icon("info")}</span><p>${esc(options.empty || "No tool result in this page session yet.")}</p></div>`;
    const body = result.data !== undefined ? result.data : result;
    const meta = [result.tool, result.status, result.returncode !== undefined ? `Return code ${result.returncode}` : ""].filter(Boolean).join(" · ");
    let checks = null;
    if (options.risk) {
      const source = result.data && typeof result.data === "object" ? result.data : result;
      checks = source.checks || source.risk_checks || source.results || null;
    }
    const checksMarkup = checks ? `<h4 class="result-section">Returned risk checks</h4>${Array.isArray(checks)
      ? `<ul class="result-list">${checks.map((check) => `<li>${esc(asText(check))}</li>`).join("")}</ul>`
      : `<pre>${esc(safeJson(checks))}</pre>`}` : "";
    return `<section class="panel result-panel"><div class="result-top"><div><h3>${esc(title)}</h3><p>${esc(meta || "Tool response received")}</p></div><span class="badge ${result.status === "completed" ? "verified" : ""}">${esc(result.status || "returned")}</span></div>
      <div class="result-content"><div class="result-callout">Deterministic tool output · returned data only</div>${checksMarkup}<pre>${esc(safeJson(body))}</pre>${rawDetails("Complete tool response", result)}</div>
    </section>${interpretationPanel(result)}`;
  }

  function scanHistoryMarkup() {
    if (!state.scans.length) return '<div class="empty-state"><p>No scans have completed in this page session. Completed API results will be listed here.</p></div>';
    return [...state.scans].reverse().map((result, index) => `<details class="history-item"><summary><span>${esc(result.data?.mode === "orb" ? "Opening range" : "Daily")} · ${esc(result.status || "returned")}</span><small>${esc(shortDate(result.data?.created_at) || `Session result ${state.scans.length - index}`)}</small></summary>${scanOutput(result)}</details>`).join("");
  }

  function renderAnalysis() {
    const status = state.status || {};
    const universe = state.universe;
    const metricsMarkup = `<div class="data-grid">
      <div class="data-cell"><span>Scanner</span><strong>${status.capabilities?.market_scanner ? "Available" : "Not reported"}</strong></div>
      <div class="data-cell"><span>Risk inputs</span><strong>${status.risk_inputs_available ? "Available" : "Unavailable"}</strong></div>
      <div class="data-cell"><span>Broker execution</span><strong>Unavailable</strong></div>
      <div class="data-cell"><span>Max symbols</span><strong>${esc(universe?.max_symbols ?? "Not reported")}</strong></div>
      <div class="data-cell"><span>Universe source</span><strong>${esc(universe?.market?.source || "Not reported")}</strong></div>
    </div>`;
    setView(`
      ${pageHead("Tool results", "Analysis", "Run deterministic tools, inspect returned metrics and risk checks, then review any optional interpretation separately.")}
      ${scanForm("analysis-scan-form", "Run a scanner workflow")}
      <div class="content-grid analysis-grid">
        <section class="panel form-panel" aria-labelledby="instrument-title"><p class="section-kicker">Instrument quote</p><h2 id="instrument-title">Analyze a ticker</h2><p class="form-intro">Requests the existing quote tool. No chart history is created in the browser.</p>
          <form id="instrument-form" class="tool-form"><div class="form-field"><label for="instrument-symbol">Ticker symbol</label><input class="text-input" id="instrument-symbol" name="symbol" maxlength="10" value="${esc(state.instrumentSymbol)}" autocomplete="off" placeholder="e.g. MSFT" required></div><button class="button" type="submit">${icon("scan")} Request quote</button></form>
          <p class="form-status" id="instrument-status" role="status"></p>
          <div id="instrument-result">${returnedFactsPanel("Instrument quote", state.instrumentResult, state.instrumentError, { empty: "Enter a ticker to request actual quote-tool output.", retryAttr: "data-retry-instrument" })}</div>
        </section>
        <section class="panel form-panel" aria-labelledby="risk-title"><p class="section-kicker">Risk tool</p><h2 id="risk-title">Configured risk checks</h2><p class="form-intro">Runs the existing non-writing tool only when order and risk inputs are available. No balances, positions, or orders are assumed.</p>
          <div class="data-grid"><div class="data-cell"><span>Inputs</span><strong>${status.risk_inputs_available ? "Reported available" : "Not available"}</strong></div><div class="data-cell"><span>Execution</span><strong>None</strong></div></div>
          <button class="button button-secondary" type="button" id="risk-button" ${status.risk_inputs_available ? "" : "disabled"}>${icon("shield")} Run risk checks</button>
          <p class="form-status" id="risk-status" role="status">${status.risk_inputs_available ? "" : "Risk checks are unavailable because required configured inputs are not reported."}</p>
          <div id="risk-result">${returnedFactsPanel("Risk check result", state.riskResult, state.riskError, { risk: true, empty: "Risk output will appear here only after the configured tool returns a result.", retryAttr: "data-retry-risk" })}</div>
        </section>
      </div>
      <section class="panel form-panel tool-metrics" aria-labelledby="metrics-title"><p class="section-kicker">Tool metrics</p><h2 id="metrics-title">Available capabilities</h2>${metricsMarkup}<p class="muted-detail">Metrics reflect service status and universe configuration, not market activity.</p></section>
      <section class="panel form-panel session-history" aria-labelledby="history-title"><p class="section-kicker">Page memory</p><h2 id="history-title">Completed scans</h2><p class="form-intro">This session only. Results are not persisted by the app or stored in localStorage.</p><div class="history-list">${scanHistoryMarkup()}</div></section>
    `);
  }

  function settingsProviderContent() {
    if (state.providerTestBusy) return '<p class="form-status" role="status">Making a real provider request through the configured fallback order…</p>';
    if (state.providerTest) {
      const provider = state.providerTest;
      return `<div class="notice ${provider.verified ? "" : "warning"}">${icon(provider.verified ? "check" : "info")}<span>${provider.verified ? `Successful live request · ${esc(provider.provider || "Provider")} · ${esc(provider.model || "model not reported")}` : esc(provider.message || "The request did not verify a provider.")}</span></div>${state.providerTestError ? errorDetails(state.providerTestError) : ""}`;
    }
    return '<p class="form-status">A configured state is presence only. Run the connection test to make a real small request.</p>';
  }

  function renderSettings() {
    const status = state.status || {};
    const market = state.universe?.market || {};
    const initialSymbols = state.settingsSymbols ?? savedSymbols();
    const details = diagnosticsSnapshot();
    const sourceError = state.statusError || state.universeError;
    setView(`
      ${pageHead("Workspace configuration", "Settings", "Manage local ticker defaults, inspect provider setup, and check what the research service actually supports.")}
      <div class="settings-grid">
        <div class="aside-stack">
          <section class="panel settings-card" aria-labelledby="provider-title">
            <p class="section-kicker">Environment configuration</p><h2 id="provider-title">Provider setup</h2>
            <p class="form-intro">Secret values stay on the server. Required variable names and setup presence only; configured does not mean connected.</p>
            <div class="provider-config">${providerRows(status.providers || [])}</div>
            <div class="form-actions"><button class="button" type="button" id="provider-check" ${state.providerTestBusy ? "disabled" : ""}>Test provider connection</button></div>
            <div id="provider-test-output" aria-live="polite">${settingsProviderContent()}</div>
          </section>
          <section class="panel settings-card" aria-labelledby="universe-title">
            <p class="section-kicker">Browser-only preference</p><h2 id="universe-title">Default ticker universe</h2>
            <p class="form-intro">Stored only in this browser's localStorage. No secrets or market results are saved here.</p>
            <form id="universe-form"><div class="form-field"><label for="default-symbols">Ticker symbols</label><textarea class="text-area" id="default-symbols" name="symbols" aria-describedby="universe-help">${esc(initialSymbols)}</textarea><p id="universe-help" class="field-help">Comma-separated or one symbol per line · up to ${esc(state.universe?.max_symbols || 20)} valid symbols.</p></div>
              <div class="form-actions"><button class="button" type="submit">Save defaults</button><button class="button button-secondary" type="button" id="restore-universe">Restore service defaults</button></div><p class="form-status" id="universe-status" role="status"></p>
            </form>
          </section>
          <section class="panel settings-card" aria-labelledby="supported-title">
            <p class="section-kicker">Market coverage</p><h2 id="supported-title">Supported instruments and sources</h2>
            <div class="data-grid"><div class="data-cell"><span>Scanner source</span><strong>${esc(market.source || "Not reported")}</strong></div><div class="data-cell"><span>Preset symbols</span><strong>${esc((state.universe?.default_symbols || []).join(", ") || "Not reported")}</strong></div><div class="data-cell"><span>Maximum</span><strong>${esc(state.universe?.max_symbols ?? "Not reported")}</strong></div></div>
            <p class="field-label">Supported</p><ul class="result-list">${(market.supported || []).map((item) => `<li>${esc(item)}</li>`).join("") || "<li>Not reported by the service.</li>"}</ul>
            <h3 class="result-section">Unsupported</h3><ul class="result-list">${(market.unsupported || []).map((item) => `<li>${esc(item)}</li>`).join("") || "<li>Not reported by the service.</li>"}</ul>
            <p class="provider-footnote">Preset symbols are not a guarantee of data availability. The scanner reports actual source and partial failures per run.</p>
          </section>
        </div>
        <aside class="aside-stack">
          <section class="panel install-card"><span class="empty-mark">${icon("settings")}</span><div><h3>Install GE Nexus</h3><p>Use the install control when your browser offers the app prompt. The service worker caches the static shell, not API results.</p><p id="install-help" class="muted-detail install-help">${window.matchMedia("(display-mode: standalone)").matches ? "Running as an installed app." : "You can also install from your browser menu."}</p></div></section>
          <section class="panel settings-card"><p class="section-kicker">Diagnostics</p><h2>${esc(status.service || "Service status")}</h2>
            <div class="status-foot"><span>Python interface</span><strong>${esc(status.engine?.interface || "Not reported")}</strong></div>
            <div class="status-foot status-foot-spaced"><span>Engine</span><strong>${status.engine?.available ? "Available" : "Unavailable"}</strong></div>
            <div class="status-foot status-foot-spaced"><span>Scanner</span><strong>${status.capabilities?.market_scanner ? "Available" : "Not reported"}</strong></div>
            <div class="status-foot status-foot-spaced"><span>Broker execution</span><strong>Unavailable</strong></div>
            ${sourceError ? `<div class="notice error">${icon("info")}<span>${esc(sourceError.message || "Some diagnostics could not be loaded.")}<button class="text-button" type="button" data-refresh>Retry status</button></span></div>` : ""}
            <details class="details-block diag-details"><summary>Raw status and universe response</summary><pre>${esc(safeJson(details))}</pre></details>
          </section>
          <div class="notice">${icon("info")}<span><strong>Research only.</strong> No broker execution. Results are held in memory during this page session.</span></div>
        </aside>
      </div>`);
  }

  function render() {
    document.getElementById("current-section").textContent = routeLabels[state.route];
    document.querySelectorAll(".nav-item").forEach((item) => {
      if (item.dataset.route === state.route) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    document.getElementById("offline-banner").classList.toggle("visible", !state.online);
    if (state.route === "home") renderHome();
    else if (state.route === "analysis") renderAnalysis();
    else renderSettings();
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
    state.universeError = null;
    if (!state.status) render();
    setConnection(false, state.online ? "Connecting to service" : "Offline");
    const results = await Promise.allSettled([api(API.status), api(API.universe)]);
    const [statusResult, universeResult] = results;
    if (statusResult.status === "fulfilled") {
      state.status = statusResult.value;
      state.statusError = null;
      setConnection(true, "Local service responding");
    } else {
      state.status = null;
      state.statusError = statusResult.reason;
      setConnection(false, state.online ? "Service unavailable" : "Offline");
    }
    if (universeResult.status === "fulfilled") {
      state.universe = universeResult.value;
      state.universeError = null;
    } else {
      state.universeError = universeResult.reason;
    }
    render();
  }

  async function submitScan(form) {
    const mode = new FormData(form).get("mode");
    const input = form.querySelector('[name="symbols"]').value.trim();
    state.scanInput = input;
    const symbols = input ? formSymbols(input) : [];
    const statusNode = form.querySelector("[data-scan-status]");
    const resultNode = form.querySelector("[data-scan-result]");
    const submit = form.querySelector("[data-scan-submit]");
    const validation = input ? symbolsError(parseSymbols(input)) : "";
    if (validation) {
      statusNode.textContent = validation;
      form.querySelector('[name="symbols"]').focus();
      return;
    }
    submit.disabled = true;
    statusNode.textContent = "Waiting for the Python scanner…";
    resultNode.innerHTML = `<section class="panel result-panel"><div class="result-top"><div><h3>Scan in progress</h3><p>Waiting for the local tool response.</p></div></div><div class="result-content"><div class="skeleton skeleton-h14 skeleton-w50"></div><div class="skeleton skeleton-h14 skeleton-w83 skeleton-gap12"></div></div></section>`;
    state.scanError = null;
    try {
      const payload = { mode };
      if (symbols.length) payload.symbols = symbols;
      const result = await api(API.scanner, { method: "POST", body: JSON.stringify(payload) });
      state.scans.push(result);
      state.latestScan = result;
      state.scanError = null;
      state.scanInput = input;
      statusNode.textContent = `Returned by ${result.tool || "scanner"} · ${result.status || "status not reported"}.`;
      resultNode.innerHTML = scanOutput(result);
      render();
    } catch (error) {
      state.scanError = error;
      statusNode.textContent = "The scan did not return a result.";
      resultNode.innerHTML = errorPanel("Scan could not complete", error, "data-retry-scan");
    } finally {
      if (submit.isConnected) submit.disabled = false;
    }
  }

  async function submitInstrument(form) {
    const symbolInput = form.querySelector('[name="symbol"]');
    const symbol = symbolInput.value.trim().toUpperCase();
    const statusNode = document.getElementById("instrument-status");
    if (!/^[A-Z][A-Z0-9.-]{0,9}$/.test(symbol)) {
      statusNode.textContent = "Enter one valid ticker symbol.";
      symbolInput.focus();
      return;
    }
    state.instrumentSymbol = symbol;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    statusNode.textContent = "Requesting the quote tool…";
    document.getElementById("instrument-result").innerHTML = '<div class="skeleton skeleton-h14 skeleton-w50"></div><div class="skeleton skeleton-h14 skeleton-w83 skeleton-gap12"></div>';
    state.instrumentError = null;
    try {
      state.instrumentResult = await api(API.instrument, { method: "POST", body: JSON.stringify({ symbol }) });
      statusNode.textContent = `Tool response received for ${symbol}.`;
      render();
    } catch (error) {
      state.instrumentError = error;
      statusNode.textContent = "Quote analysis did not return a result.";
      document.getElementById("instrument-result").innerHTML = errorPanel("Quote request failed", error, "data-retry-instrument");
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }

  async function runRisk() {
    const button = document.getElementById("risk-button");
    const status = document.getElementById("risk-status");
    if (!button || button.disabled) return;
    button.disabled = true;
    status.textContent = "Requesting configured risk checks…";
    document.getElementById("risk-result").innerHTML = '<div class="skeleton skeleton-h14 skeleton-w50"></div><div class="skeleton skeleton-h14 skeleton-w83 skeleton-gap12"></div>';
    state.riskError = null;
    try {
      state.riskResult = await api(API.risk, { method: "POST", body: JSON.stringify({}) });
      status.textContent = state.riskResult.status === "unavailable" ? "The risk tool reported unavailable inputs." : "Risk tool response received.";
      render();
    } catch (error) {
      state.riskError = error;
      status.textContent = "Risk checks did not return a result.";
      document.getElementById("risk-result").innerHTML = errorPanel("Risk request failed", error, "data-retry-risk");
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  }

  async function testProvider() {
    if (state.providerTestBusy) return;
    state.providerTestBusy = true;
    state.providerTest = null;
    state.providerTestError = null;
    renderSettings();
    try {
      const result = await api(API.providerCheck, { method: "POST", body: JSON.stringify({}) });
      state.providerTest = { verified: Boolean(result?.verified), provider: result?.provider || null, model: result?.model || null, message: result?.message || null };
    } catch (error) {
      state.providerTest = { verified: false, message: error.message };
      state.providerTestError = error;
    } finally {
      state.providerTestBusy = false;
      if (state.route === "settings") renderSettings();
    }
  }

  function saveUniverse(form) {
    const value = form.querySelector('[name="symbols"]').value;
    const symbols = parseSymbols(value);
    const message = symbolsError(symbols);
    const status = document.getElementById("universe-status");
    if (message || !symbols.length) {
      status.textContent = message || "Enter at least one ticker symbol.";
      form.querySelector('[name="symbols"]').focus();
      return;
    }
    const normalized = symbols.join(", ");
    try {
      localStorage.setItem(STORAGE_KEY, normalized);
      state.settingsSymbols = normalized;
      state.scanInput = "";
      status.textContent = `Saved ${symbols.length} symbols in this browser only.`;
      toast("Default ticker universe saved locally.");
    } catch {
      state.settingsSymbols = normalized;
      state.scanInput = "";
      status.textContent = "Browser storage is unavailable. This preference will last only for this page.";
      toast("Could not save to browser storage.", true);
    }
  }

  function restoreUniverse() {
    try { localStorage.removeItem(STORAGE_KEY); } catch { /* Continue with the service-provided defaults. */ }
    state.settingsSymbols = null;
    state.scanInput = "";
    const field = document.getElementById("default-symbols");
    if (field) field.value = (state.universe?.default_symbols || []).join(", ");
    const status = document.getElementById("universe-status");
    if (status) status.textContent = "Service defaults restored in this editor. Save to store them locally.";
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-route]");
    if (nav) navigate(nav.dataset.route, true);
    const go = event.target.closest("[data-go]");
    if (go) navigate(go.dataset.go, true);
    if (event.target.closest("[data-refresh]")) loadStatus();
    if (event.target.closest("#provider-check")) testProvider();
    if (event.target.closest("#risk-button") || event.target.closest("[data-retry-risk]")) runRisk();
    if (event.target.closest("[data-retry-scan]")) {
      const form = document.querySelector("#home-scan-form, #analysis-scan-form");
      if (form) form.requestSubmit();
    }
    if (event.target.closest("[data-retry-instrument]")) document.getElementById("instrument-form")?.requestSubmit();
    if (event.target.closest("#restore-universe")) restoreUniverse();
  });

  document.addEventListener("submit", (event) => {
    if (event.target.matches("#home-scan-form, #analysis-scan-form")) {
      event.preventDefault();
      submitScan(event.target);
    } else if (event.target.matches("#instrument-form")) {
      event.preventDefault();
      submitInstrument(event.target);
    } else if (event.target.matches("#universe-form")) {
      event.preventDefault();
      saveUniverse(event.target);
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.matches("#home-scan-form [name=symbols], #analysis-scan-form [name=symbols]")) state.scanInput = event.target.value;
  });

  document.querySelector("#refresh-button").addEventListener("click", loadStatus);
  window.addEventListener("online", () => {
    state.online = true;
    document.getElementById("offline-banner").classList.remove("visible");
    setConnection(Boolean(state.status), state.status ? "Local service responding" : "Connecting to service");
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
