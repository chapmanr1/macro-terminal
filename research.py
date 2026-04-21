# FILE: research.py
# Bloomberg Macro Terminal — Research Panel Backend

import math
import time
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_search_cache = {}
_ticker_cache = {}
SEARCH_TTL = 300
TICKER_TTL = 300


def search_tickers(query):
    q = query.strip().upper()
    if not q:
        return []
    now = time.time()
    if q in _search_cache and (now - _search_cache[q]['ts']) < SEARCH_TTL:
        return _search_cache[q]['data']
    try:
        import yfinance as yf
        results = []

        # Direct ticker lookup
        try:
            t = yf.Ticker(q)
            fi = t.fast_info
            price = getattr(fi, 'last_price', None)
            if price:
                info = t.info
                results.append({
                    'symbol': q,
                    'name': info.get('shortName') or info.get('longName', q),
                    'exchange': info.get('exchange', ''),
                    'type': info.get('quoteType', ''),
                    'price': round(price, 2),
                })
        except Exception:
            pass

        # yfinance search
        try:
            search = yf.Search(q, max_results=8)
            for item in (search.quotes or []):
                sym = item.get('symbol', '')
                if sym and sym not in [r['symbol'] for r in results]:
                    results.append({
                        'symbol': sym,
                        'name': item.get('shortname') or item.get('longname', ''),
                        'exchange': item.get('exchange', ''),
                        'type': item.get('quoteType', ''),
                        'price': None,
                    })
                if len(results) >= 8:
                    break
        except Exception:
            pass

        _search_cache[q] = {'data': results, 'ts': now}
        return results
    except Exception as e:
        log.warning(f"Search failed for {q}: {e}")
        return []


def calculate_graham_number(eps, bvps):
    if eps is None or bvps is None or eps <= 0 or bvps <= 0:
        return None
    return round(math.sqrt(22.5 * eps * bvps), 2)


def calculate_scores(info, price):
    graham_score = 0
    buffett_score = 0
    graham_notes = []
    buffett_notes = []

    pe           = info.get('trailingPE') or info.get('forwardPE')
    pb           = info.get('priceToBook')
    div_yield    = info.get('dividendYield') or 0
    debt_equity  = info.get('debtToEquity')
    roe          = info.get('returnOnEquity')
    eps          = info.get('trailingEps')
    bvps         = info.get('bookValue')
    rev_growth   = info.get('revenueGrowth')
    gross_margin = info.get('grossMargins')

    # Graham (0-6)
    if pb is not None:
        if pb < 1.5:
            graham_score += 2
            graham_notes.append(f'P/B {pb:.2f} < 1.5 \u2713')
        else:
            graham_notes.append(f'P/B {pb:.2f} > 1.5 \u2717')
    if pe is not None:
        if pe < 15:
            graham_score += 2
            graham_notes.append(f'P/E {pe:.1f} < 15 \u2713')
        else:
            graham_notes.append(f'P/E {pe:.1f} > 15 \u2717')
    if div_yield and div_yield > 0:
        graham_score += 1
        graham_notes.append(f'Dividend yield {div_yield*100:.1f}% \u2713')
    else:
        graham_notes.append('No dividend \u2717')
    if debt_equity is not None:
        if debt_equity < 50:
            graham_score += 1
            graham_notes.append(f'D/E {debt_equity/100:.2f}x < 0.5 \u2713')
        else:
            graham_notes.append(f'D/E {debt_equity/100:.2f}x > 0.5 \u2717')

    # Buffett (0-6)
    if roe is not None:
        if roe > 0.15:
            buffett_score += 2
            buffett_notes.append(f'ROE {roe*100:.1f}% > 15% \u2713')
        else:
            buffett_notes.append(f'ROE {roe*100:.1f}% < 15% \u2717')
    if eps is not None:
        if eps > 0:
            buffett_score += 1
            buffett_notes.append('Positive EPS \u2713')
        else:
            buffett_notes.append('Negative EPS \u2717')
    if debt_equity is not None:
        if debt_equity < 100:
            buffett_score += 1
            buffett_notes.append('Manageable debt \u2713')
        else:
            buffett_notes.append('High debt \u2717')
    if gross_margin is not None:
        if gross_margin > 0.25:
            buffett_score += 1
            buffett_notes.append(f'Gross margin {gross_margin*100:.1f}% > 25% \u2713')
        else:
            buffett_notes.append(f'Gross margin {gross_margin*100:.1f}% < 25% \u2717')
    if rev_growth is not None:
        if rev_growth > 0.05:
            buffett_score += 1
            buffett_notes.append(f'Revenue growth {rev_growth*100:.1f}% > 5% \u2713')
        else:
            buffett_notes.append(f'Revenue growth {rev_growth*100:.1f}% < 5% \u2717')

    value_score = round((graham_score / 6 * 50) + (buffett_score / 6 * 50))

    if value_score >= 80:
        verdict, verdict_color = 'DEEP VALUE', 'green'
    elif value_score >= 60:
        verdict, verdict_color = 'VALUE', 'green'
    elif value_score >= 40:
        verdict, verdict_color = 'FAIR', 'amber'
    elif value_score >= 20:
        verdict, verdict_color = 'EXPENSIVE', 'red'
    else:
        verdict, verdict_color = 'AVOID', 'red'

    graham_number = calculate_graham_number(eps, bvps)
    graham_margin = None
    if graham_number and price:
        graham_margin = round((graham_number - price) / price * 100, 1)

    return {
        'graham_score':            graham_score,
        'buffett_score':           buffett_score,
        'value_score':             value_score,
        'verdict':                 verdict,
        'verdict_color':           verdict_color,
        'graham_number':           graham_number,
        'graham_margin_of_safety': graham_margin,
        'graham_notes':            graham_notes,
        'buffett_notes':           buffett_notes,
    }


def get_ticker_analysis(symbol):
    sym = symbol.strip().upper()
    now = time.time()
    if sym in _ticker_cache and (now - _ticker_cache[sym]['ts']) < TICKER_TTL:
        return _ticker_cache[sym]['data']
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        info = t.info

        price = info.get('currentPrice') or info.get('regularMarketPrice')
        if not price:
            fi = t.fast_info
            price = getattr(fi, 'last_price', None)

        scores = calculate_scores(info, price)

        result = {
            'symbol':        sym,
            'name':          info.get('shortName') or info.get('longName', sym),
            'sector':        info.get('sector', '--'),
            'industry':      info.get('industry', '--'),
            'exchange':      info.get('exchange', ''),
            'market_cap':    info.get('marketCap'),
            'price':         round(price, 2) if price else None,
            'pe':            info.get('trailingPE'),
            'forward_pe':    info.get('forwardPE'),
            'pb':            info.get('priceToBook'),
            'ps':            info.get('priceToSalesTrailing12Months'),
            'ev_ebitda':     info.get('enterpriseToEbitda'),
            'roe':           info.get('returnOnEquity'),
            'roa':           info.get('returnOnAssets'),
            'gross_margins': info.get('grossMargins'),
            'profit_margins':info.get('profitMargins'),
            'revenue_growth':info.get('revenueGrowth'),
            'earnings_growth':info.get('earningsGrowth'),
            'debt_equity':   info.get('debtToEquity'),
            'current_ratio': info.get('currentRatio'),
            'eps':           info.get('trailingEps'),
            'forward_eps':   info.get('forwardEps'),
            'bvps':          info.get('bookValue'),
            'div_yield':     info.get('dividendYield'),
            'beta':          info.get('beta'),
            '52w_high':      info.get('fiftyTwoWeekHigh'),
            '52w_low':       info.get('fiftyTwoWeekLow'),
            'analyst_target':info.get('targetMeanPrice'),
            'recommendation':info.get('recommendationKey', '').upper(),
            **scores,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

        _ticker_cache[sym] = {'data': result, 'ts': now}
        return result
    except Exception as e:
        log.warning(f"Ticker analysis failed for {symbol}: {e}")
        return {'symbol': sym, 'error': str(e), 'timestamp': datetime.now(timezone.utc).isoformat()}


def get_watchlist_prices(tickers):
    results = []
    for sym in tickers[:20]:
        try:
            import yfinance as yf
            t = yf.Ticker(sym)
            fi = t.fast_info
            price = getattr(fi, 'last_price', None)
            prev  = getattr(fi, 'previous_close', None)
            chg   = round(price - prev, 2) if price and prev else None
            pct   = round(chg / prev * 100, 2) if chg and prev else None
            results.append({
                'symbol':     sym,
                'price':      round(price, 2) if price else None,
                'change':     chg,
                'pct_change': pct,
                'direction':  'UP' if chg and chg > 0 else 'DOWN' if chg and chg < 0 else 'FLAT',
            })
        except Exception as e:
            results.append({'symbol': sym, 'error': str(e)[:60]})
    return results
