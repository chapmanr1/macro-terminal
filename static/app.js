// FILE: static/app.js
// Bloomberg Macro Terminal — Frontend JS Scaffold
// Core rendering logic lives in index.html.
// This file handles utilities and optional extensions.

// ── Utility: Format numbers ───────────────────────────────────
window.TerminalUtils = {

  fmt(val, decimals = 2, suffix = '') {
    if (val === null || val === undefined) return '--';
    return Number(val).toFixed(decimals) + suffix;
  },

  colorClass(val, positiveIsGood = true) {
    if (val === null || val === undefined || isNaN(val)) return 'c-muted';
    if (positiveIsGood) return val > 0 ? 'c-green' : val < 0 ? 'c-red' : 'c-muted';
    return val > 0 ? 'c-red' : val < 0 ? 'c-green' : 'c-muted';
  },

  fmtRelTime(iso) {
    if (!iso) return '--';
    try {
      const diff = Math.floor((Date.now() - new Date(iso)) / 60000);
      if (diff < 1)  return 'JUST NOW';
      if (diff < 60) return diff + 'MIN AGO';
      return Math.floor(diff / 60) + 'HR AGO';
    } catch { return '--'; }
  },

  fmtTime(iso) {
    if (!iso) return '--';
    try {
      return new Date(iso).toLocaleTimeString('en-US',
        { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch { return '--'; }
  }

};

// ── Log terminal version to console ──────────────────────────
console.log('%cMACRO TERMINAL',
  'color:#f59e0b;font-family:monospace;font-size:14px;font-weight:bold;');
console.log('%cv1.0 — Bloomberg-style macro dashboard',
  'color:#4b5563;font-family:monospace;font-size:10px;');
