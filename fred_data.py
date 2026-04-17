# FILE: fred_data.py
# Bloomberg Macro Terminal — FRED Data Pipeline
# Fetches, calculates, caches, and serves all macro indicator data.

import os
import time
import logging
import requests
from datetime import datetime, timedelta, timezone, date as date_type
import calendar as cal_module

log = logging.getLogger(__name__)

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE    = "https://api.stlouisfed.org/fred/series/observations"
CACHE_TTL    = 3600

_cache = {
    "macro":    {"data": None, "ts": 0},
    "yields":   {"data": None, "ts": 0},
    "economy":  {"data": None, "ts": 0},
    "credit":   {"data": None, "ts": 0},
}

def _cache_valid(key):
    return (_cache[key]["data"] is not None and
            (time.time() - _cache[key]["ts"]) < CACHE_TTL)

def _set_cache(key, data):
    _cache[key]["data"] = data
    _cache[key]["ts"]   = time.time()


# ── FRED FETCH CORE ───────────────────────────────────────────
def _fetch_series(series_id, limit=36):
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not configured.")
    params = {
        "series_id":         series_id,
        "api_key":           FRED_API_KEY,
        "file_type":         "json",
        "sort_order":        "desc",
        "limit":             limit,
        "observation_start": (datetime.utcnow() - timedelta(days=1825)).strftime("%Y-%m-%d"),
    }
    resp = requests.get(FRED_BASE, params=params, timeout=12)
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    return [o for o in obs if o.get("value") not in (".", "", None)]


# ── CALCULATION HELPERS ───────────────────────────────────────
def _latest_val(obs):
    if not obs: return None
    return float(obs[0]["value"])

def _prior_val(obs, offset=1):
    if len(obs) <= offset: return None
    return float(obs[offset]["value"])

def _yoy_pct(obs):
    valid = [o for o in obs if o.get("value") not in (".", "", None)]
    if len(valid) < 2: return None, None
    current  = float(valid[0]["value"])
    year_ago = float(valid[12]["value"]) if len(valid) >= 13 else float(valid[-1]["value"])
    if year_ago == 0: return None, None
    yoy = ((current - year_ago) / abs(year_ago)) * 100
    prior_yoy = None
    if len(valid) >= 14:
        pc = float(valid[1]["value"])
        py = float(valid[13]["value"])
        if py != 0:
            prior_yoy = ((pc - py) / abs(py)) * 100
    return round(yoy, 2), (round(prior_yoy, 2) if prior_yoy is not None else None)

def _qoq_annualized(obs):
    valid = [o for o in obs if o.get("value") not in (".", "", None)]
    if len(valid) < 2: return None, None
    current = float(valid[0]["value"])
    prior   = float(valid[1]["value"])
    if prior == 0: return None, None
    qoq  = ((current / prior) ** 4 - 1) * 100
    pg   = None
    if len(valid) >= 3:
        p2 = float(valid[2]["value"])
        if p2 != 0:
            pg = ((prior / p2) ** 4 - 1) * 100
    return round(qoq, 2), (round(pg, 2) if pg is not None else None)

def _direction(change):
    if change is None: return "FLAT"
    if change > 0.005:  return "UP"
    if change < -0.005: return "DOWN"
    return "FLAT"

def _obs_date(obs):
    if not obs: return None
    return obs[0].get("date")

def _safe_change(current, prior):
    if current is None or prior is None: return None
    return round(current - prior, 4)


# ── PLAIN ENGLISH INTERPRETATION GENERATORS ──────────────────
def _interp_gdp(val, change):
    if val is None: return "GDP data unavailable."
    if val >= 3.0:  return f"GDP at {val:.1f}% annualized is above trend — economy showing genuine momentum and above-consensus resilience."
    if val >= 2.0:  return f"GDP at {val:.1f}% annualized is at trend — solid growth with no immediate recession signal."
    if val >= 0.5:  return f"GDP at {val:.1f}% annualized reflects slowing momentum — below trend growth heightens stagflation concern."
    if val >= 0:    return f"GDP at {val:.1f}% annualized is near stall speed — one more quarter of weakness would confirm technical recession."
    return f"GDP contracted {abs(val):.1f}% annualized — recession conditions are materializing."

def _interp_cpi(val, change):
    if val is None: return "CPI data unavailable."
    if val >= 5.0: return f"CPI at {val:.1f}% YoY remains acutely elevated — purchasing power erosion is severe and Fed credibility is at risk."
    if val >= 3.5: return f"CPI at {val:.1f}% YoY is above the Fed's comfort zone — inflationary pressures remain a binding constraint on policy."
    if val >= 2.5: return f"CPI at {val:.1f}% YoY is elevated but decelerating — the disinflationary trend needs to be sustained before declaring victory."
    if val >= 2.0: return f"CPI at {val:.1f}% YoY is near the Fed target — inflation appears contained, potentially allowing easing."
    return f"CPI at {val:.1f}% YoY is below target — deflationary pressure could emerge if growth continues to slow."

def _interp_pce(val, change):
    if val is None: return "Core PCE data unavailable."
    if val >= 3.5: return f"Core PCE at {val:.1f}% YoY — the Fed's preferred inflation gauge remains materially above the 2% target."
    if val >= 2.5: return f"Core PCE at {val:.1f}% YoY — progress toward the 2% target but not yet sufficient for the Fed to declare mission accomplished."
    if val >= 2.0: return f"Core PCE at {val:.1f}% YoY is near target — the Fed has significant flexibility to cut if growth softens."
    return f"Core PCE at {val:.1f}% YoY is below target — deflationary bias emerging, which could accelerate Fed easing."

def _interp_unemployment(val, change):
    if val is None: return "Unemployment data unavailable."
    trending = ""
    if change and change > 0.2: trending = " Rising trend signals deteriorating labor conditions."
    elif change and change < -0.2: trending = " Falling trend reflects continued tightening in labor supply."
    if val < 4.0:  return f"Unemployment at {val:.1f}% remains historically tight — full employment supports consumer spending and wage growth.{trending}"
    if val < 5.0:  return f"Unemployment at {val:.1f}% shows a gradually softening labor market — still broadly healthy but deserves monitoring.{trending}"
    return f"Unemployment at {val:.1f}% reflects meaningful labor market slack — reduced consumer income and spending pressure ahead.{trending}"

def _interp_claims(val, change):
    if val is None: return "Jobless claims data unavailable."
    if val < 200000: return f"Initial claims at {val:,.0f} are exceptionally low — companies are not laying off workers, consistent with tight labor market."
    if val < 250000: return f"Initial claims at {val:,.0f} are within normal range — no meaningful deterioration in labor market conditions."
    if val < 300000: return f"Initial claims at {val:,.0f} are elevated — early signs of labor market softening worth monitoring closely."
    return f"Initial claims at {val:,.0f} signal meaningful job losses — labor market stress is building and could accelerate further."

def _interp_jolts(val, change):
    if val is None: return "JOLTS data unavailable."
    m = val / 1e6
    if m > 10: return f"Job openings at {m:.1f}M remain very elevated — demand for labor far exceeds supply, keeping wage growth and inflation elevated."
    if m > 8:  return f"Job openings at {m:.1f}M are healthy — labor demand is solid though down from recent peaks."
    if m > 6:  return f"Job openings at {m:.1f}M are near pre-pandemic norms — labor market rebalancing is underway."
    return f"Job openings at {m:.1f}M signal diminished labor demand — companies are pulling back on hiring as growth slows."

def _interp_umich(val, change):
    if val is None: return "UMich sentiment data unavailable."
    if val >= 90: return f"Consumer sentiment at {val:.1f} is high — households are optimistic about income, job security, and spending plans."
    if val >= 75: return f"Consumer sentiment at {val:.1f} reflects neutral-to-positive consumer mood — spending should remain resilient."
    if val >= 65: return f"Consumer sentiment at {val:.1f} is subdued — consumers are concerned about inflation, rates, or job security."
    return f"Consumer sentiment at {val:.1f} is depressed — deeply pessimistic consumers historically cut discretionary spending, weighing on GDP."

def _interp_retail_sales(val, change):
    if val is None: return "Retail sales data unavailable."
    if val > 2.0:  return f"Retail sales at {val:.1f}% MoM is robust — consumer spending remains a key engine of GDP growth."
    if val > 0.3:  return f"Retail sales at {val:.1f}% MoM is modestly positive — consumer spending is holding up despite macro headwinds."
    if val > -0.3: return f"Retail sales near flat {val:.1f}% MoM — consumers are pausing, possibly reflecting inflation fatigue or rate sensitivity."
    return f"Retail sales declined {val:.1f}% MoM — consumer demand is weakening, which will feed through to corporate earnings and GDP."

def _interp_savings(val, change):
    if val is None: return "Savings rate data unavailable."
    if val >= 8.0: return f"Personal savings rate at {val:.1f}% is healthy — consumers have a financial buffer, supporting future spending resilience."
    if val >= 5.0: return f"Personal savings rate at {val:.1f}% is near historical norms — consumers are neither over-extended nor especially cushioned."
    if val >= 3.0: return f"Personal savings rate at {val:.1f}% is below average — households are drawing down savings to maintain spending, which is unsustainable."
    return f"Personal savings rate at {val:.1f}% is critically low — consumers are financially stretched; any income shock could trigger sharp spending cuts."

def _interp_cc_delinquency(val, change):
    if val is None: return "Credit card delinquency data unavailable."
    if val < 2.0:  return f"Credit card delinquency at {val:.1f}% is low — consumer balance sheets are healthy with minimal credit stress."
    if val < 3.0:  return f"Credit card delinquency at {val:.1f}% is near pre-pandemic norms — modest stress but not systemic."
    if val < 4.5:  return f"Credit card delinquency at {val:.1f}% is elevated — lower-income households are increasingly unable to service debt obligations."
    return f"Credit card delinquency at {val:.1f}% is at crisis levels — widespread consumer credit stress signals potential for negative GDP feedback loop."

def _interp_housing_starts(val, change):
    if val is None: return "Housing starts data unavailable."
    th = val / 1000
    if th > 1.6: return f"Housing starts at {val:,.0f}K annualized is strong — homebuilding activity supports construction employment and GDP."
    if th > 1.3: return f"Housing starts at {val:,.0f}K annualized is at trend — housing market stable despite elevated mortgage rates."
    if th > 1.0: return f"Housing starts at {val:,.0f}K annualized is below trend — high mortgage rates and affordability concerns are suppressing new supply."
    return f"Housing starts at {val:,.0f}K annualized is deeply depressed — housing activity has effectively stalled under the weight of rate pressure."

def _interp_case_shiller(val, change):
    if val is None: return "Case-Shiller HPI data unavailable."
    if val > 8.0:  return f"Home prices up {val:.1f}% YoY — strong appreciation is sustaining wealth effect for homeowners but worsening affordability."
    if val > 4.0:  return f"Home prices up {val:.1f}% YoY — moderate appreciation consistent with inflation-adjusted stability."
    if val > 0:    return f"Home prices up {val:.1f}% YoY — prices barely keeping pace with inflation, reflecting affordability ceiling from high rates."
    return f"Home prices fell {abs(val):.1f}% YoY — declining home values erode household wealth and could pressure consumer spending."

def _interp_mortgage(val, change):
    if val is None: return "Mortgage rate data unavailable."
    if val >= 7.5: return f"30Y mortgage at {val:.2f}% is historically elevated — most existing homeowners are effectively locked in, suppressing transaction volume."
    if val >= 6.5: return f"30Y mortgage at {val:.2f}% remains well above post-GFC norms — affordability is significantly impaired for most prospective buyers."
    if val >= 5.5: return f"30Y mortgage at {val:.2f}% is elevated but gradually improving — housing affordability remains strained but showing early relief."
    return f"30Y mortgage at {val:.2f}% is approaching the post-pandemic lock-in rate — housing market mobility could begin to improve."

def _interp_hy_oas(val, change):
    if val is None: return "HY spread data unavailable."
    if val < 300:  return f"HY OAS at {val:.0f}bp is historically tight — credit markets pricing near-zero default risk, consistent with euphoric risk appetite."
    if val < 400:  return f"HY OAS at {val:.0f}bp is within normal range — credit stress is contained and corporate balance sheets remain healthy."
    if val < 550:  return f"HY OAS at {val:.0f}bp is elevated — markets beginning to price rising default risk as corporate cash flows face macro headwinds."
    if val < 800:  return f"HY OAS at {val:.0f}bp is wide — significant credit stress with potential for cascading defaults; reduce risk exposure."
    return f"HY OAS at {val:.0f}bp is at crisis levels — systemic distress across corporate credit markets; maximum caution warranted."

def _interp_ig_oas(val, change):
    if val is None: return "IG spread data unavailable."
    if val < 80:   return f"IG OAS at {val:.0f}bp is tight — investment grade credit is very well-bid, reflecting strong institutional demand for quality."
    if val < 130:  return f"IG OAS at {val:.0f}bp is within normal range — investment grade credit functioning normally with no systemic stress."
    if val < 200:  return f"IG OAS at {val:.0f}bp is elevated — IG credit showing stress; quality flight not yet fully priced in."
    return f"IG OAS at {val:.0f}bp is at crisis levels — even high-quality borrowers facing significant risk premium demands."

def _interp_breakeven(val, maturity_label):
    if val is None: return f"{maturity_label} breakeven data unavailable."
    if val > 3.5:  return f"{maturity_label} breakeven at {val:.2f}% — markets expect significantly above-target inflation over this horizon."
    if val > 2.5:  return f"{maturity_label} breakeven at {val:.2f}% — inflation expectations modestly elevated above the Fed's 2% target."
    if val > 2.0:  return f"{maturity_label} breakeven at {val:.2f}% — inflation expectations well-anchored near the Fed's target."
    return f"{maturity_label} breakeven at {val:.2f}% — markets expect inflation to undershoot target; potential deflationary risk priced in."

def _interp_real_yield(val, maturity_label):
    if val is None: return f"{maturity_label} real yield data unavailable."
    if val > 2.5:  return f"{maturity_label} real yield at {val:.2f}% is highly restrictive — after-inflation returns on cash and bonds are attractive alternatives to risk assets."
    if val > 1.5:  return f"{maturity_label} real yield at {val:.2f}% is positive and above neutral — monetary policy is meaningfully restrictive."
    if val > 0.5:  return f"{maturity_label} real yield at {val:.2f}% is modestly positive — policy is above neutral but not aggressively restrictive."
    if val > 0:    return f"{maturity_label} real yield at {val:.2f}% is near zero — policy is essentially neutral in real terms."
    return f"{maturity_label} real yield at {val:.2f}% is negative — financial conditions are still supportive despite nominal rate hikes."


# ── ORIGINAL SERIES DEFINITIONS ───────────────────────────────
MACRO_SERIES = [
    {"id":"gdp",          "fred_id":"GDPC1",    "label":"GDP GROWTH",     "description":"Real GDP QoQ Annualized",       "suffix":"%","decimals":1,"limit":6,  "calc":"qoq_annualized","positive_is_good":True},
    {"id":"cpi",          "fred_id":"CPIAUCSL", "label":"CPI YOY",        "description":"Consumer Price Index YoY",       "suffix":"%","decimals":1,"limit":36, "calc":"yoy",           "positive_is_good":False},
    {"id":"pce",          "fred_id":"PCEPILFE", "label":"CORE PCE YOY",   "description":"Core PCE Price Index YoY",       "suffix":"%","decimals":1,"limit":36, "calc":"yoy",           "positive_is_good":False},
    {"id":"unemployment", "fred_id":"UNRATE",   "label":"UNEMPLOYMENT",   "description":"Unemployment Rate",              "suffix":"%","decimals":1,"limit":3,  "calc":"latest",        "positive_is_good":False},
    {"id":"fed_funds",    "fred_id":"FEDFUNDS", "label":"FED FUNDS",      "description":"Effective Fed Funds Rate",       "suffix":"%","decimals":2,"limit":3,  "calc":"latest",        "positive_is_good":None},
    {"id":"m2",           "fred_id":"M2SL",     "label":"M2 YOY",         "description":"M2 Money Supply YoY",            "suffix":"%","decimals":1,"limit":36, "calc":"yoy",           "positive_is_good":True},
]

YIELD_SERIES = [
    {"id":"dgs1mo","fred_id":"DGS1MO","label":"1MO", "maturity":"1M"},
    {"id":"dgs3mo","fred_id":"DGS3MO","label":"3MO", "maturity":"3M"},
    {"id":"dgs6mo","fred_id":"DGS6MO","label":"6MO", "maturity":"6M"},
    {"id":"dgs1",  "fred_id":"DGS1",  "label":"1YR", "maturity":"1Y"},
    {"id":"dgs2",  "fred_id":"DGS2",  "label":"2YR", "maturity":"2Y"},
    {"id":"dgs5",  "fred_id":"DGS5",  "label":"5YR", "maturity":"5Y"},
    {"id":"dgs10", "fred_id":"DGS10", "label":"10YR","maturity":"10Y"},
    {"id":"dgs30", "fred_id":"DGS30", "label":"30YR","maturity":"30Y"},
]


# ── ECONOMY SERIES ────────────────────────────────────────────
ECONOMY_SERIES = {
    "growth": [
        {"id":"gdp",      "fred_id":"GDPC1",        "label":"GDP GROWTH",     "description":"Real GDP QoQ Annualized",     "suffix":"%","decimals":1,"limit":6,  "calc":"qoq_annualized","category":"growth","positive_is_good":True},
        {"id":"ism_proxy","fred_id":"MANEMP",        "label":"MFG EMPLOYMENT", "description":"Manufacturing Employment",     "suffix":"K","decimals":0,"limit":36, "calc":"latest",        "category":"growth","positive_is_good":True},
        {"id":"houst",    "fred_id":"HOUST",         "label":"HOUSING STARTS", "description":"Housing Starts (Ann. Rate)",   "suffix":"K","decimals":0,"limit":3,  "calc":"latest",        "category":"growth","positive_is_good":True},
    ],
    "inflation": [
        {"id":"cpi",      "fred_id":"CPIAUCSL",      "label":"CPI YOY",        "description":"Consumer Price Index YoY",     "suffix":"%","decimals":1,"limit":36, "calc":"yoy",           "category":"inflation","positive_is_good":False},
        {"id":"pce",      "fred_id":"PCEPILFE",      "label":"CORE PCE YOY",   "description":"Core PCE Price Index YoY",     "suffix":"%","decimals":1,"limit":36, "calc":"yoy",           "category":"inflation","positive_is_good":False},
        {"id":"t5yie",    "fred_id":"T5YIE",         "label":"5Y BREAKEVEN",   "description":"5-Year Breakeven Inflation",   "suffix":"%","decimals":2,"limit":3,  "calc":"latest",        "category":"inflation","positive_is_good":None},
        {"id":"t10yie",   "fred_id":"T10YIE",        "label":"10Y BREAKEVEN",  "description":"10-Year Breakeven Inflation",  "suffix":"%","decimals":2,"limit":3,  "calc":"latest",        "category":"inflation","positive_is_good":None},
    ],
    "labor": [
        {"id":"unemployment","fred_id":"UNRATE",     "label":"UNEMPLOYMENT",   "description":"Unemployment Rate",            "suffix":"%","decimals":1,"limit":3,  "calc":"latest",        "category":"labor","positive_is_good":False},
        {"id":"icsa",     "fred_id":"ICSA",          "label":"JOBLESS CLAIMS", "description":"Initial Jobless Claims",       "suffix":"","decimals":0,"limit":3,   "calc":"latest",        "category":"labor","positive_is_good":False},
        {"id":"jolts",    "fred_id":"JTSJOL",        "label":"JOLTS OPENINGS", "description":"Job Openings (Thousands)",     "suffix":"K","decimals":0,"limit":3,  "calc":"latest",        "category":"labor","positive_is_good":True},
    ],
    "consumer": [
        {"id":"umich",    "fred_id":"UMCSENT",       "label":"UMICH SENTIMENT","description":"Univ Michigan Sentiment",      "suffix":"","decimals":1, "limit":3,  "calc":"latest",        "category":"consumer","positive_is_good":True},
        {"id":"retail",   "fred_id":"RSXFS",         "label":"RETAIL SALES",   "description":"Retail Sales MoM %",          "suffix":"%","decimals":1,"limit":3,  "calc":"mom_pct",       "category":"consumer","positive_is_good":True},
        {"id":"savings",  "fred_id":"PSAVERT",       "label":"SAVINGS RATE",   "description":"Personal Savings Rate",        "suffix":"%","decimals":1,"limit":3,  "calc":"latest",        "category":"consumer","positive_is_good":True},
        {"id":"cc_delinq","fred_id":"DRCCLACBS",     "label":"CC DELINQUENCY", "description":"Credit Card Delinquency Rate", "suffix":"%","decimals":2,"limit":3,  "calc":"latest",        "category":"consumer","positive_is_good":False},
        {"id":"cs_hpi",   "fred_id":"CSUSHPISA",     "label":"CASE-SHILLER HPI","description":"Home Price Index YoY",        "suffix":"%","decimals":1,"limit":14, "calc":"yoy",           "category":"consumer","positive_is_good":True},
        {"id":"mortgage", "fred_id":"MORTGAGE30US",  "label":"MORTGAGE 30Y",   "description":"30-Year Mortgage Rate",        "suffix":"%","decimals":2,"limit":3,  "calc":"latest",        "category":"consumer","positive_is_good":False},
    ],
}

CREDIT_SERIES = {
    "spreads": [
        {"id":"hy_oas", "fred_id":"BAMLH0A0HYM2","label":"HY OAS",          "description":"ICE BofA US HY OAS","decimals":0,"calc":"latest","unit":"bp","scale":100},
        {"id":"ig_oas", "fred_id":"BAMLC0A0CM",  "label":"IG OAS",          "description":"ICE BofA US IG OAS","decimals":0,"calc":"latest","unit":"bp","scale":100},
    ],
    "breakevens": [
        {"id":"t5yie",  "fred_id":"T5YIE",        "label":"5Y BREAKEVEN",   "description":"5-Year Breakeven Inflation Rate", "decimals":2,"calc":"latest","unit":"%"},
        {"id":"t10yie", "fred_id":"T10YIE",        "label":"10Y BREAKEVEN",  "description":"10-Year Breakeven Inflation Rate","decimals":2,"calc":"latest","unit":"%"},
    ],
    "real_yields": [
        {"id":"dfii5",  "fred_id":"DFII5",         "label":"5Y REAL YIELD",  "description":"5-Year TIPS Real Yield",          "decimals":2,"calc":"latest","unit":"%"},
        {"id":"dfii10", "fred_id":"DFII10",         "label":"10Y REAL YIELD", "description":"10-Year TIPS Real Yield",         "decimals":2,"calc":"latest","unit":"%"},
    ],
    "falsification": [
        {"id":"core_pce",    "fred_id":"PCEPILFE",     "calc":"yoy",      "limit":16},
        {"id":"gdp_growth",  "fred_id":"GDPC1",        "calc":"qoq",      "limit":6},
        {"id":"hy_spreads",  "fred_id":"BAMLH0A0HYM2", "calc":"latest",   "limit":3},
        {"id":"productivity","fred_id":"OPHNFB",        "calc":"yoy",      "limit":14},
    ],
}


def _mom_pct(obs):
    valid = [o for o in obs if o.get("value") not in (".", "", None)]
    if len(valid) < 2: return None, None
    curr  = float(valid[0]["value"])
    prior = float(valid[1]["value"])
    if prior == 0: return None, None
    mom = ((curr - prior) / abs(prior)) * 100
    prior_mom = None
    if len(valid) >= 3:
        pp = float(valid[2]["value"])
        if pp != 0:
            prior_mom = ((prior - pp) / abs(pp)) * 100
    return round(mom, 2), (round(prior_mom, 2) if prior_mom is not None else None)


def _get_interpretation(series_id, current, change):
    """Route to the appropriate interpretation generator."""
    interps = {
        "gdp":        lambda c, ch: _interp_gdp(c, ch),
        "cpi":        lambda c, ch: _interp_cpi(c, ch),
        "pce":        lambda c, ch: _interp_pce(c, ch),
        "unemployment": lambda c, ch: _interp_unemployment(c, ch),
        "icsa":       lambda c, ch: _interp_claims(c, ch),
        "jolts":      lambda c, ch: _interp_jolts(c, ch),
        "umich":      lambda c, ch: _interp_umich(c, ch),
        "retail":     lambda c, ch: _interp_retail_sales(c, ch),
        "savings":    lambda c, ch: _interp_savings(c, ch),
        "cc_delinq":  lambda c, ch: _interp_cc_delinquency(c, ch),
        "houst":      lambda c, ch: _interp_housing_starts(c, ch),
        "cs_hpi":     lambda c, ch: _interp_case_shiller(c, ch),
        "mortgage":   lambda c, ch: _interp_mortgage(c, ch),
        "hy_oas":     lambda c, ch: _interp_hy_oas(c, ch),
        "ig_oas":     lambda c, ch: _interp_ig_oas(c, ch),
        "t5yie":      lambda c, ch: _interp_breakeven(c, "5-year"),
        "t10yie":     lambda c, ch: _interp_breakeven(c, "10-year"),
        "dfii5":      lambda c, ch: _interp_real_yield(c, "5-year"),
        "dfii10":     lambda c, ch: _interp_real_yield(c, "10-year"),
    }
    fn = interps.get(series_id)
    if fn:
        try:
            return fn(current, change)
        except Exception:
            pass
    return None


def _signal_word(series_id, current, positive_is_good):
    """Generate a one-word signal label."""
    if current is None: return "N/A"

    mapping = {
        "gdp":        [(3.0,"STRONG"),(2.0,"ABOVE TREND"),(1.0,"TREND"),(0.0,"SLOWING"),(-99,"CONTRACTION")],
        "cpi":        [(-99,"DEFLATION"),(2.0,"ON TARGET"),(3.5,"ELEVATED"),(5.0,"HIGH"),(99,"CRITICAL")],
        "pce":        [(-99,"DEFLATION"),(2.0,"ON TARGET"),(2.5,"ELEVATED"),(3.5,"HIGH"),(99,"CRITICAL")],
        "unemployment":[(-99,"VERY TIGHT"),(4.0,"TIGHT"),(5.5,"NORMAL"),(7.0,"ELEVATED"),(99,"HIGH")],
        "icsa":       [(-99,"VERY LOW"),(200000,"LOW"),(250000,"NORMAL"),(300000,"ELEVATED"),(99e9,"HIGH")],
        "jolts":      [(-99,"VERY LOW"),(6e6,"LOW"),(8e6,"NORMAL"),(10e6,"HIGH"),(99e9,"VERY HIGH")],
        "umich":      [(-99,"DEPRESSED"),(65,"PESSIMISTIC"),(75,"NEUTRAL"),(85,"OPTIMISTIC"),(99,"EUPHORIC")],
        "savings":    [(-99,"DISTRESSED"),(2.5,"VERY LOW"),(5.0,"LOW"),(8.0,"NORMAL"),(99,"HIGH")],
        "cc_delinq":  [(-99,"HEALTHY"),(2.0,"NORMAL"),(3.0,"ELEVATED"),(4.5,"HIGH"),(99,"CRISIS")],
        "mortgage":   [(-99,"VERY LOW"),(4.0,"LOW"),(5.5,"MODERATE"),(6.5,"HIGH"),(99,"ELEVATED")],
    }

    if series_id in mapping:
        levels = mapping[series_id]
        for threshold, label in levels:
            if current <= threshold:
                return label
        return levels[-1][1]

    if positive_is_good is None:
        return "DATA"
    return "POSITIVE" if (positive_is_good and current > 0) or (not positive_is_good and current < 0) else "WATCH"


# ── RECESSION PROBABILITY ─────────────────────────────────────
def _compute_recession_probability(indicators):
    from config import RECESSION_WEIGHTS, RECESSION_SIGNALS
    score = 0

    t10y2y = indicators.get("t10y2y")
    if t10y2y is not None:
        if t10y2y < -0.5:  score += RECESSION_WEIGHTS["yield_curve"]
        elif t10y2y < 0:   score += int(RECESSION_WEIGHTS["yield_curve"] * 0.6)
        elif t10y2y < 0.5: score += int(RECESSION_WEIGHTS["yield_curve"] * 0.2)

    gdp = indicators.get("gdp")
    if gdp is not None:
        if gdp < 0:   score += RECESSION_WEIGHTS["gdp"]
        elif gdp < 1: score += int(RECESSION_WEIGHTS["gdp"] * 0.6)
        elif gdp < 2: score += int(RECESSION_WEIGHTS["gdp"] * 0.2)

    hy_oas = indicators.get("hy_oas")
    if hy_oas is not None:
        if hy_oas > 600:   score += RECESSION_WEIGHTS["credit_spreads"]
        elif hy_oas > 450: score += int(RECESSION_WEIGHTS["credit_spreads"] * 0.6)
        elif hy_oas > 350: score += int(RECESSION_WEIGHTS["credit_spreads"] * 0.25)

    ur = indicators.get("unemployment")
    ur_prior = indicators.get("unemployment_prior")
    if ur is not None:
        ur_change = (ur - ur_prior) if (ur_prior is not None) else 0
        if ur >= 5.5:         score += RECESSION_WEIGHTS["unemployment"]
        elif ur_change > 0.5: score += int(RECESSION_WEIGHTS["unemployment"] * 0.6)
        elif ur_change > 0.2: score += int(RECESSION_WEIGHTS["unemployment"] * 0.3)

    score = min(100, score)
    for level in RECESSION_SIGNALS:
        if score <= level["max"]:
            return score, level["label"], level["color"]
    return score, "CRITICAL", "red"


# ── K-SHAPE DIVERGENCE ────────────────────────────────────────
def _compute_k_shape(indicators):
    from config import K_SHAPE as KS

    upper_score = 50
    lower_score = 50
    upper_signals = []
    lower_signals = []

    cs_hpi = indicators.get("cs_hpi")
    if cs_hpi is not None:
        if cs_hpi >= KS["cs_hpi_strong"]:
            upper_score += 20
            upper_signals.append(f"Home prices +{cs_hpi:.1f}% YoY — wealth effect active for property owners.")
        elif cs_hpi > 0:
            upper_score += 8
            upper_signals.append(f"Home prices +{cs_hpi:.1f}% YoY — modest appreciation preserving homeowner wealth.")
        else:
            upper_score -= 10
            upper_signals.append(f"Home prices {cs_hpi:.1f}% YoY — declining real estate wealth pressuring upper-income households.")

    cc = indicators.get("cc_delinquency")
    if cc is not None:
        if cc >= KS["cc_delinquency_crisis"]:
            lower_score -= 30
            lower_signals.append(f"CC delinquency at {cc:.1f}% — crisis-level consumer credit stress.")
        elif cc >= KS["cc_delinquency_stress"]:
            lower_score -= 18
            lower_signals.append(f"CC delinquency at {cc:.1f}% — elevated credit stress among lower-income consumers.")
        else:
            lower_score += 10
            lower_signals.append(f"CC delinquency at {cc:.1f}% — consumer credit quality healthy.")

    sr = indicators.get("savings_rate")
    if sr is not None:
        if sr <= KS["savings_rate_very_low"]:
            lower_score -= 20
            lower_signals.append(f"Savings rate at {sr:.1f}% — consumers effectively living paycheck to paycheck.")
        elif sr <= KS["savings_rate_low"]:
            lower_score -= 10
            lower_signals.append(f"Savings rate at {sr:.1f}% — below-average savings buffer limits consumer resilience.")
        else:
            lower_score += 8
            lower_signals.append(f"Savings rate at {sr:.1f}% — consumers maintaining adequate financial buffer.")

    umich = indicators.get("umich")
    if umich is not None:
        if umich < KS["umich_weak"]:
            lower_score -= 12
            lower_signals.append(f"Consumer sentiment at {umich:.1f} — pessimistic households likely to cut discretionary spending.")
        elif umich >= KS["umich_strong"]:
            lower_score += 8
            upper_score += 8
            lower_signals.append(f"Consumer sentiment at {umich:.1f} — optimism broadly shared across income groups.")

    mortgage = indicators.get("mortgage")
    if mortgage is not None:
        if mortgage >= 7.0:
            lower_score -= 10
            lower_signals.append(f"30Y mortgage at {mortgage:.2f}% — first-time buyers effectively priced out of the market.")
        elif mortgage <= 5.0:
            lower_score += 8
            lower_signals.append(f"30Y mortgage at {mortgage:.2f}% — improved affordability expanding homeownership access.")

    upper_score = max(0, min(100, upper_score))
    lower_score = max(0, min(100, lower_score))

    def sig(score):
        if score >= 65: return "HEALTHY"
        if score >= 45: return "NEUTRAL"
        if score >= 25: return "STRESSED"
        return "DISTRESSED"

    return {
        "upper": {"score": upper_score, "signal": sig(upper_score), "indicators": upper_signals[:3]},
        "lower": {"score": lower_score, "signal": sig(lower_score), "indicators": lower_signals[:4]},
        "divergence": upper_score - lower_score,
        "interpretation": (
            f"K-shape gap of {abs(upper_score - lower_score)} points — upper-income households are {sig(upper_score).lower()} "
            f"while lower-income households are {sig(lower_score).lower()}."
            if abs(upper_score - lower_score) > 15
            else f"Income cohorts showing similar economic conditions ({sig(upper_score).lower()}) — limited K-shape divergence."
        ),
    }


# ── FALSIFICATION TRIGGER EVALUATION ────────────────────────
def _eval_falsification_triggers():
    from config import FALSIFICATION_TRIGGERS
    results = []

    for trigger in FALSIFICATION_TRIGGERS:
        entry = {
            "id":           trigger["id"],
            "label":        trigger["label"],
            "full_label":   trigger["full_label"],
            "description":  trigger["description"],
            "threshold":    trigger["threshold"],
            "direction":    trigger["direction"],
            "unit":         trigger["unit"],
            "current_value":None,
            "progress_pct": 0,
            "status":       "UNAVAILABLE",
            "sustained_count": 0,
            "required":     trigger.get("sustained", 1),
            "met":          False,
        }

        if trigger["calc"] == "manual":
            entry["status"] = "MANUAL INPUT"
            results.append(entry)
            continue

        try:
            series_id = trigger["fred_series"]
            calc      = trigger["calc"]
            limit     = 16 if calc in ("yoy", "qoq") else 3

            obs = _fetch_series(series_id, limit=limit)

            if calc == "yoy":
                current, _ = _yoy_pct(obs)
            elif calc == "qoq":
                current, _ = _qoq_annualized(obs)
            elif calc == "latest_bp":
                raw = _latest_val(obs)
                current = round(raw * 100, 0) if raw is not None else None
            else:
                current = _latest_val(obs)

            if current is None:
                entry["status"] = "NO DATA"
                results.append(entry)
                continue

            entry["current_value"] = round(current, 2)
            threshold = trigger["threshold"]
            direction = trigger["direction"]

            if direction == "below":
                met = current < threshold
                if threshold != 0:
                    progress = max(0, min(100, (1 - (current - threshold) / abs(threshold)) * 100))
                else:
                    progress = 100 if met else 0
            else:
                met = current > threshold
                if threshold != 0:
                    progress = max(0, min(100, (current / threshold) * 100))
                else:
                    progress = 100 if met else 0

            entry["progress_pct"] = round(progress, 1)
            entry["met"]          = met

            # Check sustained periods
            sustained_count = 0
            required = trigger.get("sustained", 1)
            if required > 1 and calc == "yoy":
                for i in range(required):
                    if len(obs) >= i + 13:
                        c = float(obs[i]["value"])
                        ya = float(obs[i + 12]["value"])
                        if ya != 0:
                            v = ((c - ya) / abs(ya)) * 100
                            if direction == "below" and v < threshold:
                                sustained_count += 1
                            elif direction == "above" and v > threshold:
                                sustained_count += 1
                            else:
                                break
            elif required > 1 and calc == "qoq":
                for i in range(required):
                    if len(obs) >= i + 2:
                        c = float(obs[i]["value"])
                        p = float(obs[i + 1]["value"])
                        if p != 0:
                            v = ((c / p) ** 4 - 1) * 100
                            if direction == "above" and v > threshold:
                                sustained_count += 1
                            elif direction == "below" and v < threshold:
                                sustained_count += 1
                            else:
                                break
            else:
                sustained_count = 1 if met else 0

            entry["sustained_count"] = sustained_count
            trigger_met = sustained_count >= required

            if trigger_met:
                entry["status"] = "TRIGGERED"
            elif met:
                entry["status"] = f"MET ({sustained_count}/{required})"
            elif progress >= 80:
                entry["status"] = "APPROACHING"
            else:
                entry["status"] = "NOT MET"

        except Exception as e:
            log.warning(f"Falsification trigger eval failed [{trigger['id']}]: {e}")
            entry["status"] = "ERROR"

        results.append(entry)

    return results


# ── ECONOMIC CALENDAR ─────────────────────────────────────────
def get_economic_calendar():
    """
    Generate approximate economic calendar for the next 7 days.
    Based on standard federal release patterns — dates are approximate.
    """
    today  = datetime.utcnow().date()
    events = []

    for day_offset in range(8):
        d       = today + timedelta(days=day_offset)
        weekday = d.weekday()  # 0=Mon, 6=Sun
        day_ev  = []

        if weekday >= 5:  # Skip weekends
            continue

        # Every Thursday: Initial Jobless Claims
        if weekday == 3:
            day_ev.append({"time": "8:30 ET", "event": "INITIAL JOBLESS CLAIMS", "impact": "HIGH",   "note": "Weekly"})

        # First Friday of month: NFP
        if weekday == 4 and 1 <= d.day <= 7:
            day_ev.append({"time": "8:30 ET", "event": "NONFARM PAYROLLS",       "impact": "HIGH",   "note": "Monthly"})
            day_ev.append({"time": "8:30 ET", "event": "UNEMPLOYMENT RATE",      "impact": "HIGH",   "note": "Monthly"})

        # 2nd Wednesday of month: CPI (approx)
        if weekday == 2 and 8 <= d.day <= 14:
            day_ev.append({"time": "8:30 ET", "event": "CPI RELEASE",            "impact": "HIGH",   "note": "Approx"})
            day_ev.append({"time": "8:30 ET", "event": "CORE CPI",               "impact": "HIGH",   "note": "Approx"})

        # 2nd Thursday of month: PPI (approx)
        if weekday == 3 and 8 <= d.day <= 14:
            day_ev.append({"time": "8:30 ET", "event": "PPI RELEASE",            "impact": "MEDIUM", "note": "Approx"})

        # 2nd-3rd Wednesday: Retail Sales (approx)
        if weekday == 2 and 12 <= d.day <= 17:
            day_ev.append({"time": "8:30 ET", "event": "RETAIL SALES",           "impact": "HIGH",   "note": "Approx"})

        # 3rd Wednesday: Housing Starts (approx)
        if weekday == 2 and 15 <= d.day <= 21:
            day_ev.append({"time": "8:30 ET", "event": "HOUSING STARTS",         "impact": "MEDIUM", "note": "Approx"})

        # First business day of month: ISM Manufacturing
        if weekday < 5 and 1 <= d.day <= 3:
            day_ev.append({"time": "10:00 ET","event": "ISM MANUFACTURING",      "impact": "HIGH",   "note": "Monthly"})

        # 3rd business day of month: ISM Services (approx)
        if weekday < 5 and 3 <= d.day <= 5:
            day_ev.append({"time": "10:00 ET","event": "ISM SERVICES",           "impact": "HIGH",   "note": "Monthly"})

        # 2nd Tuesday: JOLTS (approx)
        if weekday == 1 and 8 <= d.day <= 14:
            day_ev.append({"time": "10:00 ET","event": "JOLTS JOB OPENINGS",     "impact": "HIGH",   "note": "Approx"})

        # Last Thursday: PCE (approx)
        if weekday == 3 and d.day >= 26:
            day_ev.append({"time": "8:30 ET", "event": "CORE PCE INFLATION",     "impact": "HIGH",   "note": "Approx"})
            day_ev.append({"time": "8:30 ET", "event": "PERSONAL INCOME/SPEND",  "impact": "MEDIUM", "note": "Approx"})

        # Last Friday: Univ Michigan Final Sentiment
        if weekday == 4 and d.day >= 26:
            day_ev.append({"time": "10:00 ET","event": "UMICH SENTIMENT FINAL",  "impact": "MEDIUM", "note": "Monthly"})

        # 2nd Friday: Univ Michigan Preliminary
        if weekday == 4 and 8 <= d.day <= 14:
            day_ev.append({"time": "10:00 ET","event": "UMICH SENTIMENT PRELIM", "impact": "MEDIUM", "note": "Monthly"})

        if day_ev:
            events.append({
                "date":        d.isoformat(),
                "day_of_week": d.strftime("%A").upper(),
                "day_display": d.strftime("%b %d").upper(),
                "events":      day_ev,
            })

    return events


# ── GET MACRO (existing) ──────────────────────────────────────
def get_macro():
    if _cache_valid("macro"):
        log.info("Macro: returning cached data.")
        return _cache["macro"]["data"]

    log.info("Macro: fetching fresh FRED data...")
    ts     = datetime.utcnow().isoformat()
    series = []
    errors = []

    for s in MACRO_SERIES:
        try:
            obs = _fetch_series(s["fred_id"], limit=s["limit"])
            if s["calc"] == "yoy":
                current, prior = _yoy_pct(obs)
            elif s["calc"] == "qoq_annualized":
                current, prior = _qoq_annualized(obs)
            else:
                current = _latest_val(obs)
                prior   = _prior_val(obs, offset=1)
            change = _safe_change(current, prior)
            series.append({
                "id": s["id"], "label": s["label"], "description": s["description"],
                "current": current, "prior": prior, "change": change,
                "direction": _direction(change), "suffix": s["suffix"],
                "decimals": s["decimals"], "positive_is_good": s["positive_is_good"],
                "as_of": _obs_date(obs),
            })
        except Exception as e:
            log.warning(f"Macro fetch failed [{s['fred_id']}]: {e}")
            errors.append(f"{s['label']}: {str(e)[:80]}")
            series.append({
                "id": s["id"], "label": s["label"], "description": s["description"],
                "current": None, "prior": None, "change": None,
                "direction": "FLAT", "suffix": s.get("suffix",""),
                "decimals": s.get("decimals",2), "positive_is_good": s.get("positive_is_good",True),
                "as_of": None,
            })

    result = {"series": series, "timestamp": ts, "errors": errors}
    _set_cache("macro", result)
    return result


# ── GET YIELDS (existing) ─────────────────────────────────────
def get_yields():
    if _cache_valid("yields"):
        log.info("Yields: returning cached data.")
        return _cache["yields"]["data"]

    log.info("Yields: fetching fresh FRED data...")
    ts     = datetime.utcnow().isoformat()
    yields = []
    errors = []
    raw    = {}

    for s in YIELD_SERIES:
        try:
            obs     = _fetch_series(s["fred_id"], limit=3)
            current = _latest_val(obs)
            prior   = _prior_val(obs, offset=1)
            change  = _safe_change(current, prior)
            raw[s["id"]] = current
            yields.append({
                "id": s["id"], "label": s["label"], "maturity": s["maturity"],
                "value": current, "prior": prior, "change": change,
                "direction": _direction(change), "as_of": _obs_date(obs),
            })
        except Exception as e:
            log.warning(f"Yield fetch failed [{s['fred_id']}]: {e}")
            errors.append(f"{s['label']}: {str(e)[:80]}")
            raw[s["id"]] = None
            yields.append({
                "id": s["id"], "label": s["label"], "maturity": s["maturity"],
                "value": None, "prior": None, "change": None,
                "direction": "FLAT", "as_of": None,
            })

    def spread(a_key, b_key, label):
        a = raw.get(a_key)
        b = raw.get(b_key)
        if a is None or b is None:
            return {"label": label, "value": None, "direction": "FLAT"}
        val = round(a - b, 3)
        return {"label": label, "value": val, "direction": _direction(val)}

    spreads = [
        spread("dgs10", "dgs2",   "10Y-2Y"),
        spread("dgs10", "dgs3mo", "10Y-3MO"),
        spread("dgs30", "dgs5",   "30Y-5Y"),
        spread("dgs5",  "dgs2",   "5Y-2Y"),
    ]

    t10y2y = None
    if raw.get("dgs10") and raw.get("dgs2"):
        t10y2y = raw["dgs10"] - raw["dgs2"]

    if t10y2y is None:     curve_status = "UNKNOWN"
    elif t10y2y < -0.1:   curve_status = "INVERTED"
    elif t10y2y < 0.25:   curve_status = "FLAT"
    else:                  curve_status = "NORMAL"

    result = {
        "yields": yields, "spreads": spreads,
        "curve_status": curve_status,
        "t10y2y": round(t10y2y, 3) if t10y2y is not None else None,
        "timestamp": ts, "errors": errors,
    }
    _set_cache("yields", result)
    return result


# ── GET ECONOMY (new) ─────────────────────────────────────────
def get_economy():
    if _cache_valid("economy"):
        log.info("Economy: returning cached data.")
        return _cache["economy"]["data"]

    log.info("Economy: fetching fresh FRED data...")
    ts = datetime.utcnow().isoformat()

    categories = {}
    raw_vals   = {}
    all_errors = []

    for cat_name, cat_series in ECONOMY_SERIES.items():
        cat_items = []
        for s in cat_series:
            entry = {
                "id": s["id"], "label": s["label"], "description": s["description"],
                "suffix": s["suffix"], "decimals": s["decimals"],
                "positive_is_good": s["positive_is_good"], "category": s["category"],
                "current": None, "prior": None, "change": None,
                "direction": "FLAT", "signal": "N/A", "interpretation": None, "as_of": None,
            }
            try:
                obs = _fetch_series(s["fred_id"], limit=s["limit"])

                if s["calc"] == "yoy":
                    current, prior = _yoy_pct(obs)
                elif s["calc"] == "qoq_annualized":
                    current, prior = _qoq_annualized(obs)
                elif s["calc"] == "mom_pct":
                    current, prior = _mom_pct(obs)
                else:
                    current = _latest_val(obs)
                    prior   = _prior_val(obs, offset=1)

                change = _safe_change(current, prior)
                entry.update({
                    "current":         current,
                    "prior":           prior,
                    "change":          change,
                    "direction":       _direction(change),
                    "signal":          _signal_word(s["id"], current, s["positive_is_good"]),
                    "interpretation":  _get_interpretation(s["id"], current, change),
                    "as_of":           _obs_date(obs),
                })
                raw_vals[s["id"]] = current

            except Exception as e:
                log.warning(f"Economy fetch failed [{s['fred_id']}]: {e}")
                all_errors.append(f"{s['label']}: {str(e)[:80]}")

            cat_items.append(entry)
        categories[cat_name] = cat_items

    # Build recession probability input bag
    recession_inputs = {
        "gdp":               raw_vals.get("gdp"),
        "unemployment":      raw_vals.get("unemployment"),
        "unemployment_prior":raw_vals.get("unemployment"),  # placeholder; same series
        "hy_oas":            None,  # pulled from credit cache if available
    }
    try:
        yields_data = get_yields()
        recession_inputs["t10y2y"] = yields_data.get("t10y2y")
    except Exception:
        pass

    # Try to pull HY OAS from a quick FRED fetch
    try:
        hy_obs = _fetch_series("BAMLH0A0HYM2", limit=3)
        hy_raw = _latest_val(hy_obs)
        recession_inputs["hy_oas"] = hy_raw * 100 if hy_raw is not None else None
    except Exception:
        pass

    recession_prob, recession_signal, recession_color = _compute_recession_probability(recession_inputs)
    recession_interp = (
        f"Composite model scores recession risk at {recession_prob}% — "
        + {
            "LOW":      "leading indicators are broadly constructive with no near-term contraction signal.",
            "MODERATE": "some caution warranted but no imminent recession trigger visible.",
            "ELEVATED": "multiple warning flags active; above-average probability of contraction within 12 months.",
            "HIGH":     "significant recession risk; historical analogs suggest contraction is more likely than not.",
            "CRITICAL": "severe deterioration across all leading indicators; recession conditions effectively present.",
        }.get(recession_signal, "data insufficient for definitive assessment.")
    )

    k_shape_inputs = {
        "cs_hpi":        raw_vals.get("cs_hpi"),
        "cc_delinquency":raw_vals.get("cc_delinq"),
        "savings_rate":  raw_vals.get("savings"),
        "umich":         raw_vals.get("umich"),
        "mortgage":      raw_vals.get("mortgage"),
    }
    k_shape = _compute_k_shape(k_shape_inputs)

    result = {
        "growth":   categories.get("growth",  []),
        "inflation":categories.get("inflation",[]),
        "labor":    categories.get("labor",   []),
        "consumer": categories.get("consumer",[]),
        "recession_probability": recession_prob,
        "recession_signal":      recession_signal,
        "recession_color":       recession_color,
        "recession_interpretation": recession_interp,
        "k_shape":  k_shape,
        "timestamp":ts,
        "errors":   all_errors,
    }

    _set_cache("economy", result)
    log.info(f"Economy: fetched — recession_prob={recession_prob}%, k_shape divergence={k_shape['divergence']}")
    return result


# ── GET CREDIT (new) ──────────────────────────────────────────
def get_credit():
    if _cache_valid("credit"):
        log.info("Credit: returning cached data.")
        return _cache["credit"]["data"]

    log.info("Credit: fetching fresh FRED data...")
    ts = datetime.utcnow().isoformat()
    errors = []

    def fetch_item(s):
        entry = {
            "id": s["id"], "label": s["label"], "description": s["description"],
            "decimals": s["decimals"], "unit": s.get("unit", ""),
            "value": None, "prior_value": None, "change": None,
            "direction": "FLAT", "interpretation": None, "as_of": None,
            "signal": "N/A", "signal_color": "muted",
        }
        try:
            obs = _fetch_series(s["fred_id"], limit=3)
            raw     = _latest_val(obs)
            raw_pri = _prior_val(obs, offset=1)
            scale   = s.get("scale", 1)
            current = round(raw * scale, s["decimals"]) if raw is not None else None
            prior   = round(raw_pri * scale, s["decimals"]) if raw_pri is not None else None
            change  = _safe_change(current, prior)
            entry.update({
                "value":        current,
                "prior_value":  prior,
                "change":       change,
                "direction":    _direction(change),
                "interpretation": _get_interpretation(s["id"], current, change),
                "as_of":        _obs_date(obs),
            })

            # Signal for spreads
            if s.get("unit") == "bp" and current is not None:
                from config import CREDIT_THRESHOLDS as CT
                key_pre = "hy" if "hy" in s["id"] else "ig"
                if current < CT[f"{key_pre}_tight"]:
                    entry["signal"] = "TIGHT"; entry["signal_color"] = "green"
                elif current < CT[f"{key_pre}_normal"]:
                    entry["signal"] = "NORMAL"; entry["signal_color"] = "amber"
                elif current < CT[f"{key_pre}_wide"]:
                    entry["signal"] = "WIDE"; entry["signal_color"] = "red"
                else:
                    entry["signal"] = "CRISIS"; entry["signal_color"] = "red"

        except Exception as e:
            log.warning(f"Credit fetch failed [{s['fred_id']}]: {e}")
            errors.append(f"{s['label']}: {str(e)[:80]}")
        return entry

    spreads     = [fetch_item(s) for s in CREDIT_SERIES["spreads"]]
    breakevens  = [fetch_item(s) for s in CREDIT_SERIES["breakevens"]]
    real_yields = [fetch_item(s) for s in CREDIT_SERIES["real_yields"]]
    triggers    = _eval_falsification_triggers()

    # Pull full yield curve for credit tab display
    try:
        yields_data = get_yields()
    except Exception:
        yields_data = {"yields": [], "spreads": [], "curve_status": "UNKNOWN", "t10y2y": None}

    result = {
        "spreads":      spreads,
        "breakevens":   breakevens,
        "real_yields":  real_yields,
        "falsification_triggers": triggers,
        "yield_curve":  yields_data,
        "timestamp":    ts,
        "errors":       errors,
    }

    _set_cache("credit", result)
    log.info(f"Credit: fetched — {len(spreads)} spreads, {len(triggers)} triggers evaluated.")
    return result


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    print("=== MACRO ===")
    print(json.dumps(get_macro(), indent=2))
    print("\n=== YIELDS ===")
    print(json.dumps(get_yields(), indent=2))
    print("\n=== ECONOMY ===")
    print(json.dumps(get_economy(), indent=2))
    print("\n=== CREDIT ===")
    print(json.dumps(get_credit(), indent=2))
    print("\n=== CALENDAR ===")
    print(json.dumps(get_economic_calendar(), indent=2))
