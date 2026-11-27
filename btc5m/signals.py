"""Technische momentopname: trend, momentum, volatiliteit, niveaus en een scorekaart."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import add_indicators, ema, resample


def swing_levels(df: pd.DataFrame, lookback: int = 288, width: int = 6, atr_value: float | None = None) -> dict:
    """Steun/weerstand uit swing-pivots (±`width` candles) van de laatste `lookback` candles.

    Pivots binnen 0.5×ATR van elkaar worden samengevoegd (sterkte = aantal aanrakingen);
    niveaus dichter dan 0.3×ATR bij de huidige prijs worden genegeerd.
    """
    w = df.tail(lookback)
    win = 2 * width + 1
    highs = w["high"][w["high"] == w["high"].rolling(win, center=True).max()]
    lows = w["low"][w["low"] == w["low"].rolling(win, center=True).min()]
    price = float(w["close"].iloc[-1])
    tol = atr_value if atr_value else float((w["high"] - w["low"]).mean())

    clusters: list[list[float]] = []
    for x in sorted(float(v) for v in pd.concat([highs, lows])):
        if clusters and x - clusters[-1][-1] <= 0.5 * tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    levels = [(float(np.mean(c)), len(c)) for c in clusters]
    above = sorted((lv for lv in levels if lv[0] > price + 0.3 * tol), key=lambda lv: lv[0])
    below = sorted((lv for lv in levels if lv[0] < price - 0.3 * tol), key=lambda lv: -lv[0])
    return {
        "resistance": [round(lv[0], 1) for lv in above[:3]],
        "resistance_touches": [lv[1] for lv in above[:3]],
        "support": [round(lv[0], 1) for lv in below[:3]],
        "support_touches": [lv[1] for lv in below[:3]],
        "high_24h": float(w["high"].max()),
        "low_24h": float(w["low"].min()),
    }


def _trend_label(close: float, e_fast: float, e_mid: float, e_slow: float) -> str:
    if close > e_fast > e_mid > e_slow:
        return "sterk stijgend"
    if close < e_fast < e_mid < e_slow:
        return "sterk dalend"
    if close > e_mid and e_fast > e_mid:
        return "stijgend"
    if close < e_mid and e_fast < e_mid:
        return "dalend"
    return "zijwaarts/gemengd"


def higher_timeframe(df: pd.DataFrame) -> dict:
    res = {}
    for rule in ("15min", "1h"):
        htf = resample(df, rule)
        if len(htf) < 60:
            continue
        c = htf["close"]
        e9, e21, e50 = (float(ema(c, n).iloc[-1]) for n in (9, 21, 50))
        res[rule] = {"trend": _trend_label(float(c.iloc[-1]), e9, e21, e50),
                     "change_pct_last": float(c.pct_change().iloc[-1] * 100)}
    return res


def snapshot(df: pd.DataFrame) -> dict:
    ind = add_indicators(df)
    last = ind.iloc[-1]
    price = float(last["close"])
    atr_pct_rank = float((ind["atr14"].tail(1000) <= last["atr14"]).mean())

    score = 0
    reasons: list[str] = []

    def vote(cond_up: bool, cond_down: bool, up_txt: str, down_txt: str, weight: int = 1) -> None:
        nonlocal score
        if cond_up:
            score += weight
            reasons.append(f"+{weight} {up_txt}")
        elif cond_down:
            score -= weight
            reasons.append(f"-{weight} {down_txt}")

    vote(last["ema9"] > last["ema21"], last["ema9"] < last["ema21"], "EMA9 boven EMA21", "EMA9 onder EMA21")
    vote(price > last["ema50"], price < last["ema50"], "prijs boven EMA50", "prijs onder EMA50")
    vote(price > last["ema200"], price < last["ema200"], "prijs boven EMA200", "prijs onder EMA200")
    vote(last["macd_hist"] > 0, last["macd_hist"] < 0, "MACD-histogram positief", "MACD-histogram negatief")
    vote(price > last["vwap"], price < last["vwap"], "prijs boven dag-VWAP", "prijs onder dag-VWAP")
    vote(55 <= last["rsi14"] < 70, 30 < last["rsi14"] <= 45, "RSI bullish zone", "RSI bearish zone")
    # Extremen: tegengesteld (mean reversion) signaal
    vote(last["rsi14"] <= 30, last["rsi14"] >= 70, "RSI oversold (kans op bounce)", "RSI overbought (kans op terugval)")

    htf = higher_timeframe(df)
    for rule, info in htf.items():
        vote("stijgend" in info["trend"], "dalend" in info["trend"],
             f"{rule}-trend {info['trend']}", f"{rule}-trend {info['trend']}")

    if score >= 4:
        bias = "bullish"
    elif score >= 2:
        bias = "licht bullish"
    elif score <= -4:
        bias = "bearish"
    elif score <= -2:
        bias = "licht bearish"
    else:
        bias = "neutraal"

    return {
        "time": ind.index[-1].isoformat(),
        "price": price,
        "change_pct": {
            "5m": float(ind["close"].pct_change(1).iloc[-1] * 100),
            "1h": float(ind["close"].pct_change(12).iloc[-1] * 100),
            "4h": float(ind["close"].pct_change(48).iloc[-1] * 100),
            "24h": float(ind["close"].pct_change(288).iloc[-1] * 100),
        },
        "trend_5m": _trend_label(price, last["ema9"], last["ema21"], last["ema50"]),
        "higher_timeframes": htf,
        "indicators": {
            "ema9": float(last["ema9"]), "ema21": float(last["ema21"]),
            "ema50": float(last["ema50"]), "ema200": float(last["ema200"]),
            "rsi14": float(last["rsi14"]), "macd_hist": float(last["macd_hist"]),
            "bb_pctb": float(last["bb_pctb"]), "bb_width_pct": float(last["bb_width"] * 100),
            "atr14": float(last["atr14"]), "atr_pct_of_price": float(last["atr14"] / price * 100),
            "atr_percentile_1000": atr_pct_rank, "vwap": float(last["vwap"]),
            "volume_z": None if np.isnan(last["vol_z"]) else float(last["vol_z"]),
        },
        "volatility_regime": "hoog" if atr_pct_rank > 0.8 else "laag" if atr_pct_rank < 0.2 else "normaal",
        "levels": swing_levels(df, atr_value=float(last["atr14"])),
        "score": score,
        "bias": bias,
        "reasons": reasons,
    }
