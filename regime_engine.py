# FILE: regime_engine.py
# Bloomberg Macro Terminal — Regime Assessment Engine
# Classifies the current macro environment using live FRED data.
# Internal regimes: GOLDILOCKS / REFLATION / OVERHEATING / STAGFLATION_RISK / STAGFLATION / RECESSION
# GOLDILOCKS displays as "STRONG GROWTH". All other labels are unchanged.
# Thresholds come from config.py and auto-calibrate monthly from live FRED data.

import os
import requests
import logging
from datetime import datetime, timedelta
import time

from config import get_thresholds, REGIME_LABELS, REGIME_DESCRIPTIONS, POSITIONING

log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE    = "https://api.stlouisfed.org/fred/series/observations"
CACHE_TTL    = 3600  # seconds — refresh FRED data every 1 hour

# ── FRED SERIES IDs ───────────────────────────────────────────
SERIES = {
    "cpi_yoy":        "CPIAUCSL",
    "pce_yoy":        "PCEPILFE",
    "unemployment":   "UNRATE",
    "gdp_growth":     "GDPC1",
    "fed_funds":      "FEDFUNDS",
    "t10y2y":         "T10Y2Y",
    "ism_pmi":        "MANEMP",
    "m2":             "M2SL",
}

# ── SIMPLE CACHE ──────────────────────────────────────────────
_cache = {"data": None, "ts": 0}

def _cache_valid():
    return _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL

# ── FRED FETCH ────────────────────────────────────────────────
def _fetch_series(series_id, limit=13):
    """Fetch the most recent N observations for a FRED series."""
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not set in Replit Secrets.")
    params = {
        "series_id":     series_id,
        "api_key":       FRED_API_KEY,
        "file_type":     "json",
        "sort_order":    "desc",
        "limit":         limit,
        "observation_start": (datetime.utcnow() - timedelta(days=730)).strftime("%Y-%m-%d"),
    }
    resp = requests.get(FRED_BASE, params=params, timeout=10)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    # Filter out missing values
    return [o for o in obs if o.get("value") not in (".", "", None)]

def _latest(obs):
    """Return the most recent valid float value."""
    if not obs:
        return None
    return float(obs[0]["value"])

def _yoy_change(obs):
    """
    Calculate year-over-year % change from monthly observations.
    Requires at least 13 observations (current + 12 months ago).
    """
    valid = [o for o in obs if o.get("value") not in (".", "", None)]
    if len(valid) < 13:
        return None
    current = float(valid[0]["value"])
    year_ago = float(valid[12]["value"])
    if year_ago == 0:
        return None
    return ((current - year_ago) / abs(year_ago)) * 100

def _qoq_annualized(obs):
    """
    Calculate annualized QoQ GDP growth rate.
    Requires at least 2 quarterly observations.
    """
    valid = [o for o in obs if o.get("value") not in (".", "", None)]
    if len(valid) < 2:
        return None
    current  = float(valid[0]["value"])
    prior    = float(valid[1]["value"])
    if prior == 0:
        return None
    qoq = (current - prior) / abs(prior)
    return ((1 + qoq) ** 4 - 1) * 100  # annualize

# ── FETCH ALL INDICATORS ──────────────────────────────────────
def _fetch_indicators():
    """Fetch all macro indicators from FRED. Returns dict of values."""
    indicators = {}
    errors = []

    fetches = [
        ("cpi",          SERIES["cpi_yoy"],      13, "yoy"),
        ("pce",          SERIES["pce_yoy"],       13, "yoy"),
        ("unemployment", SERIES["unemployment"],   2, "latest"),
        ("gdp",          SERIES["gdp_growth"],     5, "qoq"),
        ("fed_funds",    SERIES["fed_funds"],       2, "latest"),
        ("t10y2y",       SERIES["t10y2y"],          2, "latest"),
        ("m2",           SERIES["m2"],             13, "yoy"),
    ]

    for key, series_id, limit, calc in fetches:
        try:
            obs = _fetch_series(series_id, limit=limit)
            if calc == "yoy":
                indicators[key] = _yoy_change(obs)
            elif calc == "qoq":
                indicators[key] = _qoq_annualized(obs)
            else:
                indicators[key] = _latest(obs)
        except Exception as e:
            log.warning(f"FRED fetch failed for {series_id}: {e}")
            indicators[key] = None
            errors.append(f"{key}: {str(e)[:60]}")

    indicators["_errors"] = errors
    return indicators

# ── SCORING ENGINE ────────────────────────────────────────────
def _score_indicators(ind):
    """
    Score each indicator on a -2 to +2 scale.
    Positive = growth/reflationary signal.
    Negative = recessionary/contractionary signal.
    Inflation is scored separately on its own axis.
    """
    T = get_thresholds()
    scores = {}

    # ── GROWTH SCORE ──────────────────────────────────────────
    gdp = ind.get("gdp")
    if gdp is not None:
        if gdp >= T["growth_strong"]:    scores["gdp"] = 2
        elif gdp >= T["growth_weak"]:    scores["gdp"] = 1
        elif gdp >= T["growth_negative"]: scores["gdp"] = -1
        else:                             scores["gdp"] = -2
    else:
        scores["gdp"] = 0

    # ── LABOR MARKET ──────────────────────────────────────────
    ur = ind.get("unemployment")
    if ur is not None:
        if ur <= T["unemployment_low"]:    scores["unemployment"] = 2
        elif ur <= T["unemployment_high"]: scores["unemployment"] = 0
        else:                              scores["unemployment"] = -2
    else:
        scores["unemployment"] = 0

    # ── YIELD CURVE ───────────────────────────────────────────
    t10y2y = ind.get("t10y2y")
    if t10y2y is not None:
        if t10y2y >= T["spread_steep"]:    scores["curve"] = 2
        elif t10y2y >= T["spread_inverted"]: scores["curve"] = 0
        else:                               scores["curve"] = -2
    else:
        scores["curve"] = 0

    # ── MONETARY POLICY ───────────────────────────────────────
    ff = ind.get("fed_funds")
    if ff is not None:
        if ff >= T["fed_funds_high"]: scores["policy"] = -1
        else:                         scores["policy"] = 1
    else:
        scores["policy"] = 0

    # ── INFLATION SCORE (separate axis) ───────────────────────
    cpi = ind.get("cpi")
    pce = ind.get("pce")

    if cpi is not None and pce is not None:
        inflation = (cpi + pce) / 2
    elif cpi is not None:
        inflation = cpi
    elif pce is not None:
        inflation = pce
    else:
        inflation = None

    if inflation is not None:
        if inflation >= T["inflation_very_high"]: scores["inflation"] = 3
        elif inflation >= T["inflation_high"]:    scores["inflation"] = 2
        elif inflation >= T["inflation_low"]:     scores["inflation"] = 1
        else:                                     scores["inflation"] = 0
    else:
        scores["inflation"] = 1

    scores["_inflation_val"] = inflation
    scores["_gdp_val"]       = gdp
    scores["_ur_val"]        = ur
    scores["_curve_val"]     = t10y2y
    scores["_ff_val"]        = ff

    return scores

# ── REGIME CLASSIFICATION ─────────────────────────────────────
def _classify_regime(scores):
    """
    Classify macro regime from scored indicators.
    Returns (label, confidence_score 0-100, description).
    """
    growth_score    = scores.get("gdp", 0) + scores.get("unemployment", 0)
    curve_score     = scores.get("curve", 0)
    policy_score    = scores.get("policy", 0)
    inflation_score = scores.get("inflation", 1)

    # Combined growth signal: -4 to +4
    # inflation_score: 0 (deflation) to 3 (stagflation)

    # ── RECESSION ─────────────────────────────────────────────
    if growth_score <= -2 and curve_score <= -2:
        label = "RECESSION"
        confidence = min(95, 60 + abs(growth_score) * 10 + abs(curve_score) * 5)

    elif growth_score <= -2:
        label = "RECESSION"
        confidence = min(85, 55 + abs(growth_score) * 10)

    # ── STAGFLATION ───────────────────────────────────────────
    elif growth_score <= 0 and inflation_score >= 3:
        label = "STAGFLATION"
        confidence = min(95, 65 + inflation_score * 8)

    elif growth_score <= 1 and inflation_score >= 2:
        label = "STAGFLATION_RISK"
        confidence = min(85, 55 + inflation_score * 7)

    # ── OVERHEATING ───────────────────────────────────────────
    elif growth_score >= 3 and inflation_score >= 2:
        label = "OVERHEATING"
        confidence = min(90, 60 + growth_score * 5 + inflation_score * 5)

    # ── GOLDILOCKS ────────────────────────────────────────────
    elif growth_score >= 2 and inflation_score <= 1:
        label = "GOLDILOCKS"
        confidence = min(90, 60 + growth_score * 8)

    # ── REFLATION ─────────────────────────────────────────────
    elif growth_score >= 1 and inflation_score >= 1 and curve_score >= 0:
        label = "REFLATION"
        confidence = min(80, 55 + growth_score * 5 + inflation_score * 5)

    # ── DEFAULT: STAGFLATION_RISK ─────────────────────────────
    # Current base case given elevated inflation + slowing growth
    else:
        label = "STAGFLATION_RISK"
        confidence = 45

    return label, int(confidence)

# ── BUILD INDICATOR BREAKDOWN ─────────────────────────────────
def _build_breakdown(ind, scores):
    """Build the indicator_breakdown array for the API response."""
    T = get_thresholds()
    breakdown = []

    cpi = ind.get("cpi")
    breakdown.append({
        "name":   "CPI YOY",
        "value":  f"{cpi:.1f}%" if cpi is not None else "N/A",
        "signal": "BEARISH" if (cpi or 0) >= T["inflation_very_high"]
                  else "NEUTRAL-BEARISH" if (cpi or 0) >= T["inflation_high"]
                  else "NEUTRAL" if (cpi or 0) >= T["inflation_low"]
                  else "BULLISH",
        "raw":    cpi,
    })

    pce = ind.get("pce")
    breakdown.append({
        "name":   "CORE PCE YOY",
        "value":  f"{pce:.1f}%" if pce is not None else "N/A",
        "signal": "BEARISH" if (pce or 0) >= T["inflation_very_high"]
                  else "NEUTRAL-BEARISH" if (pce or 0) >= T["inflation_high"]
                  else "NEUTRAL" if (pce or 0) >= T["inflation_low"]
                  else "BULLISH",
        "raw":    pce,
    })

    gdp = ind.get("gdp")
    breakdown.append({
        "name":   "GDP ANNUALIZED",
        "value":  f"{gdp:.1f}%" if gdp is not None else "N/A",
        "signal": "BULLISH" if (gdp or 0) >= T["growth_strong"]
                  else "NEUTRAL" if (gdp or 0) >= T["growth_weak"]
                  else "BEARISH",
        "raw":    gdp,
    })

    ur = ind.get("unemployment")
    breakdown.append({
        "name":   "UNEMPLOYMENT",
        "value":  f"{ur:.1f}%" if ur is not None else "N/A",
        "signal": "BULLISH" if (ur or 99) <= T["unemployment_low"]
                  else "NEUTRAL" if (ur or 99) <= T["unemployment_high"]
                  else "BEARISH",
        "raw":    ur,
    })

    ff = ind.get("fed_funds")
    breakdown.append({
        "name":   "FED FUNDS",
        "value":  f"{ff:.2f}%" if ff is not None else "N/A",
        "signal": "BEARISH" if (ff or 0) >= T["fed_funds_high"]
                  else "NEUTRAL",
        "raw":    ff,
    })

    t = ind.get("t10y2y")
    breakdown.append({
        "name":   "10Y-2Y SPREAD",
        "value":  f"{t:.2f}%" if t is not None else "N/A",
        "signal": "BULLISH" if (t or -99) >= T["spread_steep"]
                  else "NEUTRAL" if (t or -99) >= T["spread_inverted"]
                  else "BEARISH",
        "raw":    t,
    })

    m2 = ind.get("m2")
    breakdown.append({
        "name":   "M2 YOY",
        "value":  f"{m2:.1f}%" if m2 is not None else "N/A",
        "signal": "BULLISH" if (m2 or 0) >= 5
                  else "NEUTRAL" if (m2 or 0) >= 0
                  else "BEARISH",
        "raw":    m2,
    })

    return breakdown

# ── BUILD KEY RISKS ───────────────────────────────────────────
def _build_risks(internal_label, ind, scores):
    """Generate key risk flags based on current regime and data."""
    T = get_thresholds()
    risks = []

    cpi    = ind.get("cpi") or 0
    gdp    = ind.get("gdp") or 0
    ur     = ind.get("unemployment") or 0
    t10y2y = ind.get("t10y2y") or 0
    ff     = ind.get("fed_funds") or 0
    m2     = ind.get("m2") or 0

    if cpi >= T["inflation_very_high"]:
        risks.append(f"CPI AT {cpi:.1f}% — STAGFLATIONARY PRESSURE ELEVATED")
    elif cpi >= T["inflation_high"]:
        risks.append(f"INFLATION ABOVE FED TARGET AT {cpi:.1f}%")

    if t10y2y < T["spread_inverted"]:
        risks.append(f"YIELD CURVE INVERTED {t10y2y:.2f}% — RECESSION SIGNAL")

    if gdp < T["growth_negative"]:
        risks.append(f"GDP CONTRACTION AT {gdp:.1f}% — RECESSION CONDITIONS")
    elif gdp < T["growth_weak"]:
        risks.append(f"GDP GROWTH SLOWING — {gdp:.1f}% ANNUALIZED")

    if ff >= T["fed_funds_high"] and cpi >= T["inflation_high"]:
        risks.append(f"FED RESTRICTIVE AT {ff:.2f}% WITH INFLATION {cpi:.1f}%")

    if ur >= T["unemployment_high"]:
        risks.append(f"UNEMPLOYMENT ELEVATED AT {ur:.1f}%")

    if m2 < 0:
        risks.append(f"M2 CONTRACTING {m2:.1f}% YOY — LIQUIDITY TIGHTENING")

    if internal_label in ("STAGFLATION", "STAGFLATION_RISK"):
        risks.append("STAGFLATION LIMITS FED CIRCUIT-BREAKING CAPACITY")

    if internal_label == "RECESSION":
        risks.append("CREDIT STRESS RISK — MONITOR PRIVATE CREDIT SPREADS")

    return risks[:5] if risks else ["NO CRITICAL FLAGS AT THIS TIME"]

# ── MAIN ENTRY POINT ──────────────────────────────────────────
def get_regime():
    """
    Primary function called by main.py /api/regime route.
    Returns full regime assessment object.
    Uses cache — refreshes every CACHE_TTL seconds.
    """
    if _cache_valid():
        log.info("Regime: returning cached data.")
        return _cache["data"]

    log.info("Regime: fetching fresh FRED data...")
    ts = datetime.utcnow().isoformat()

    try:
        # Fetch raw indicators
        ind = _fetch_indicators()
        fetch_errors = ind.pop("_errors", [])

        # Score indicators
        scores = _score_indicators(ind)

        # Classify regime
        internal_label, confidence = _classify_regime(scores)

        # Map internal label → plain-English display label
        display_label = REGIME_LABELS.get(internal_label, internal_label)
        description   = REGIME_DESCRIPTIONS.get(display_label, "")

        # Build response components
        breakdown   = _build_breakdown(ind, scores)
        risks       = _build_risks(internal_label, ind, scores)
        positioning = POSITIONING.get(display_label, [])

        result = {
            "label":                  display_label,
            "internal_label":         internal_label,
            "description":            description,
            "confidence_score":       confidence,
            "indicator_breakdown":    breakdown,
            "key_risks":              risks,
            "asset_class_positioning": positioning,
            "timestamp":              ts,
            "data_errors":            fetch_errors,
            "raw_indicators":         {
                k: round(v, 4) if v is not None else None
                for k, v in ind.items()
            },
        }

        # Cache result
        _cache["data"] = result
        _cache["ts"]   = time.time()

        log.info(f"Regime classified: {label} ({confidence}% confidence)")
        return result

    except Exception as e:
        log.error(f"Regime engine failure: {e}")
        # Return a safe fallback — never crash the dashboard
        return {
            "label":                  "UNAVAILABLE",
            "confidence_score":       0,
            "indicator_breakdown":    [],
            "key_risks":              [f"DATA UNAVAILABLE: {str(e)[:100]}"],
            "asset_class_positioning": [],
            "timestamp":              ts,
            "data_errors":            [str(e)],
            "raw_indicators":         {},
        }


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    # Set FRED_API_KEY in your environment before running directly
    result = get_regime()
    print(json.dumps(result, indent=2))
