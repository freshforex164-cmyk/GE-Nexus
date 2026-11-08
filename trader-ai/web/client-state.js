(function attachClientState(root, factory) {
  const clientState = factory();
  if (typeof module === "object" && module.exports) module.exports = clientState;
  if (root) root.GENexusClientState = clientState;
})(typeof globalThis === "undefined" ? this : globalThis, function createClientState() {
  "use strict";

  const TICKER_RE = /^[A-Z][A-Z0-9]{0,4}(?:[.-][A-Z])?$/;
  const HISTORY_LIMIT = 8;
  const HISTORY_TTL_MS = 72 * 60 * 60 * 1000;

  function parseScannerSymbols(value, maxSymbols = 20) {
    const tokens = String(value || "").split(/[,\s]+/).map((item) => item.trim()).filter(Boolean);
    const symbols = [];
    const validSymbols = [];
    const invalidSymbols = [];
    const duplicates = [];
    const seen = new Set();
    for (const token of tokens) {
      const normalized = token.toUpperCase();
      if (seen.has(normalized)) {
        duplicates.push(normalized.slice(0, 30));
        continue;
      }
      seen.add(normalized);
      const shown = normalized.slice(0, 30);
      symbols.push(shown);
      if (TICKER_RE.test(normalized)) validSymbols.push(shown);
      else invalidSymbols.push(shown);
    }
    return {
      symbols,
      validSymbols,
      invalidSymbols,
      duplicates,
      tooMany: tokens.length > maxSymbols,
      empty: tokens.length === 0
    };
  }

  function recordTimestamp(item) {
    const values = [
      item?.saved_at,
      item?.data?.created_at,
      item?.scan_summary?.completed_at,
      item?.data?.as_of
    ];
    for (const value of values) {
      if (typeof value !== "string") continue;
      const timestamp = Date.parse(value);
      if (Number.isFinite(timestamp)) return timestamp;
    }
    return null;
  }

  function parseDate(value) {
    if (typeof value !== "string") return null;
    const direct = Date.parse(value);
    if (Number.isFinite(direct)) return direct;
    const day = value.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
    if (!day) return null;
    const dateOnly = Date.parse(`${day}T00:00:00Z`);
    return Number.isFinite(dateOnly) ? dateOnly : null;
  }

  function expireResearchHistory(records, now = Date.now()) {
    if (!Array.isArray(records)) return [];
    return records
      .filter((item) => {
        if (!item || typeof item !== "object" || item.status !== "completed") return false;
        const timestamp = recordTimestamp(item);
        return timestamp !== null && timestamp <= now && now - timestamp <= HISTORY_TTL_MS;
      })
      .slice(-HISTORY_LIMIT)
      .map((item) => {
        const saved = { ...item };
        delete saved.research_id;
        return saved;
      });
  }

  function ageLabel(value, now = Date.now()) {
    const timestamp = typeof value === "number" ? value : parseDate(value || "");
    if (!Number.isFinite(timestamp) || timestamp > now) return "Age unavailable";
    const elapsed = now - timestamp;
    if (elapsed < 60 * 60 * 1000) return "Less than 1 hour ago";
    if (elapsed < 24 * 60 * 60 * 1000) {
      return `${Math.floor(elapsed / (60 * 60 * 1000))} hours ago`;
    }
    return `${Math.floor(elapsed / (24 * 60 * 60 * 1000))} days ago`;
  }

  function navigateToScanner(navigate, focusScanner) {
    navigate("analysis", false);
    focusScanner();
  }

  return {
    HISTORY_LIMIT,
    HISTORY_TTL_MS,
    ageLabel,
    expireResearchHistory,
    navigateToScanner,
    parseScannerSymbols
  };
});
