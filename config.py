# FILE: config.py
# Bloomberg Macro Terminal — Central Configuration
# Thresholds auto-calibrate monthly from live FRED data.
# Edit floors/ceilings or positioning here; never touch core logic.

import os
import json
import time
import logging
from datetime import datetime

log = logging.getLogger(__name__)

# ── CALIBRATION SETTINGS ──────────────────────────────────────
_CALIB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".thresholds.json")
_CALIB_TTL  = 30 * 24 * 3600   # 30 days in seconds
_mem_cache  = {"data": None, "ts": 0}

# ── DEFAULT THRESHOLDS (fallback if FRED unreachable) ─────────
DEFAULT_THRESHOLDS = {
    "inflation_high":       3.5,
    "inflation_low":        2.0,
    "inflation_very_high":  5.0,
    "growth_strong":        2.5,
    "growth_weak":          1.0,
    "growth_negative":      0.0,
    "unemployment_low":     4.0,
    "unemployment_high":    5.5,
    "fed_funds_high":       4.0,
    "spread_inverted":      0.0,
    "spread_steep":         1.0,
}

# ── PERCENTILE HELPER ─────────────────────────────────────────
def _pct(values, p):
    """Linear-interpolation percentile — no numpy required."""
    s = sorted(v for v in (values or []) if v is not None)
    if not s:
        return None
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)

# ── CALIBRATION FUNCTION ──────────────────────────────────────
def calibrate(fred_api_key):
    """
    Recalibrate regime thresholds using trailing FRED data.
    Uses rolling windows:
      - Inflation / labor / policy: last 36 months
      - GDP growth: last 16 quarters (4 years)
      - Yield curve: last 36 monthly averages

    Thresholds are set at percentile boundaries with absolute
    floors/ceilings so they can't drift into nonsensical territory.
    Returns a dict in the same shape as DEFAULT_THRESHOLDS.
    """
    import requests

    FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

    def _fetch(series_id, limit, frequency=None):
        params = {
            "series_id":  series_id,
            "api_key":    fred_api_key,
            "file_type":  "json",
            "sort_order": "desc",
            "limit":      limit,
            "observation_start": (
                datetime.utcnow().replace(year=datetime.utcnow().year - 6)
            ).strftime("%Y-%m-%d"),
        }
        if frequency:
            params["frequency"] = frequency
        resp = requests.get(FRED_BASE, params=params, timeout=15)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        return [float(o["value"]) for o in obs if o.get("value") not in (".", "", None)]

    def _yoy_pct_series(raw_monthly, n=36):
        """Compute n YoY % change values from descending monthly raw index."""
        result = []
        for i in range(min(n, len(raw_monthly) - 12)):
            ya = raw_monthly[i + 12]
            if ya != 0:
                result.append((raw_monthly[i] - ya) / abs(ya) * 100)
        return result

    def _qoq_ann_series(raw_quarterly, n=16):
        """Compute n QoQ annualised growth values from descending quarterly raw."""
        result = []
        for i in range(min(n, len(raw_quarterly) - 1)):
            p = raw_quarterly[i + 1]
            if p != 0:
                result.append(((raw_quarterly[i] / p) ** 4 - 1) * 100)
        return result

    # Fetch raw series
    cpi_raw  = _fetch("CPIAUCSL", 50)           # monthly CPI index level
    gdp_raw  = _fetch("GDPC1",    20)           # quarterly real GDP level
    unemp    = _fetch("UNRATE",   40)           # monthly unemployment %
    ff       = _fetch("FEDFUNDS", 40)           # monthly fed funds %
    spread   = _fetch("T10Y2Y",   40, "m")      # monthly 10Y-2Y %

    # Derived series
    cpi_yoy  = _yoy_pct_series(cpi_raw, 36)
    gdp_qoq  = _qoq_ann_series(gdp_raw, 16)

    def clamp(val, floor, ceiling=None):
        if val is None:
            return floor
        v = max(floor, round(val, 2))
        return min(v, ceiling) if ceiling is not None else v

    t = {
        # Inflation thresholds — shift with the current cycle's CPI distribution
        "inflation_low":       clamp(_pct(cpi_yoy, 25), 1.5, 3.0),
        "inflation_high":      clamp(_pct(cpi_yoy, 70), 2.8, 6.0),
        "inflation_very_high": clamp(_pct(cpi_yoy, 88), 4.0, 9.0),

        # Growth thresholds — shift with current cycle's GDP distribution
        "growth_strong":       clamp(_pct(gdp_qoq, 65), 2.0, 4.0),
        "growth_weak":         clamp(_pct(gdp_qoq, 30), 0.5, 2.0),
        "growth_negative":     0.0,   # contraction boundary is always zero

        # Labor thresholds — shift with current cycle's employment conditions
        "unemployment_low":    clamp(_pct(unemp, 25),  3.5, 5.0),
        "unemployment_high":   clamp(_pct(unemp, 75),  5.0, 8.0),

        # Policy threshold — what counts as "restrictive" in this cycle
        "fed_funds_high":      clamp(_pct(ff, 60),     2.5, 7.0),

        # Yield curve — inverted is always < 0; steep is cycle-relative
        "spread_inverted":     0.0,
        "spread_steep":        clamp(_pct(spread, 65), 0.5, 2.0),
    }

    log.info(f"Thresholds calibrated: {t}")
    return t


# ── GET THRESHOLDS (cached, auto-recalibrates monthly) ────────
def get_thresholds():
    """
    Returns calibrated regime thresholds.
    Priority: in-memory cache → disk cache → FRED recalibration → defaults.
    Recalibration happens at most once every 30 days.
    """
    now = time.time()

    # 1. Fast in-memory path
    if _mem_cache["data"] and (now - _mem_cache["ts"]) < _CALIB_TTL:
        return _mem_cache["data"]

    # 2. Disk cache
    if os.path.exists(_CALIB_FILE):
        try:
            with open(_CALIB_FILE) as f:
                saved = json.load(f)
            if (now - saved.get("ts", 0)) < _CALIB_TTL:
                _mem_cache["data"] = saved["thresholds"]
                _mem_cache["ts"]   = saved["ts"]
                log.info("Thresholds: loaded from disk cache.")
                return _mem_cache["data"]
        except Exception as e:
            log.warning(f"Threshold disk cache unreadable: {e}")

    # 3. Recalibrate from FRED
    fred_key = os.environ.get("FRED_API_KEY", "")
    if fred_key:
        try:
            new_t = calibrate(fred_key)
            ts    = now
            _mem_cache["data"] = new_t
            _mem_cache["ts"]   = ts
            try:
                with open(_CALIB_FILE, "w") as f:
                    json.dump({
                        "thresholds":    new_t,
                        "ts":            ts,
                        "calibrated_at": datetime.utcnow().isoformat(),
                    }, f, indent=2)
            except Exception as e:
                log.warning(f"Could not save threshold cache to disk: {e}")
            return new_t
        except Exception as e:
            log.warning(f"Threshold calibration failed, using defaults: {e}")

    # 4. Hardcoded defaults
    return dict(DEFAULT_THRESHOLDS)


def get_calibration_meta():
    """Return metadata about the last calibration (for /api/health)."""
    if os.path.exists(_CALIB_FILE):
        try:
            with open(_CALIB_FILE) as f:
                saved = json.load(f)
            age_days = (time.time() - saved.get("ts", 0)) / 86400
            return {
                "calibrated_at": saved.get("calibrated_at"),
                "age_days":      round(age_days, 1),
                "stale":         age_days > 30,
                "thresholds":    saved.get("thresholds"),
            }
        except Exception:
            pass
    return {"calibrated_at": None, "age_days": None, "stale": True, "thresholds": None}


# ── REGIME LABELS (only GOLDILOCKS gets a plain-English rename) ─
REGIME_LABELS = {
    "GOLDILOCKS": "STRONG GROWTH",
}

REGIME_DESCRIPTIONS = {
    "STRONG GROWTH":   "Economy expanding with controlled inflation",
    "REFLATION":       "Growth accelerating from trough",
    "OVERHEATING":     "Growth running too hot",
    "STAGFLATION_RISK": "Growth slowing with persistent inflation",
    "STAGFLATION":     "Stagnant growth with elevated inflation",
    "RECESSION":       "Economic contraction underway",
}

# ── ASSET CLASS POSITIONING BY REGIME ─────────────────────────
POSITIONING = {
    "STRONG GROWTH": [
        {"asset_class": "US EQUITIES",      "stance": "OW"},
        {"asset_class": "CORP CREDIT",      "stance": "OW"},
        {"asset_class": "INT'L EQUITIES",   "stance": "OW"},
        {"asset_class": "COMMODITIES",      "stance": "N"},
        {"asset_class": "GOLD",             "stance": "N"},
        {"asset_class": "LONG TREASURIES",  "stance": "N"},
        {"asset_class": "CASH",             "stance": "UW"},
        {"asset_class": "TIPS",             "stance": "UW"},
    ],
    "REFLATION": [
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "ENERGY",           "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "INT'L EQUITIES",   "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "N"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "N"},
        {"asset_class": "CASH",             "stance": "N"},
    ],
    "OVERHEATING": [
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "SHORT TREASURIES", "stance": "OW"},
        {"asset_class": "ENERGY",           "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "UW"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "UW"},
        {"asset_class": "CASH",             "stance": "N"},
    ],
    "STAGFLATION": [
        {"asset_class": "GOLD",             "stance": "OW"},
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "CASH",             "stance": "OW"},
        {"asset_class": "ENERGY",           "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "UW"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "UW"},
    ],
    "STAGFLATION_RISK": [
        {"asset_class": "GOLD",             "stance": "OW"},
        {"asset_class": "TIPS",             "stance": "OW"},
        {"asset_class": "COMMODITIES",      "stance": "OW"},
        {"asset_class": "CASH",             "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "N"},
        {"asset_class": "LONG TREASURIES",  "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "N"},
        {"asset_class": "ENERGY",           "stance": "OW"},
    ],
    "RECESSION": [
        {"asset_class": "LONG TREASURIES",  "stance": "OW"},
        {"asset_class": "GOLD",             "stance": "OW"},
        {"asset_class": "CASH",             "stance": "OW"},
        {"asset_class": "US EQUITIES",      "stance": "UW"},
        {"asset_class": "CORP CREDIT",      "stance": "UW"},
        {"asset_class": "COMMODITIES",      "stance": "UW"},
        {"asset_class": "ENERGY",           "stance": "UW"},
        {"asset_class": "TIPS",             "stance": "N"},
    ],
}

# ── VIX REGIME LEVELS ─────────────────────────────────────────
VIX_LEVELS = [
    {"max": 12,   "label": "COMPLACENT",  "color": "green",
     "description": "Markets pricing near-zero fear — historically precedes volatility spikes."},
    {"max": 17,   "label": "CALM",        "color": "green",
     "description": "Low volatility with bullish sentiment — risk assets broadly well-bid."},
    {"max": 22,   "label": "CAUTIOUS",    "color": "amber",
     "description": "Elevated uncertainty — investors hedging against potential downside."},
    {"max": 28,   "label": "FEARFUL",     "color": "amber",
     "description": "Significant market stress — de-risking underway across portfolios."},
    {"max": 35,   "label": "PANIC",       "color": "red",
     "description": "Acute fear selling — forced liquidations and margin calls likely."},
    {"max": 9999, "label": "EXTREME FEAR","color": "red",
     "description": "Crisis-level volatility — generational buying opportunities historically emerge here."},
]
VIX_DISPLAY_MAX = 50

# ── CREDIT SPREAD THRESHOLDS ──────────────────────────────────
CREDIT_THRESHOLDS = {
    "hy_tight":  300, "hy_normal": 450, "hy_wide":  600, "hy_crisis":  900,
    "ig_tight":   80, "ig_normal": 130, "ig_wide":  200, "ig_crisis":  300,
}

# ── DOLLAR INDEX THRESHOLDS ───────────────────────────────────
DOLLAR_THRESHOLDS = {
    "very_strong": 108, "strong": 104, "neutral_hi": 100,
    "neutral_lo":   96, "weak":    92,
}

# ── RECESSION PROBABILITY SCORING ────────────────────────────
RECESSION_WEIGHTS = {
    "yield_curve": 25, "gdp": 20, "credit_spreads": 20,
    "unemployment": 20, "ism": 15,
}

RECESSION_SIGNALS = [
    {"max": 20,  "label": "LOW",      "color": "green"},
    {"max": 40,  "label": "MODERATE", "color": "green"},
    {"max": 60,  "label": "ELEVATED", "color": "amber"},
    {"max": 80,  "label": "HIGH",     "color": "red"},
    {"max": 100, "label": "CRITICAL", "color": "red"},
]

# ── FALSIFICATION TRIGGERS ────────────────────────────────────
FALSIFICATION_TRIGGERS = [
    {
        "id": "core_pce", "label": "CORE PCE < 2.5%",
        "full_label": "CORE PCE BELOW 2.5% FOR 3 CONSECUTIVE MONTHS",
        "description": "Fed inflation target sustainably met — eliminates the supply-side constraint and restores full policy flexibility.",
        "threshold": 2.5, "direction": "below", "unit": "%",
        "fred_series": "PCEPILFE", "calc": "yoy", "sustained": 3,
    },
    {
        "id": "gdp_growth", "label": "GDP > 2.5% FOR 2Q",
        "full_label": "REAL GDP ABOVE 2.5% ANNUALIZED FOR 2 QUARTERS",
        "description": "Sustained above-trend growth invalidates the demand destruction thesis and supports durable risk asset performance.",
        "threshold": 2.5, "direction": "above", "unit": "%",
        "fred_series": "GDPC1", "calc": "qoq", "sustained": 2,
    },
    {
        "id": "hy_spreads", "label": "HY SPREADS < 300bp",
        "full_label": "HIGH YIELD OAS BELOW 300 BASIS POINTS",
        "description": "Tight credit spreads signal strong corporate balance sheets and low default risk — inconsistent with WARNING/CAUTION stress.",
        "threshold": 300, "direction": "below", "unit": "bp",
        "fred_series": "BAMLH0A0HYM2", "calc": "latest_bp", "sustained": 1,
    },
    {
        "id": "productivity", "label": "PRODUCTIVITY > 2%",
        "full_label": "NONFARM BUSINESS PRODUCTIVITY ABOVE 2% YOY",
        "description": "Above-trend productivity growth resolves the WARNING tension by expanding supply-side capacity without adding inflation.",
        "threshold": 2.0, "direction": "above", "unit": "%",
        "fred_series": "OPHNFB", "calc": "yoy", "sustained": 1,
    },
    {
        "id": "bdc_nonaccruals", "label": "BDC NON-ACCRUALS < 2%",
        "full_label": "BDC PORTFOLIO NON-ACCRUAL RATE BELOW 2%",
        "description": "Low non-accrual rates across major BDC portfolios (ARCC, BXSL, FSK) signal private credit health and absence of systemic stress.",
        "threshold": 2.0, "direction": "below", "unit": "%",
        "fred_series": None, "calc": "manual", "sustained": 1,
    },
]

# ── K-SHAPE DIVERGENCE THRESHOLDS ────────────────────────────
K_SHAPE = {
    "cc_delinquency_stress": 3.0, "cc_delinquency_crisis": 4.5,
    "savings_rate_low": 4.0,      "savings_rate_very_low": 2.5,
    "umich_weak": 70.0,           "umich_strong": 85.0,
    "cs_hpi_strong": 5.0,
}
