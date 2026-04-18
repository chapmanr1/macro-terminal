# FILE: main.py
# Bloomberg Macro Terminal — Flask Entry Point

import os
import threading
import time
import logging
from flask import Flask, jsonify, render_template, request
from datetime import datetime

try:
    from regime_engine import get_regime
except ImportError:
    def get_regime():
        return {"label": "UNAVAILABLE", "confidence_score": 0,
                "indicator_breakdown": [], "key_risks": ["Regime engine not loaded"],
                "asset_class_positioning": [], "timestamp": datetime.utcnow().isoformat()}

try:
    from fred_data import get_macro, get_yields, get_economy, get_credit, get_economic_calendar
except ImportError:
    def get_macro():
        return {"series": [], "timestamp": datetime.utcnow().isoformat(), "error": "FRED module not loaded"}
    def get_yields():
        return {"yields": [], "spreads": [], "timestamp": datetime.utcnow().isoformat(), "error": "FRED module not loaded"}
    def get_economy():
        return {"growth": [], "inflation": [], "labor": [], "consumer": [],
                "timestamp": datetime.utcnow().isoformat(), "error": "Economy module not loaded"}
    def get_credit():
        return {"spreads": [], "breakevens": [], "real_yields": [], "falsification_triggers": [],
                "timestamp": datetime.utcnow().isoformat(), "error": "Credit module not loaded"}
    def get_economic_calendar():
        return []

try:
    from news_feed import get_news
except ImportError:
    def get_news():
        return {"articles": [], "timestamp": datetime.utcnow().isoformat(), "error": "News module not loaded"}

try:
    from market_data import get_market
except ImportError:
    def get_market():
        return {"indices": [], "futures": [], "sectors": [], "commodities": [], "currencies": [],
                "timestamp": datetime.utcnow().isoformat(), "error": "Market module not loaded"}

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── ROUTES ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "ts": datetime.utcnow().isoformat()})

@app.route("/api/regime")
def api_regime():
    try:
        return jsonify(get_regime())
    except Exception as e:
        log.error(f"Regime error: {e}")
        return jsonify({"error": str(e), "label": "ERROR", "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/macro")
def api_macro():
    try:
        return jsonify(get_macro())
    except Exception as e:
        log.error(f"Macro error: {e}")
        return jsonify({"error": str(e), "series": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/yields")
def api_yields():
    try:
        return jsonify(get_yields())
    except Exception as e:
        log.error(f"Yields error: {e}")
        return jsonify({"error": str(e), "yields": [], "spreads": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/news")
def api_news():
    try:
        return jsonify(get_news())
    except Exception as e:
        log.error(f"News error: {e}")
        return jsonify({"error": str(e), "articles": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/market")
def api_market():
    try:
        return jsonify(get_market())
    except Exception as e:
        log.error(f"Market error: {e}")
        return jsonify({"error": str(e), "indices": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/economy")
def api_economy():
    try:
        return jsonify(get_economy())
    except Exception as e:
        log.error(f"Economy error: {e}")
        return jsonify({"error": str(e), "growth": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/credit")
def api_credit():
    try:
        return jsonify(get_credit())
    except Exception as e:
        log.error(f"Credit error: {e}")
        return jsonify({"error": str(e), "spreads": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/calendar")
def api_calendar():
    try:
        return jsonify({"events": get_economic_calendar(), "timestamp": datetime.utcnow().isoformat()})
    except Exception as e:
        log.error(f"Calendar error: {e}")
        return jsonify({"error": str(e), "events": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/watchlist")
def api_watchlist():
    try:
        tickers_str = request.args.get('tickers', '')
        if not tickers_str:
            return jsonify({"items": [], "timestamp": datetime.utcnow().isoformat()})
        tickers = [t.strip().upper() for t in tickers_str.split(',') if t.strip()][:20]
        import yfinance as yf
        items = []
        for ticker in tickers:
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                price = getattr(info, 'last_price', None)
                prev  = getattr(info, 'previous_close', None)
                chg   = round(price - prev, 2) if price and prev else None
                pct   = round(chg / prev * 100, 2) if chg and prev else None
                items.append({
                    "symbol":     ticker,
                    "price":      round(price, 2) if price else None,
                    "change":     chg,
                    "pct_change": pct,
                    "direction":  "UP" if chg and chg > 0 else "DOWN" if chg and chg < 0 else "FLAT",
                })
            except Exception as e:
                items.append({"symbol": ticker, "error": str(e)[:60]})
        return jsonify({"items": items, "timestamp": datetime.utcnow().isoformat()})
    except Exception as e:
        log.error(f"Watchlist error: {e}")
        return jsonify({"error": str(e), "items": [], "timestamp": datetime.utcnow().isoformat()}), 500

@app.route("/api/health")
def api_health():
    health = {"status": "ok", "timestamp": datetime.utcnow().isoformat(), "modules": {}}
    for name, fn in [("regime", get_regime), ("macro", get_macro), ("yields", get_yields),
                     ("news", get_news), ("market", get_market), ("economy", get_economy),
                     ("credit", get_credit)]:
        try:
            fn()
            health["modules"][name] = "ok"
        except Exception as e:
            health["modules"][name] = str(e)
            health["status"] = "degraded"
    return jsonify(health)

# ── INTERNAL KEEP-ALIVE ───────────────────────────────────────
def internal_keepalive():
    import urllib.request
    replit_url = os.environ.get("REPLIT_URL", "")
    if not replit_url:
        log.info("REPLIT_URL not set — internal keep-alive disabled.")
        return
    while True:
        try:
            urllib.request.urlopen(f"{replit_url}/ping", timeout=10)
            log.info("Keep-alive ping sent.")
        except Exception as e:
            log.warning(f"Keep-alive failed: {e}")
        time.sleep(240)

if __name__ == "__main__":
    ka_thread = threading.Thread(target=internal_keepalive, daemon=True)
    ka_thread.start()
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting Macro Terminal on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
