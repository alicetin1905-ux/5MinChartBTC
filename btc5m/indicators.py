"""Technische indicatoren (pure pandas/numpy, geen extra dependencies)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP die elke UTC-dag opnieuw begint."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.floor("D")
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return (pv / vol).fillna(typical)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    for n in (9, 21, 50, 200):
        out[f"ema{n}"] = ema(c, n)
    out["rsi14"] = rsi(c)
    macd = ema(c, 12) - ema(c, 26)
    out["macd"] = macd
    out["macd_signal"] = ema(macd, 9)
    out["macd_hist"] = macd - out["macd_signal"]
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    out["bb_mid"], out["bb_up"], out["bb_low"] = mid, mid + 2 * sd, mid - 2 * sd
    out["bb_pctb"] = (c - out["bb_low"]) / (out["bb_up"] - out["bb_low"])
    out["bb_width"] = (out["bb_up"] - out["bb_low"]) / mid
    out["atr14"] = atr(out)
    out["vwap"] = session_vwap(out)
    vol_mean = out["volume"].rolling(48).mean()
    vol_std = out["volume"].rolling(48).std()
    out["vol_z"] = (out["volume"] - vol_mean) / vol_std.replace(0, np.nan)
    out["logret"] = np.log(c).diff()
    return out


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """5m -> hoger tijdframe (bijv. '15min', '1h')."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df[list(agg)].resample(rule, label="left", closed="left").agg(agg).dropna()
