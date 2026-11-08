(() => {
  "use strict";

  const API = {
    status: "/api/status",
    universe: "/api/universe",
    scanner: "/api/scanner",
    instrument: "/api/instrument",
    risk: "/api/risk",
    providerCheck: "/api/providers/check",
    researchInterpret: "/api/research/interpret"
  };
  const routeLabels = { home: "Home", analysis: "Analysis", settings: "Settings" };
  const STORAGE_KEY = "ge-nexus-default-symbols";
  const HISTORY_STORAGE_KEY = "ge-nexus-research-history";
  const HISTORY_LIMIT = GENexusClientState.HISTORY_LIMIT;
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
    latestResearch: null,
    scanInput: "",
    scanError: null,
    scanResult: null,
    scanBusy: false,
    scanController: null,
    scanSymbolCount: 0,
    scanMessage: "",
    instrumentSymbol: "",
    instrumentResult: null,
    instrumentError: null,
    riskResult: null,
    riskError: null,
    providerTest: null,
    providerTestError: null,
    providerTestBusy: false,
    settingsSymbols: null,
    settingsError: null,
    aiInterpretations: {},
    interpretingResearch: {}
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
    const timeoutMs = options.timeoutMs || 30000;
    const { timeoutMs: _ignoredTimeout, signal: externalSignal, ...fetchOptions } = options;
    const controller = new AbortController();
    let timedOut = false;
    let timeoutId = null;
    const abortFromCaller = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener("abort", abortFromCaller, { once: true });
    }
    timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    let response;
    try {
      response = await fetch(url, {
        ...fetchOptions,
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
      });
    } catch (cause) {
      window.clearTimeout(timeoutId);
      externalSignal?.removeEventListener("abort", abortFromCaller);
      if (controller.signal.aborted) {
        const error = new Error(timedOut
          ? `The request timed out after ${Math.ceil(timeoutMs / 1000)} seconds.`
          : "The request was cancelled.");
        error.name = timedOut ? "TimeoutError" : "AbortError";
        error.url = url;
        throw error;
      }
      const error = new Error("The local research service could not be reached.");
      error.cause = cause;
      error.url = url;
      throw error;
    } finally {
      window.clearTimeout(timeoutId);
      externalSignal?.removeEventListener("abort", abortFromCaller);
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
    if (!data || typeof data !== "object") {
      const error = new Error("The service returned an unreadable response.");
      error.status = response.status;
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
    sideCopy.textContent = connected ? "Market research is available." : "Live research needs the local service.";
  }

  function savedSymbols() {
    if (state.settingsSymbols) return state.settingsSymbols;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved !== null) return saved;
    } catch { /* Storage is optional; keep the in-memory defaults. */ }
    return (state.universe?.default_symbols || []).join(", ");
  }

  function loadResearchHistory() {
    try {
      const stored = JSON.parse(localStorage.getItem(HISTORY_STORAGE_KEY) || "[]");
      const history = GENexusClientState.expireResearchHistory(stored);
      localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(history));
      return history.filter((item) => item.data !== undefined);
    } catch {
      return [];
    }
  }

  function persistResearchResult(result, kind) {
    if (!result || result.status !== "completed") return false;
    const savedResult = {
      ...result,
      history_kind: kind,
      saved_at: new Date().toISOString()
    };
    // Server-side Q&A evidence is short-lived. Do not persist its handle where
    // it could appear usable after the server has discarded the context.
    delete savedResult.research_id;
    state.scans.push(savedResult);
    state.scans = GENexusClientState.expireResearchHistory(state.scans);
    try {
      let serialized = JSON.stringify(state.scans);
      while (serialized.length > 500000 && state.scans.length > 1) {
        state.scans.shift();
        serialized = JSON.stringify(state.scans);
      }
      localStorage.setItem(HISTORY_STORAGE_KEY, serialized);
      return true;
    } catch {
      return false;
    }
  }

  function parseSymbols(value) {
    return value.split(/[,\n\r]+/).map((symbol) => symbol.trim().toUpperCase()).filter(Boolean);
  }

  function symbolsError(symbols) {
    const max = Number(state.universe?.max_symbols) || 20;
    if (symbols.length > max) return `Use no more than ${max} ticker symbols.`;
    if (symbols.some((symbol) => !/^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$/.test(symbol))) return "Use valid ticker symbols separated by commas or new lines.";
    if (new Set(symbols).size !== symbols.length) return "Remove duplicate ticker symbols.";
    return "";
  }

  function formSymbols(value) {
    const symbols = parseSymbols(value);
    return symbolsError(symbols) ? null : symbols;
  }

  function providerStatus(provider) {
    const name = String(provider.name || "").toLowerCase();
    if (state.providerTest?.verified &&
        String(state.providerTest.provider || "").toLowerCase() === name) {
      return { text: "Ready", className: "verified" };
    }
    const last = provider.last_connection_result;
    if (last?.status === "verified") return { text: "Provider verified", className: "verified" };
    if (last?.status === "failed") return { text: "Last check failed", className: "failed" };
    if (provider.state === "misconfigured") return { text: "Misconfigured", className: "failed" };
    if (provider.state === "unavailable" || !provider.key_configured) {
      return { text: "Unavailable · key missing", className: "needs" };
    }
    const tested = (state.providerTest?.attempts || []).find(
      (attempt) => String(attempt.provider || "").toLowerCase() === name
    );
    if (tested) {
      const states = {
        missing_key: ["Unavailable", "needs"],
        authentication: ["Authentication failed", "failed"],
        model_unavailable: ["Model unavailable", "failed"],
        invalid_configuration: ["Connection failed", "failed"],
        invalid_configuration_or_request: ["Model unavailable", "failed"],
        rate_limited: ["Temporarily unavailable", "needs"],
        server_error: ["Temporarily unavailable", "needs"],
        timeout: ["Temporarily unavailable", "needs"],
        connection_error: ["Temporarily unavailable", "needs"],
        deadline_exceeded: ["Temporarily unavailable", "needs"]
      };
      const [text, className] = states[tested.category] || ["Connection failed", "failed"];
      return { text, className };
    }
    return { text: "Ready to test", className: "" };
  }

  function providerRows(providers = []) {
    if (!providers.length) return '<div class="empty-state"><p>Provider configuration is not available until service status responds.</p></div>';
    return providers.map((provider) => {
      const setup = providerStatus(provider);
      const endpoint = provider.endpoint || provider.base_url || provider.api_base || null;
      const last = provider.last_connection_result;
      const lastResult = last
        ? `Last check: ${last.status.replaceAll("_", " ")}${last.checked_at ? ` · ${shortDate(last.checked_at)}` : ""}${last.category ? ` (${last.category.replaceAll("_", " ")})` : ""}`
        : "Not tested yet · Settings do not make a provider request";
      return `<article class="config-row">
        <div><strong>${esc(provider.name || "Provider")}</strong><small>${provider.key_configured ? "API key present" : "API key missing"} · Model ${esc(provider.selected_model || "not configured")} (${provider.model_source === "override" ? "override" : "default"})</small><small>${esc(lastResult)}</small></div>
        <span class="badge ${setup.className}">${esc(setup.text)}</span>
        <details class="details-block provider-details"><summary>Technical details</summary><pre>${esc(safeJson({
          key_variable: provider.key_env,
          model_variable: provider.model_env,
          model_source: provider.model_source,
          endpoint_variable: provider.endpoint_env,
          endpoint: endpoint
        }))}</pre></details>
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
    const researchId = result?.research_id;
    if (!researchId) return "";
    const response = state.aiInterpretations[researchId];
    const busy = Boolean(state.interpretingResearch[researchId]);
    const button = `<button class="button button-secondary" type="button" data-interpret-research data-research-id="${esc(researchId)}" ${busy ? "disabled" : ""}>${busy ? "Interpreting…" : "Interpret with AI"}</button>`;
    if (!response) {
      return `<section class="panel result-panel interpretation-panel"><div class="result-top"><div><h3>Optional AI interpretation</h3><p>Runs only when you request it. Python results remain unchanged.</p></div><span class="provider-tag">AI is optional</span></div><div class="result-content">${button}</div></section>`;
    }
    if (response.ai_status !== "complete") {
      const attempts = Array.isArray(response.attempts) ? response.attempts : [];
      return `<section class="panel result-panel interpretation-panel"><div class="result-top"><div><h3>Optional AI interpretation</h3><p>The original Python result is unchanged.</p></div></div><div class="result-content"><div class="notice warning">${icon("info")}<span>${esc(response.message || "AI interpretation is unavailable.")}</span></div>${attempts.length ? rawDetails("Provider diagnostics", attempts) : ""}${button}</div></section>`;
    }
    const resultData = response.interpretation || {};
    const value = (field) => {
      const item = resultData[field];
      return typeof item === "number" ? formatPrice(item) : esc(item ?? "Not calculated");
    };
    const evidence = Array.isArray(resultData.evidence) ? resultData.evidence : [];
    const warnings = Array.isArray(resultData.data_warning) ? resultData.data_warning : [];
    return `<section class="panel result-panel interpretation-panel">
      <div class="result-top"><div><h3>AI interpretation</h3><p>Returned by ${esc(resultData.provider || "provider not reported")}</p></div><span class="provider-tag">Interpretation only</span></div>
      <div class="result-content">
        <div class="summary-grid">
          <div class="summary-cell"><span>Research decision</span><strong>${value("decision")}</strong></div>
          <div class="summary-cell"><span>Entry</span><strong>${value("entry")}</strong></div>
          <div class="summary-cell"><span>Stop loss</span><strong>${value("stop_loss")}</strong></div>
          <div class="summary-cell"><span>Take profit</span><strong>${value("take_profit")}</strong></div>
          <div class="summary-cell"><span>Invalidation</span><strong>${value("invalidation")}</strong></div>
          <div class="summary-cell"><span>Confidence</span><strong>${value("confidence")}</strong></div>
        </div>
        <h4 class="result-section">Reason</h4><p class="result-summary">${esc(resultData.reason || "No interpretation details were returned.")}</p>
        ${evidence.length ? `<h4 class="result-section">Python evidence</h4><ul class="result-list">${evidence.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}
        ${warnings.length ? `<h4 class="result-section">Data warning</h4><ul class="result-list">${warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : ""}
        ${rawDetails("Provider diagnostics", resultData.attempts || [])}
        ${button}
      </div>
    </section>`;
  }

  function formatPrice(value) {
    const number = Number(value);
    return Number.isFinite(number)
      ? `$${number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : esc(value);
  }

  function findingCards(result) {
    const findings = Array.isArray(result?.research_findings) ? result.research_findings : [];
    if (!findings.length) {
      const stateName = result?.scan_summary?.status;
      const message = stateName === "data_unavailable"
        ? "The scanner did not have enough usable data to analyze any instruments. No findings were generated."
        : stateName === "failed"
          ? "The scanner could not complete this request. No findings were generated."
          : "The completed scan found no qualifying patterns for these symbols.";
      return `<div class="empty-state"><span class="empty-mark">${icon("info")}</span><p>${esc(message)}</p></div>`;
    }
    return `<div class="finding-grid">${findings.map((finding) => {
      const setups = (finding.setups || []).map((setup) => `<span class="finding-tag">${esc(setup.label)}</span>`).join("");
      const facts = [];
      if (finding.rsi_14 != null) facts.push(["RSI 14", finding.rsi_14]);
      if (finding.volume != null) facts.push(["Volume", Number(finding.volume).toLocaleString()]);
      if (finding.bar_time) facts.push(["Bar closed", finding.bar_time]);
      if (finding.opening_range_low != null) facts.push(["Range low", formatPrice(finding.opening_range_low)]);
      if (finding.opening_range_high != null) facts.push(["Range high", formatPrice(finding.opening_range_high)]);
      if (finding.percent_above_range != null) facts.push(["Above range", `${finding.percent_above_range}%`]);
      return `<article class="finding-card">
        <div class="finding-top"><div><p class="section-kicker">Research finding</p><h4>${esc(finding.symbol)}</h4></div><div class="finding-tags">${setups}</div></div>
        ${finding.price != null ? `<div class="finding-price">${formatPrice(finding.price)}<small>Reference price</small></div>` : ""}
        ${finding.price_as_of ? `<p class="finding-meta">${result.data?.mode === "orb" ? "Observed" : "Price date"} · ${esc(finding.price_as_of)}</p>` : ""}
        ${facts.length ? `<div class="finding-facts">${facts.map(([label, value]) => `<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>` : ""}
        ${finding.setups?.length ? `<ul class="result-list finding-explanations">${finding.setups.map((setup) => `<li>${esc(setup.explanation)}</li>`).join("")}</ul>` : ""}
        <p class="finding-source">${esc(finding.price_source || "Data source not reported")}</p>
      </article>`;
    }).join("")}</div>`;
  }

  function symbolResultsMarkup(result) {
    const rows = Array.isArray(result?.symbol_results) ? result.symbol_results : [];
    if (!rows.length) return '<div class="empty-state"><p>No per-symbol details were returned.</p></div>';
    const bySymbol = new Map(rows.map((row) => [row.symbol, row]));
    const ranked = (result?.research_findings || [])
      .map((finding) => bySymbol.get(finding.symbol))
      .filter(Boolean);
    const rankedSymbols = new Set(ranked.map((row) => row.symbol));
    const ordered = [...ranked, ...rows.filter((row) => !rankedSymbols.has(row.symbol))];
    const labels = {
      completed: ["Research finding", "verified"],
      no_qualifying_result: ["WAIT · no qualifying setup", ""],
      no_market_data: ["NO RESULT · data unavailable", "failed"],
      unsupported_symbol: ["NO RESULT · unsupported symbol", "failed"],
      temporarily_unavailable: ["NO RESULT · temporarily unavailable", "failed"],
      analysis_error: ["NO RESULT · analysis failed", "failed"],
      analysis_incomplete: ["Not fully checked", "needs"]
    };
    return `<div class="finding-grid">${ordered.map((row) => {
      const [label, badgeClass] = labels[row.analysis_status] || ["NO RESULT", "failed"];
      const finding = row.tool_result;
      const displayFinding = (result?.research_findings || []).find((item) => item.symbol === row.symbol);
      const facts = [
        row.market_price != null ? ["Latest available price", formatPrice(row.market_price)] : null,
        row.entry_price != null ? ["Entry", formatPrice(row.entry_price)] : null,
        row.stop_loss != null ? ["Stop", formatPrice(row.stop_loss)] : null,
        row.take_profit != null ? ["Target", formatPrice(row.take_profit)] : null,
        row.timeframe ? ["Timeframe", row.timeframe] : null,
        row.price_as_of || row.analysis_timestamp
          ? ["Data time", row.price_as_of || row.analysis_timestamp] : null
      ].filter(Boolean);
      const setups = (displayFinding?.setups || []).map((setup) => setup.label);
      return `<article class="finding-card">
        <div class="finding-top"><div><p class="section-kicker">Instrument</p><h4>${esc(row.symbol)}</h4></div><span class="badge ${badgeClass}">${esc(label)}</span></div>
        ${row.assessment_note ? `<p class="muted-detail">${esc(row.assessment_note)}</p>` : ""}
        ${setups.length ? `<div class="finding-tags">${setups.map((setup) => `<span class="finding-tag">${esc(setup)}</span>`).join("")}</div>` : ""}
        ${facts.length ? `<div class="finding-facts">${facts.map(([name, value]) => `<div><span>${esc(name)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>` : ""}
        ${(row.reasons || []).length ? `<ul class="result-list finding-explanations">${row.reasons.map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul>` : ""}
        ${row.warnings?.length ? `<p class="finding-source">${esc(row.warnings.join(" · "))}</p>` : ""}
      </article>`;
    }).join("")}</div>`;
  }

  function scanOutput(result, index = "") {
    const data = result?.data || {};
    const summary = result?.scan_summary || {};
    const modeLabel = data.mode === "orb" ? "Opening-range scan" : data.mode === "daily" ? "Daily-close scan" : "Market scan";
    const status = summary.status || result?.status || "unknown";
    const title = `${modeLabel}${index ? ` · ${index}` : ""}`;
    const dataTimestamp = summary.data_as_of || data.as_of;
    const meta = [
      summary.completed_at ? `Completed ${shortDate(summary.completed_at)}` : "",
      dataTimestamp ? `Data through ${dataTimestamp} · ${GENexusClientState.ageLabel(dataTimestamp)}` : "Data timestamp unavailable"
    ].filter(Boolean).join(" · ");
    const metrics = [
      ["Requested", summary.instruments_checked],
      ["Completed", summary.instruments_analyzed],
      ["Partial", summary.instruments_partial],
      ["Failed", summary.instruments_failed],
      ["Findings", summary.candidate_count],
      ["Data issues", summary.data_errors]
    ];
    if (summary.unconfirmed_candidates) metrics.push(["Not confirmed", summary.unconfirmed_candidates]);
    const explanations = (result?.research_explanation || []).map((item) => `<li>${esc(item)}</li>`).join("");
    return `<section class="panel result-panel">
      <div class="result-top"><div><h3>${esc(title)}</h3><p>${esc(meta || "Scan result")}</p></div><span class="badge ${["completed", "no_findings"].includes(status) ? "verified" : ["failed", "data_unavailable"].includes(status) ? "failed" : ""}">${esc(summary.label || status.replaceAll("_", " "))}</span></div>
      <div class="result-content">
        <p class="result-callout">${esc(summary.data_source || "Market-data source not reported")}${summary.data_as_of ? ` · Data as of ${esc(summary.data_as_of)}` : ""}</p>
        <div class="summary-grid">${metrics.map(([label, value]) => `<div class="summary-cell"><span>${esc(label)}</span><strong>${esc(value ?? 0)}</strong></div>`).join("")}</div>
        ${summary.status === "partial" ? `<div class="notice warning">${icon("info")}<span>Some symbols could not be fully checked${summary.unconfirmed_candidates ? `; ${esc(summary.unconfirmed_candidates)} possible candidates were not fully confirmed` : ""}. Successful results are preserved.</span></div>` : ""}
        ${summary.data_error_symbols?.length ? `<div class="notice warning">${icon("info")}<span>No data was returned for: ${esc(summary.data_error_symbols.join(", "))}. The scanner does not assume these symbols are supported or fabricate a result.</span></div>` : ""}
        ${summary.invalid_symbols?.length ? `<div class="notice warning">${icon("info")}<span>Invalid ticker entries were skipped: ${esc(summary.invalid_symbols.join(", "))}. Valid symbols were still scanned.</span></div>` : ""}
        ${summary.duplicates_removed?.length ? `<p class="muted-detail">Duplicate entries removed: ${esc(summary.duplicates_removed.join(", "))}.</p>` : ""}
        ${result.scan_mode_reason ? `<p class="muted-detail">${esc(result.scan_mode_reason)}</p>` : ""}
        <h4 class="result-section">Symbol-by-symbol results</h4>${symbolResultsMarkup(result)}
        ${summary.candidate_count ? `<h4 class="result-section">Qualifying findings · scanner order</h4>${findingCards(result)}` : ""}
        <h4 class="result-section">What the scan checks</h4><ul class="result-list">${explanations || "<li>The scanner did not return an explanation.</li>"}</ul>
        ${rawDetails("Technical details", result)}
      </div>
    </section>${interpretationPanel(result)}`;
  }

  function errorPanel(title, error, retryAttr) {
    return `<section class="panel result-panel"><div class="result-top"><div><h3>${esc(title)}</h3><p>No result was returned.</p></div></div><div class="result-content"><div class="notice error">${icon("info")}<span>${esc(error?.message || "The request could not be completed.")}</span></div>${retryAttr ? `<button class="button button-secondary" type="button" ${retryAttr}>Try again</button>` : ""}<details class="details-block"><summary>Technical details</summary>${errorDetails(error)}</details></div></section>`;
  }

  function scanForm(id, title) {
    const symbols = state.scanInput || savedSymbols();
    const progress = state.scanBusy
      ? `<div class="scan-progress-wrap"><progress aria-label="Market scan in progress"></progress><span>Analyzing ${esc(state.scanSymbolCount)} instruments in one rate-limited scanner batch. Per-symbol progress is available when the batch returns.</span></div>`
      : "";
    return `<section class="panel scan-controls" aria-labelledby="${id}-heading">
      <p class="section-kicker">Market research</p><h2 id="${id}-heading">${esc(title)}</h2>
      <p class="form-intro">GE Nexus picks the right scan for the current market session.</p>
      <form id="${id}">
        <input type="hidden" name="mode" value="auto">
        <div class="scan-inline"><div class="form-field"><label for="${id}-symbols">Tickers</label><input class="text-input" id="${id}-symbols" name="symbols" value="${esc(symbols)}" autocomplete="off" placeholder="Use your saved defaults" aria-describedby="${id}-help"><p class="field-help" id="${id}-help">Comma-separated · up to ${esc(state.universe?.max_symbols || 20)} U.S.-listed equity symbols.</p></div><button class="button" type="submit" data-scan-submit ${state.scanBusy ? "disabled" : ""}>${icon("scan")} ${state.scanBusy ? "Scanning…" : "Start Market Scan"}</button><button class="button button-secondary" type="button" data-cancel-scan ${state.scanBusy ? "" : "hidden"}>Cancel scan</button></div>
        ${progress}
        <p class="form-status" data-scan-status role="status" aria-live="polite">${esc(state.scanMessage)}</p>
      </form>
      <div data-scan-result aria-live="polite">${state.scanBusy ? '<div class="skeleton skeleton-h14 skeleton-w83 skeleton-gap12"></div>' : state.scanResult ? scanOutput(state.scanResult) : state.scanError ? errorPanel("Scan could not complete", state.scanError, "data-retry-scan") : ""}</div>
    </section>`;
  }

  function renderHome() {
    if (!state.status && !state.statusError) {
      setView(`${pageHead("GE Nexus", "Market research, made clear.", "Start with market evidence. AI explanations are optional.")}<div class="home-grid"><section class="hero-panel"><div class="hero-content"><p class="hero-overline">GE Nexus research desk</p><h2>Start with the market evidence.</h2><p>Research findings are based on available market data. GE Nexus does not place trades.</p><div class="hero-actions"><button class="button" type="button" data-start-market-scan>${icon("scan")} Start Market Scan</button></div></div></section><section class="panel system-card"><div class="skeleton skeleton-h14 skeleton-w40"></div><div class="skeleton skeleton-h14 skeleton-w70 skeleton-gap12"></div></section></div><div class="home-lower">${scanForm("home-scan-form", "Choose your tickers")}<section class="panel"><div class="panel-heading"><div><p class="section-kicker">Latest research</p><h2>Recent results</h2></div></div><div class="panel-body"><div class="empty-state"><p>Checking the research service.</p></div></div></section></div>`, true);
      return;
    }
    const status = state.status || {};
    const engineOK = Boolean(status.engine?.available);
    const latest = state.latestResearch || state.scans[state.scans.length - 1];
    const latestContent = latest
      ? (latest.data?.mode
        ? scanOutput(latest, "latest")
        : returnedFactsPanel("Latest instrument research", latest))
      : state.scanError
      ? errorPanel("Latest scan", state.scanError, 'data-go="analysis"')
      : `<div class="empty-state"><span class="empty-mark">${icon("info")}</span><p>Your completed research will appear here and remain in this browser.</p></div>`;
    setView(`
      ${pageHead("GE Nexus", "Market research, made clear.", "Scan supported stocks and review the patterns in their data.", `<button class="button button-secondary" type="button" data-refresh>${icon("reload")} Refresh</button>`)}
      <div class="home-grid">
        <section class="hero-panel"><div class="hero-content"><p class="hero-overline">GE Nexus research desk</p><h2>Start with the market evidence.</h2><p>Research findings are based on available market data. GE Nexus does not place trades.</p>
          <div class="hero-actions"><button class="button" type="button" data-start-market-scan>${icon("scan")} Start Market Scan</button></div>
        </div></section>
        <section class="panel system-card"><div class="panel-heading"><div><p class="section-kicker">Service status</p><h2>${esc(status.service || "GE Nexus")}</h2></div><span class="inline-state"><span class="status-dot ${engineOK ? "" : "warn"}"></span>${engineOK ? "Ready" : "Unavailable"}</span></div>
          <div class="status-main">${engineOK ? "Research service ready" : "Research service unavailable"}</div>
          <p class="status-description">Market data may be delayed</p>
          <div class="status-rule"></div><div class="status-foot"><span>Asset coverage</span><strong>U.S. equities</strong></div>
          <div class="status-foot status-foot-spaced"><span>Optional AI</span><strong>${(status.providers || []).some((provider) => provider.configured) ? "Configured" : "Unavailable"}</strong></div>
          ${state.statusError ? `<div class="notice error">${icon("info")}<span>${esc(state.statusError.message || state.statusError)}${errorDetails(state.statusError)}</span></div>` : ""}
        </section>
      </div>
      <div class="home-lower">
        ${scanForm("home-scan-form", "Choose your tickers")}
        <section class="panel" aria-labelledby="latest-title"><div class="panel-heading"><div><p class="section-kicker">Browser history</p><h2 id="latest-title">Latest research</h2><p>Saved in this browser only.</p></div></div><div class="panel-body">${latestContent}</div></section>
      </div>`);
  }

  function returnedFactsPanel(title, result, error, options = {}) {
    if (error) return errorPanel(title, error, options.retryAttr);
    if (!result) return `<div class="empty-state"><span class="empty-mark">${icon("info")}</span><p>${esc(options.empty || "No tool result yet.")}</p></div>`;
    const body = result.data !== undefined ? result.data : result;
    const meta = [result.symbol || result.tool, result.status === "completed" ? "Completed" : "Unavailable"].filter(Boolean).join(" · ");
    let checks = null;
    if (options.risk) {
      const source = result.data && typeof result.data === "object" ? result.data : result;
      checks = source.checks || source.risk_checks || source.results || null;
    }
    const checksMarkup = Array.isArray(checks)
      ? `<h4 class="result-section">Risk checks returned</h4><ul class="result-list">${checks.map((check) => `<li>${esc(typeof check === "string" ? check : [check?.name, check?.rule, check?.message, check?.status, check?.result].filter(Boolean).join(" · ") || "A risk check was returned.")}</li>`).join("")}</ul>`
      : "";
    const bodyText = typeof body === "string"
      ? body
      : options.risk && body && typeof body === "object"
        ? `${Array.isArray(checks) ? `${checks.length} checks returned. ` : ""}${body.block != null ? `${body.block} blocked. ` : ""}${body.pass != null ? `${body.pass} passed.` : ""}`.trim() || "The risk check returned a result."
        : "Instrument data was returned.";
    return `<section class="panel result-panel"><div class="result-top"><div><h3>${esc(title)}</h3><p>${esc(meta || "Tool response received")}</p></div><span class="badge ${result.status === "completed" ? "verified" : ""}">${esc(result.status || "returned")}</span></div>
      <div class="result-content"><div class="result-callout">Based on returned data. AI interpretation is optional.</div><p class="result-summary">${esc(bodyText)}</p>${checksMarkup}${rawDetails("Technical details", result)}</div>
    </section>${interpretationPanel(result)}`;
  }

  function scanHistoryMarkup() {
    if (!state.scans.length) return '<div class="empty-state"><p>Completed scans and quote results are saved only in this browser.</p></div>';
    return [...state.scans].reverse().map((result, index) => {
      const isScan = Boolean(result.data?.mode);
      const label = isScan ? (result.data.mode === "orb" ? "Opening range" : "Daily close") : `Quote · ${result.symbol || "Instrument"}`;
      const dataDate = result.scan_summary?.data_as_of || result.data?.as_of;
      const savedAge = GENexusClientState.ageLabel(result.saved_at || result.data?.created_at);
      return `<details class="history-item"><summary><span>${esc(label)} · ${esc(result.scan_summary?.label || "Completed")}</span><small>${esc(dataDate ? `Data ${dataDate} · ${GENexusClientState.ageLabel(dataDate)}` : `Saved ${savedAge}`)}</small></summary>${isScan ? scanOutput(result) : returnedFactsPanel("Instrument quote", result, null)}</details>`;
    }).join("");
  }

  function renderAnalysis() {
    const status = state.status || {};
    setView(`
      ${pageHead("Research workspace", "Analysis", "Run a market scan, review instrument data, and check risk only when configured inputs exist.")}
      ${scanForm("analysis-scan-form", "Start a market scan")}
      <div class="content-grid analysis-grid">
        <section class="panel form-panel" aria-labelledby="instrument-title"><p class="section-kicker">Instrument research</p><h2 id="instrument-title">Analyze one ticker</h2><p class="form-intro">Review available price and quote data for a supported stock.</p>
          <form id="instrument-form" class="tool-form"><div class="form-field"><label for="instrument-symbol">Ticker symbol</label><input class="text-input" id="instrument-symbol" name="symbol" maxlength="7" value="${esc(state.instrumentSymbol)}" autocomplete="off" placeholder="e.g. MSFT" required></div><button class="button" type="submit">${icon("scan")} Get quote</button></form>
          <p class="form-status" id="instrument-status" role="status"></p>
          <div id="instrument-result">${returnedFactsPanel("Instrument quote", state.instrumentResult, state.instrumentError, { empty: "Enter a ticker to request actual quote-tool output.", retryAttr: "data-retry-instrument" })}</div>
        </section>
        <section class="panel form-panel" aria-labelledby="risk-title"><p class="section-kicker">Risk review</p><h2 id="risk-title">Check configured risk limits</h2><p class="form-intro">Checks your saved proposed order against risk limits when those details are available. It never places an order.</p>
          <div class="data-grid"><div class="data-cell"><span>Required inputs</span><strong>${status.risk_inputs_available ? "Available" : "Not available"}</strong></div></div>
          <button class="button button-secondary" type="button" id="risk-button" ${status.risk_inputs_available ? "" : "disabled"}>${icon("shield")} Run risk checks</button>
          <p class="form-status" id="risk-status" role="status">${status.risk_inputs_available ? "" : "Risk checks are unavailable because required details are not available."}</p>
          <div id="risk-result">${returnedFactsPanel("Risk check result", state.riskResult, state.riskError, { risk: true, empty: "Risk output will appear here only after the configured tool returns a result.", retryAttr: "data-retry-risk" })}</div>
        </section>
      </div>
      <section class="panel form-panel session-history" aria-labelledby="history-title"><p class="section-kicker">Saved in this browser</p><h2 id="history-title">Research history</h2><p class="form-intro">Your latest eight completed scans and quotes are stored in this browser only. They are not uploaded as an archive.</p><div class="history-list">${scanHistoryMarkup()}</div></section>
    `);
  }

  function settingsProviderContent() {
    if (state.providerTestBusy) return '<p class="form-status" role="status">Checking the configured providers in order…</p>';
    if (state.providerTest) {
      const provider = state.providerTest;
      const attempts = provider.attempts || [];
      const failures = attempts.length
        ? `<details class="details-block"><summary>Provider test details</summary><ul class="result-list">${attempts.map((attempt) => `<li>${esc(attempt.provider)} · ${esc(attempt.category.replaceAll("_", " "))}</li>`).join("")}</ul></details>`
        : "";
      return `<div class="notice ${provider.verified ? "" : "warning"}">${icon(provider.verified ? "check" : "info")}<span>${provider.verified ? `Connection verified · ${esc(provider.provider || "Provider")} · ${esc(provider.model || "model not reported")}` : esc(provider.message || "The request did not verify a provider.")}</span></div>${failures}${state.providerTestError ? errorDetails(state.providerTestError) : ""}`;
    }
    return '<p class="form-status">A real server-side request verifies connectivity. Secret values are never shown here.</p>';
  }

  function renderSettings() {
    const status = state.status || {};
    const market = state.universe?.market || {};
    const initialSymbols = state.settingsSymbols ?? savedSymbols();
    const details = diagnosticsSnapshot();
    const sourceError = state.statusError || state.universeError;
    setView(`
      ${pageHead("Preferences", "Settings", "Manage your ticker defaults and review optional AI and app preferences.")}
      <div class="settings-grid">
        <div class="aside-stack">
          <section class="panel settings-card" aria-labelledby="provider-title">
            <p class="section-kicker">Optional AI</p><h2 id="provider-title">AI providers</h2>
            <p class="form-intro">AI explains research findings. Market scanning works without it.</p>
            <div class="provider-config">${providerRows(status.providers || [])}</div>
            <div class="form-actions"><button class="button" type="button" id="provider-check" ${state.providerTestBusy ? "disabled" : ""}>Test connection</button></div>
            <div id="provider-test-output" aria-live="polite">${settingsProviderContent()}</div>
          </section>
          <section class="panel settings-card" aria-labelledby="universe-title">
            <p class="section-kicker">Browser-only preference</p><h2 id="universe-title">Default tickers</h2>
            <p class="form-intro">Saved in this browser. The preset is not the scanner's full supported universe.</p>
            <form id="universe-form"><div class="form-field"><label for="default-symbols">Ticker symbols</label><textarea class="text-area" id="default-symbols" name="symbols" aria-describedby="universe-help">${esc(initialSymbols)}</textarea><p id="universe-help" class="field-help">Comma-separated or one symbol per line · up to ${esc(state.universe?.max_symbols || 20)} valid symbols.</p></div>
              <div class="form-actions"><button class="button" type="submit">Save defaults</button><button class="button button-secondary" type="button" id="restore-universe">Restore service defaults</button></div><p class="form-status" id="universe-status" role="status"></p>
            </form>
          </section>
          <section class="panel settings-card" aria-labelledby="supported-title">
            <p class="section-kicker">Coverage</p><h2 id="supported-title">U.S.-listed equities</h2>
            <p class="result-summary">The scanner supports common shares, ordinary shares, and ADRs. Forex, crypto, and commodities are not supported.</p>
            <p class="provider-footnote">Yahoo Finance chart data may be delayed or unavailable. A ticker is not guaranteed to return data.</p>
          </section>
        </div>
        <aside class="aside-stack">
          <section class="panel install-card"><span class="empty-mark">${icon("settings")}</span><div><h3>Install GE Nexus</h3><p>Install from the browser menu when available. The app shell works offline; research needs a connection.</p><p id="install-help" class="muted-detail install-help">${window.matchMedia("(display-mode: standalone)").matches ? "Running as an installed app." : "Available from your browser menu when supported."}</p></div></section>
          <section class="panel settings-card"><p class="section-kicker">App status</p><h2>${status.engine?.available ? "Research service ready" : "Service status unavailable"}</h2>
            <p class="result-summary">Research history is stored in this browser only. No database is configured.</p>
            ${sourceError ? `<div class="notice error">${icon("info")}<span>${esc(sourceError.message || "Some settings could not be loaded.")}<button class="text-button" type="button" data-refresh>Retry</button></span></div>` : ""}
            <details class="details-block diag-details"><summary>Technical diagnostics</summary><pre>${esc(safeJson(details))}</pre></details>
          </section>
          <div class="notice">${icon("info")}<span><strong>Research only.</strong> GE Nexus does not place or execute orders.</span></div>
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
      const engineReady = Boolean(
        state.status?.backend?.healthy && state.status?.engine?.available
      );
      setConnection(
        engineReady,
        engineReady ? "Research engine ready" : "Research engine unavailable"
      );
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
    if (state.scanBusy) return;
    const mode = new FormData(form).get("mode");
    const input = form.querySelector('[name="symbols"]').value.trim();
    state.scanInput = input;
    const max = Number(state.universe?.max_symbols) || 20;
    const parsed = input
      ? GENexusClientState.parseScannerSymbols(input, max)
      : { symbols: [], tooMany: false, duplicates: [] };
    if (parsed.tooMany) {
      state.scanMessage = `Use no more than ${max} ticker entries.`;
      render();
      form.querySelector('[name="symbols"]').focus();
      return;
    }
    const effectiveSymbols = parsed.symbols.length
      ? parsed.symbols
      : (state.universe?.default_symbols || ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA"]);
    state.scanBusy = true;
    state.scanSymbolCount = effectiveSymbols.length;
    state.scanController = new AbortController();
    state.scanMessage = `Submitting ${effectiveSymbols.length} symbols to the existing scanner${parsed.duplicates?.length ? ` · Removed duplicates: ${parsed.duplicates.join(", ")}` : ""}.`;
    state.scanError = null;
    state.scanResult = null;
    render();
    try {
      const payload = { mode, symbols: effectiveSymbols };
      const result = await api(API.scanner, {
        method: "POST",
        body: JSON.stringify(payload),
        timeoutMs: 120000,
        signal: state.scanController.signal
      });
      const stored = persistResearchResult(result, "scan");
      state.latestResearch = result;
      state.scanError = null;
      state.scanResult = result;
      state.scanInput = input;
      state.scanMessage = result.status !== "completed"
        ? (result.scan_summary?.label || "The scan could not complete.")
        : `${result.scan_summary?.label || "Scan returned"}${stored ? " · Saved in this browser." : " · Browser storage was unavailable."}`;
    } catch (error) {
      state.scanError = error;
      state.scanMessage = error.name === "AbortError"
        ? "Scan cancelled. No partial result was returned."
        : error.name === "TimeoutError"
          ? "The scan timed out. Try fewer symbols or retry later."
          : "The scan did not return a result.";
    } finally {
      state.scanBusy = false;
      state.scanController = null;
      render();
    }
  }

  async function submitInstrument(form) {
    const symbolInput = form.querySelector('[name="symbol"]');
    const symbol = symbolInput.value.trim().toUpperCase();
    const statusNode = document.getElementById("instrument-status");
    if (!/^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$/.test(symbol)) {
      statusNode.textContent = "Enter one valid ticker symbol.";
      symbolInput.focus();
      return;
    }
    state.instrumentSymbol = symbol;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    statusNode.textContent = "Getting available quote data…";
    document.getElementById("instrument-result").innerHTML = '<div class="skeleton skeleton-h14 skeleton-w50"></div><div class="skeleton skeleton-h14 skeleton-w83 skeleton-gap12"></div>';
    state.instrumentError = null;
    try {
      state.instrumentResult = await api(API.instrument, { method: "POST", body: JSON.stringify({ symbol }) });
      const stored = persistResearchResult(state.instrumentResult, "instrument");
      state.latestResearch = state.instrumentResult;
      statusNode.textContent = `Quote returned for ${symbol}${stored ? " · Saved in this browser." : " · Could not save to browser storage."}.`;
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
    status.textContent = "Checking the saved order against configured risk limits…";
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
      state.providerTest = {
        verified: Boolean(result?.verified),
        provider: result?.provider || null,
        model: result?.model || null,
        message: result?.message || null,
        attempts: Array.isArray(result?.attempts) ? result.attempts : []
      };
    } catch (error) {
      state.providerTest = {
        verified: false,
        message: error.data?.message || error.message,
        attempts: Array.isArray(error.data?.attempts) ? error.data.attempts : []
      };
      state.providerTestError = error;
    } finally {
      state.providerTestBusy = false;
      try { state.status = await api(API.status); } catch { /* Keep the latest visible status if refresh fails. */ }
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

  async function interpretResearch(researchId) {
    if (!researchId || state.interpretingResearch[researchId]) return;
    state.interpretingResearch[researchId] = true;
    render();
    try {
      state.aiInterpretations[researchId] = await api(API.researchInterpret, {
        method: "POST",
        body: JSON.stringify({ research_id: researchId }),
        timeoutMs: 120000
      });
    } catch (error) {
      state.aiInterpretations[researchId] = {
        ai_status: "unavailable",
        message: error.data?.message || error.data?.error?.message || error.message,
        attempts: Array.isArray(error.data?.attempts) ? error.data.attempts : []
      };
    } finally {
      delete state.interpretingResearch[researchId];
      render();
    }
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-route]");
    if (nav) navigate(nav.dataset.route, true);
    const go = event.target.closest("[data-go]");
    if (go) navigate(go.dataset.go, true);
    if (event.target.closest("[data-start-market-scan]")) {
      GENexusClientState.navigateToScanner(
        (route) => navigate(route),
        () => {
          const form = document.getElementById("analysis-scan-form");
          const input = form?.querySelector('[name="symbols"]');
          form?.scrollIntoView({ behavior: "smooth", block: "start" });
          input?.focus({ preventScroll: true });
        }
      );
    }
    const interpretButton = event.target.closest("[data-interpret-research]");
    if (interpretButton) interpretResearch(interpretButton.dataset.researchId);
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

  state.scans = loadResearchHistory();
  state.latestResearch = state.scans[state.scans.length - 1] || null;
  render();
  loadStatus();
})();
