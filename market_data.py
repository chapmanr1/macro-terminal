# FILE: market_data.py
# Bloomberg Macro Terminal — Market Data via Yahoo Finance
# Fetches equities, futures, VIX, sectors, commodities, currencies.

import time
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CACHE_TTL = 300  # 5 minutes for market data

_cache = {"data": None, "ts": 0}

def _cache_valid():
    return _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL

# ── SYMBOL DEFINITIONS ────────────────────────────────────────
INDICES = [
    {"symbol": "^GSPC", "label": "S&P 500",    "abbr": "SPX"},
    {"symbol": "^DJI",  "label": "DOW JONES",  "abbr": "DJIA"},
    {"symbol": "^IXIC", "label": "NASDAQ",      "abbr": "NDX"},
    {"symbol": "^RUT",  "label": "RUSSELL 2K",  "abbr": "RUT"},
]

FUTURES = [
    {"symbol": "ES=F",  "label": "S&P FUT",    "group": "equity"},
    {"symbol": "NQ=F",  "label": "NQ FUT",     "group": "equity"},
    {"symbol": "YM=F",  "label": "DOW FUT",    "group": "equity"},
]

SECTORS = [
    {"symbol": "XLK",  "label": "TECHNOLOGY",       "short": "TECH"},
    {"symbol": "XLF",  "label": "FINANCIALS",        "short": "FINS"},
    {"symbol": "XLE",  "label": "ENERGY",            "short": "ENGY"},
    {"symbol": "XLV",  "label": "HEALTH CARE",       "short": "HLTH"},
    {"symbol": "XLI",  "label": "INDUSTRIALS",       "short": "INDU"},
    {"symbol": "XLB",  "label": "MATERIALS",         "short": "MATL"},
    {"symbol": "XLY",  "label": "CONS DISC",         "short": "DISC"},
    {"symbol": "XLP",  "label": "CONS STAPLES",      "short": "STPL"},
    {"symbol": "XLRE", "label": "REAL ESTATE",       "short": "REIT"},
    {"symbol": "XLU",  "label": "UTILITIES",         "short": "UTIL"},
    {"symbol": "XLC",  "label": "COMM SERVICES",     "short": "COMM"},
]

COMMODITIES = [
    {"symbol": "CL=F",  "label": "CRUDE OIL",   "suffix": "$/bbl", "decimals": 2},
    {"symbol": "GC=F",  "label": "GOLD",        "suffix": "$/oz",  "decimals": 2},
    {"symbol": "SI=F",  "label": "SILVER",      "suffix": "$/oz",  "decimals": 3},
    {"symbol": "HG=F",  "label": "COPPER",      "suffix": "$/lb",  "decimals": 3},
    {"symbol": "NG=F",  "label": "NAT GAS",     "suffix": "$/mmBtu","decimals": 3},
]

CURRENCIES = [
    {"symbol": "DX-Y.NYB", "label": "DXY",     "description": "US Dollar Index", "decimals": 2},
    {"symbol": "EURUSD=X", "label": "EUR/USD",  "description": "Euro / US Dollar","decimals": 4},
]
VIX_SYMBOL = "^VIX"
VIX_DISPLAY_MAX = 50


# ── YFINANCE FETCH ────────────────────────────────────────────
def _fetch_ticker_stats(symbol, ytd=False):
    """
    Fetch price, daily change, pct_change for a symbol.
    If ytd=True, also compute year-to-date % change.
    Returns dict or None on failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", auto_adjust=True)
        if hist.empty or len(hist) < 2:
            hist = ticker.history(period="1mo", auto_adjust=True)
        if hist.empty:
            return None

        hist = hist.dropna(subset=["Close"])
        if len(hist) < 1:
            return None

        current = float(hist["Close"].iloc[-1])
        prior   = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
        change     = current - prior
        pct_change = (change / prior * 100) if prior != 0 else 0.0

        result = {
            "price":      round(current, 4),
            "change":     round(change, 4),
            "pct_change": round(pct_change, 3),
            "direction":  "UP" if change > 0.0005 else "DOWN" if change < -0.0005 else "FLAT",
            "ytd_pct":    None,
        }

        if ytd:
            try:
                year_start = f"{datetime.now().year}-01-01"
                hist_ytd = ticker.history(start=year_start, auto_adjust=True).dropna(subset=["Close"])
                if not hist_ytd.empty:
                    jan_price = float(hist_ytd["Close"].iloc[0])
                    if jan_price != 0:
                        result["ytd_pct"] = round((current - jan_price) / jan_price * 100, 2)
            except Exception as e:
                log.warning(f"YTD calc failed for {symbol}: {e}")

        return result

    except Exception as e:
        log.warning(f"yfinance fetch failed [{symbol}]: {e}")
        return None


def _null_instrument(label, symbol=""):
    return {
        "symbol":     symbol,
        "label":      label,
        "price":      None,
        "change":     None,
        "pct_change": None,
        "ytd_pct":    None,
        "direction":  "FLAT",
        "error":      True,
    }


# ── SIGNAL GENERATORS ─────────────────────────────────────────
def _futures_signal(futures_data):
    equity = [f for f in futures_data if f.get("group") == "equity" and f.get("pct_change") is not None]
    if not equity:
        return "NEUTRAL", "Futures data unavailable — cannot determine pre-market bias."
    avg = sum(f["pct_change"] for f in equity) / len(equity)
    all_pos = all(f["pct_change"] > 0 for f in equity)
    all_neg = all(f["pct_change"] < 0 for f in equity)

    if all_pos and avg > 0.4:
        return "RISK ON", "All three major equity futures are pointing higher — pre-market bias is constructive."
    elif all_pos and avg > 0.1:
        return "MILDLY POSITIVE", "Equity futures tilted higher but gains are modest — cautiously constructive tone."
    elif all_neg and avg < -0.4:
        return "RISK OFF", "All three major equity futures under pressure — pre-market de-risking underway."
    elif all_neg:
        return "MILDLY NEGATIVE", "Equity futures slightly lower — modest pre-market headwinds."
    else:
        return "MIXED", "Divergence across equity futures — no clear directional bias pre-market."


def _vix_signal(vix_value):
    from config import VIX_LEVELS, VIX_DISPLAY_MAX
    if vix_value is None:
        return {"label": "UNKNOWN", "color": "muted", "gauge_pct": 0,
                "description": "VIX data unavailable."}
    for level in VIX_LEVELS:
        if vix_value <= level["max"]:
            gauge_pct = min(100, round(vix_value / VIX_DISPLAY_MAX * 100, 1))
            return {
                "label":       level["label"],
                "color":       level["color"],
                "gauge_pct":   gauge_pct,
                "description": level["description"],
            }
    return {"label": "EXTREME FEAR", "color": "red", "gauge_pct": 100,
            "description": VIX_LEVELS[-1]["description"]}


def _sector_signal(sectors_data):
    valid = [s for s in sectors_data if s.get("pct_change") is not None]
    if not valid:
        return "NO DATA", "Sector data unavailable."

    positive = sum(1 for s in valid if s["pct_change"] > 0)
    negative = sum(1 for s in valid if s["pct_change"] < 0)
    total    = len(valid)

    defensive_sym = {"XLU", "XLP", "XLV", "XLRE"}
    cyclical_sym  = {"XLK", "XLF", "XLE", "XLI", "XLB", "XLY", "XLC"}

    def avg_pct(syms):
        vals = [s["pct_change"] for s in valid if s["symbol"] in syms]
        return sum(vals) / len(vals) if vals else 0

    def_avg  = avg_pct(defensive_sym)
    cyc_avg  = avg_pct(cyclical_sym)

    if positive >= 9:
        if cyc_avg > def_avg + 0.3:
            return "RISK ON — CYCLICAL LEADERSHIP", "Wide breadth led by cyclicals signals investor confidence in durable economic expansion."
        return "BROAD ADVANCE", "Near-unanimous sector participation suggests genuine macro momentum rather than narrow speculation."
    elif negative >= 9:
        if def_avg > cyc_avg + 0.3:
            return "RISK OFF — DEFENSIVE ROTATION", "Defensive sectors outperforming as money rotates away from growth-sensitive cyclicals."
        return "BROAD SELL-OFF", "Wide sector decline points to macro-driven forced de-risking, not isolated sector weakness."
    elif cyc_avg > def_avg + 0.5:
        return "CYCLICAL ROTATION — GROWTH POSITIVE", "Cyclicals outperforming defensives by a meaningful margin — markets are pricing in growth resilience."
    elif def_avg > cyc_avg + 0.5:
        return "DEFENSIVE ROTATION — RISK AVERSE", "Defensives outperforming cyclicals signals elevated macro uncertainty and growth concern."
    elif positive > negative:
        return "SLIGHT POSITIVE BIAS", f"{positive} of {total} sectors advancing — modest constructive tone without clear leadership."
    elif negative > positive:
        return "SLIGHT NEGATIVE BIAS", f"{negative} of {total} sectors declining — modest risk-off tone without clear defensive rotation."
    else:
        return "MIXED — NO CLEAR SIGNAL", "Sector performance is balanced with no dominant rotation signal — wait for confirmation."


def _dollar_signal(dxy_value):
    from config import DOLLAR_THRESHOLDS as D
    if dxy_value is None:
        return "UNKNOWN", "DXY data unavailable."
    if dxy_value >= D["very_strong"]:
        return "USD VERY STRONG", f"DXY at {dxy_value:.1f} is significantly elevated — meaningful headwind for commodities, EM assets, and multinational earnings."
    elif dxy_value >= D["strong"]:
        return "USD STRONG", f"DXY at {dxy_value:.1f} reflects dollar demand — watch for drag on commodity prices and export-sensitive earnings."
    elif dxy_value >= D["neutral_hi"]:
        return "USD FIRM", f"DXY at {dxy_value:.1f} is near-neutral but tilted toward dollar strength — balanced currency conditions."
    elif dxy_value >= D["neutral_lo"]:
        return "USD NEUTRAL", f"DXY at {dxy_value:.1f} is in neutral territory — no outsized currency headwinds or tailwinds."
    elif dxy_value >= D["weak"]:
        return "USD SOFT", f"DXY at {dxy_value:.1f} is mildly weak — modest tailwind for commodities and international risk assets."
    else:
        return "USD WEAK", f"DXY at {dxy_value:.1f} is meaningfully depressed — significant tailwind for commodities, EM, and international equities."


# ── MAIN ENTRY POINT ──────────────────────────────────────────
def get_market():
    if _cache_valid():
        log.info("Market: returning cached data.")
        return _cache["data"]

    log.info("Market: fetching fresh yfinance data...")
    ts = datetime.now(timezone.utc).isoformat()
    try:
        return _fetch_market_data()
    except Exception as e:
        log.error(f"Market: fetch failed — {e}")
        if _cache["data"] is not None:
            log.info("Market: returning stale cache after error.")
            return _cache["data"]
        return {"indices": [], "futures": [], "sectors": [], "commodities": [], "currencies": [],
                "timestamp": ts, "error": str(e)}

def _fetch_market_data():
    ts = datetime.now(timezone.utc).isoformat()

    # ── INDICES ───────────────────────────────────────────────
    indices_out = []
    for idx in INDICES:
        stats = _fetch_ticker_stats(idx["symbol"], ytd=True)
        if stats:
            indices_out.append({**idx, **stats})
        else:
            indices_out.append({**idx, **_null_instrument(idx["label"], idx["symbol"])})

    # ── FUTURES ───────────────────────────────────────────────
    futures_out = []
    for fut in FUTURES:
        stats = _fetch_ticker_stats(fut["symbol"])
        if stats:
            futures_out.append({**fut, **stats})
        else:
            futures_out.append({**fut, **_null_instrument(fut["label"], fut["symbol"])})

    futures_signal, futures_detail = _futures_signal(futures_out)

    # ── VIX ───────────────────────────────────────────────────
    vix_stats  = _fetch_ticker_stats(VIX_SYMBOL)
    vix_value  = vix_stats["price"] if vix_stats else None
    vix_info   = _vix_signal(vix_value)
    vix_out    = {
        "symbol":    VIX_SYMBOL,
        "label":     "VIX",
        "value":     vix_value,
        "change":    vix_stats["change"]     if vix_stats else None,
        "pct_change":vix_stats["pct_change"] if vix_stats else None,
        "direction": vix_stats["direction"]  if vix_stats else "FLAT",
        **vix_info,
    }

    # ── SECTORS ───────────────────────────────────────────────
    sectors_out = []
    for sec in SECTORS:
        stats = _fetch_ticker_stats(sec["symbol"], ytd=True)
        if stats:
            sectors_out.append({**sec, **stats})
        else:
            sectors_out.append({**sec, **_null_instrument(sec["label"], sec["symbol"])})

    sector_signal, sector_detail = _sector_signal(sectors_out)

    # ── COMMODITIES ───────────────────────────────────────────
    commodities_out = []
    for com in COMMODITIES:
        stats = _fetch_ticker_stats(com["symbol"])
        entry = {**com}
        if stats:
            entry.update({
                "price":      round(stats["price"],      com["decimals"]),
                "change":     round(stats["change"],     com["decimals"]),
                "pct_change": stats["pct_change"],
                "direction":  stats["direction"],
            })
        else:
            entry.update({"price": None, "change": None, "pct_change": None, "direction": "FLAT"})
        commodities_out.append(entry)

    # ── CURRENCIES ────────────────────────────────────────────
    currencies_out = []
    dxy_value = None
    for cur in CURRENCIES:
        # Fallback: try DX=F if DX-Y.NYB fails for DXY
        symbols_to_try = [cur["symbol"]]
        if cur["symbol"] == "DX-Y.NYB":
            symbols_to_try.append("DX=F")

        stats = None
        for sym in symbols_to_try:
            stats = _fetch_ticker_stats(sym)
            if stats:
                break

        entry = {**cur}
        if stats:
            entry.update({
                "price":      round(stats["price"],      cur["decimals"]),
                "change":     round(stats["change"],     cur["decimals"]),
                "pct_change": stats["pct_change"],
                "direction":  stats["direction"],
            })
            if cur["label"] == "DXY":
                dxy_value = stats["price"]
        else:
            entry.update({"price": None, "change": None, "pct_change": None, "direction": "FLAT"})
        currencies_out.append(entry)

    dollar_signal, dollar_detail = _dollar_signal(dxy_value)

    result = {
        "indices":        indices_out,
        "futures":        futures_out,
        "futures_signal": futures_signal,
        "futures_detail": futures_detail,
        "vix":            vix_out,
        "sectors":        sectors_out,
        "sector_signal":  sector_signal,
        "sector_detail":  sector_detail,
        "commodities":    commodities_out,
        "currencies":     currencies_out,
        "dollar_signal":  dollar_signal,
        "dollar_detail":  dollar_detail,
        "timestamp":      ts,
    }

    _cache["data"] = result
    _cache["ts"]   = time.time()
    log.info(f"Market: fetched — futures={futures_signal}, VIX={vix_value}, sector={sector_signal}")
    return result


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    print(json.dumps(get_market(), indent=2, default=str))
