"""Ophalen van 5-minuten candles en derivatencontext voor BTC perpetual futures.

Bronnen (publieke endpoints, geen API-key nodig):
- okx      : BTC-USDT-SWAP (standaard)
- kraken   : PF_XBTUSD perpetual van Kraken Futures (reserve; bereikbaar vanaf GitHub-servers)
- coinbase : BTC-USD spot (laatste reserve; spot ligt dicht bij de perpetual, maar is geen future)
- binance  : BTCUSDT USDⓈ-M perpetual (niet bereikbaar vanuit sommige landen/cloud-regio's)
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

USER_AGENT = "btc5m-analyst/0.1"
OKX = "https://www.okx.com"
BINANCE = "https://fapi.binance.com"
KRAKEN_F = "https://futures.kraken.com"
COINBASE = "https://api.exchange.coinbase.com"


def _get(url: str, params: dict | None = None, retries: int = 3) -> dict | list:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as err:  # netwerk-/HTTP-fouten: kort wachten en opnieuw
            last_err = err
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Ophalen mislukt: {url} ({last_err})")


def _frame(rows: list[list], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    df["time"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df = df.drop_duplicates("time").sort_values("time").set_index("time")
    return df[["open", "high", "low", "close", "volume"]]


def fetch_okx(bars: int = 2000, inst: str = "BTC-USDT-SWAP") -> pd.DataFrame:
    """Laatste `bars` afgesloten 5m candles van OKX. Volume = BTC (volCcy)."""
    cols = ["ts", "open", "high", "low", "close", "contracts", "volume", "quote_volume", "confirm"]
    first = _get(f"{OKX}/api/v5/market/candles", {"instId": inst, "bar": "5m", "limit": 300})
    rows = [r for r in first["data"] if r[8] == "1"]  # alleen afgesloten candles
    while len(rows) < bars:
        oldest = min(int(r[0]) for r in rows)
        page = _get(f"{OKX}/api/v5/market/history-candles",
                    {"instId": inst, "bar": "5m", "limit": 100, "after": oldest})["data"]
        if not page:
            break
        rows.extend(page)
        time.sleep(0.12)  # ruim binnen de rate limit (20 req / 2 s)
    return _frame(rows, cols).tail(bars)


def fetch_binance(bars: int = 2000, symbol: str = "BTCUSDT") -> pd.DataFrame:
    cols = ["ts", "open", "high", "low", "close", "volume", "close_ts", "quote_volume",
            "trades", "taker_base", "taker_quote", "ignore"]
    rows: list[list] = []
    end: int | None = None
    while len(rows) < bars + 1:
        params = {"symbol": symbol, "interval": "5m", "limit": 1500}
        if end is not None:
            params["endTime"] = end
        page = _get(f"{BINANCE}/fapi/v1/klines", params)
        if not page:
            break
        rows = page + rows
        end = int(page[0][0]) - 1
    df = _frame(rows, cols)
    return df.iloc[:-1].tail(bars)  # laatste candle is nog niet afgesloten


def _closed(df: pd.DataFrame) -> pd.DataFrame:
    """Laat de lopende (nog niet afgesloten) candle weg."""
    return df[df.index + pd.Timedelta(minutes=5) <= pd.Timestamp.now(tz="UTC")]


def fetch_kraken(bars: int = 2000, symbol: str = "PF_XBTUSD") -> pd.DataFrame:
    rows: list[list] = []
    end = int(time.time())
    while len(rows) < bars + 1:
        start = end - 2000 * 300
        page = _get(f"{KRAKEN_F}/api/charts/v1/trade/{symbol}/5m", {"from": start, "to": end})["candles"]
        if not page:
            break
        rows = [[c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"]] for c in page] + rows
        end = start - 1
        time.sleep(0.2)
    df = _frame(rows, ["ts", "open", "high", "low", "close", "volume"])
    return _closed(df).tail(bars)


def fetch_coinbase(bars: int = 2000, product: str = "BTC-USD") -> pd.DataFrame:
    rows: list[list] = []
    end = pd.Timestamp.now(tz="UTC").floor("5min")
    while len(rows) < bars + 1:
        start = end - pd.Timedelta(minutes=5 * 299)
        page = _get(f"{COINBASE}/products/{product}/candles",
                    {"granularity": 300, "start": start.isoformat(), "end": end.isoformat()})
        if not page:
            break
        # Coinbase: [time(s), low, high, open, close, volume], nieuwste eerst
        rows += [[int(r[0]) * 1000, r[3], r[2], r[1], r[4], r[5]] for r in page]
        end = start - pd.Timedelta(minutes=5)
        time.sleep(0.15)
    df = _frame(rows, ["ts", "open", "high", "low", "close", "volume"])
    return _closed(df).tail(bars)


def fetch_candles(bars: int = 2000, source: str = "okx") -> pd.DataFrame:
    fetchers = {"okx": fetch_okx, "binance": fetch_binance, "kraken": fetch_kraken, "coinbase": fetch_coinbase}
    if source not in fetchers:
        raise ValueError(f"Onbekende bron: {source}")
    return fetchers[source](bars)


def fetch_derivatives_context(source: str = "okx") -> dict:
    """Funding rate, open interest en premium (best effort; lege dict bij fouten)."""
    try:
        if source == "okx":
            fr = _get(f"{OKX}/api/v5/public/funding-rate", {"instId": "BTC-USDT-SWAP"})["data"][0]
            oi = _get(f"{OKX}/api/v5/public/open-interest",
                      {"instType": "SWAP", "instId": "BTC-USDT-SWAP"})["data"][0]
            return {
                "funding_rate": float(fr["fundingRate"]),
                "next_funding_time": pd.to_datetime(int(fr["fundingTime"]), unit="ms", utc=True).isoformat(),
                "premium": float(fr["premium"]) if fr.get("premium") else None,
                "open_interest_btc": float(oi["oiCcy"]),
                "open_interest_usd": float(oi["oiUsd"]),
            }
        if source == "kraken":
            t = next(x for x in _get(f"{KRAKEN_F}/derivatives/api/v3/tickers")["tickers"] if x.get("symbol") == "PF_XBTUSD")
            mark = float(t["markPrice"])
            # Funding van Kraken heeft een andere eenheid dan OKX; liever weglaten dan verkeerd tonen
            return {
                "funding_rate": None,
                "next_funding_time": None, "premium": float(t["markPrice"]) / float(t["indexPrice"]) - 1,
                "open_interest_btc": float(t["openInterest"]), "open_interest_usd": float(t["openInterest"]) * mark,
            }
        if source == "binance":
            pi = _get(f"{BINANCE}/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})
            oi = _get(f"{BINANCE}/fapi/v1/openInterest", {"symbol": "BTCUSDT"})
            mark = float(pi["markPrice"])
            return {
                "funding_rate": float(pi["lastFundingRate"]),
                "next_funding_time": pd.to_datetime(int(pi["nextFundingTime"]), unit="ms", utc=True).isoformat(),
                "premium": mark / float(pi["indexPrice"]) - 1,
                "open_interest_btc": float(oi["openInterest"]),
                "open_interest_usd": float(oi["openInterest"]) * mark,
            }
    except Exception as err:
        return {"error": str(err)}
    return {}


def fetch_orderflow(inst: str = "BTC-USDT-SWAP") -> pd.DataFrame:
    """Orderflow per 5m van OKX (publiek). OKX bewaart dit maar 8-48 uur, dus de geplande run
    slaat het elke keer op en bouwt zo een eigen, groeiende dataset op.
    Kolommen: taker_buy, taker_sell (agressieve koop/verkoop), oi_ccy (open interest, BTC), ls_ratio."""
    tv = _get(f"{OKX}/api/v5/rubik/stat/taker-volume-contract", {"instId": inst, "period": "5m", "limit": 100})["data"]
    oi = _get(f"{OKX}/api/v5/rubik/stat/contracts/open-interest-history", {"instId": inst, "period": "5m", "limit": 100})["data"]
    ls = _get(f"{OKX}/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": "BTC", "period": "5m"})["data"]
    idx = lambda rows: pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)  # noqa: E731
    f = pd.DataFrame({"taker_sell": [float(r[1]) for r in tv], "taker_buy": [float(r[2]) for r in tv]}, index=idx(tv))
    f = f.join(pd.DataFrame({"oi_ccy": [float(r[2]) for r in oi]}, index=idx(oi)), how="outer")
    f = f.join(pd.DataFrame({"ls_ratio": [float(r[1]) for r in ls]}, index=idx(ls)), how="outer")
    now_bucket = pd.Timestamp.now(tz="UTC").floor("5min")
    f = f[f.index < now_bucket]  # de lopende 5 minuten zijn nog niet compleet
    f.index.name = "time"
    return f.sort_index()


def fetch_book_imbalance(inst: str = "BTC-USDT-SWAP") -> dict:
    """Momentopname orderboek: (bied − laat) / (bied + laat) voor de 400 beste niveaus en binnen ±0,05%."""
    b = _get(f"{OKX}/api/v5/market/books", {"instId": inst, "sz": 400})["data"][0]
    bids = [(float(r[0]), float(r[1])) for r in b["bids"]]
    asks = [(float(r[0]), float(r[1])) for r in b["asks"]]
    mid = (bids[0][0] + asks[0][0]) / 2
    imb = lambda bs, as_: (bs - as_) / (bs + as_) if bs + as_ else 0.0  # noqa: E731
    near = lambda side, sign: sum(q for px, q in side if abs(px / mid - 1) <= 0.0005)  # noqa: E731
    return {
        "time": pd.Timestamp(int(b["ts"]), unit="ms", tz="UTC").isoformat(),
        "mid": mid, "spread": asks[0][0] - bids[0][0],
        "imbalance_400": round(imb(sum(q for _, q in bids), sum(q for _, q in asks)), 5),
        "imbalance_5bp": round(imb(near(bids, 1), near(asks, -1)), 5),
    }


def load_csv(path: str) -> pd.DataFrame:
    """CSV met kolommen time/open/high/low/close/volume (bijv. eerder opgeslagen met `fetch`)."""
    df = pd.read_csv(path)
    tcol = "time" if "time" in df.columns else df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol], utc=True)
    return df.set_index(tcol).sort_index()[["open", "high", "low", "close", "volume"]].astype(float)
