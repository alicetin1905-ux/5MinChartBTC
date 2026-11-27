"""Zelflerend deel: eigen voorspellingen toetsen en daarmee de modellen kalibreren.

Twee dingen worden geleerd:

1. **vol_scale** – hoe breed de Monte Carlo-banden moeten zijn. Doel: de 90%-band moet in
   ~90% van de gevallen de werkelijke prijs na 60 min bevatten. Startpunt is een schatting
   uit de candles zelf; naarmate er meer eigen (live) voorspellingen getoetst zijn, weegt het
   eigen track record zwaarder.
2. **dir_scale** – hoeveel vertrouwen de richtingskans verdient. De ruwe kans p wordt
   p' = 0.5 + dir_scale·(p − 0.5). dir_scale = 0 betekent "het model voegt niets toe"
   (altijd 50%), 1 = de ruwe kans klopt. Geschat uit out-of-sample backtest + eigen track record.

Alles gebruikt alleen informatie die op het moment van voorspellen bekend was.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .forecast import ewma_sigma

Z90 = 1.6449  # 95e percentiel van de standaardnormale verdeling (90%-band tweezijdig)
PRIOR_N = 100  # hoeveel "virtuele" waarnemingen de startschatting waard is


def _bar_close(candles: pd.DataFrame, t: pd.Timestamp) -> float | None:
    return float(candles.at[t, "close"]) if t in candles.index else None


def evaluate_history(history: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    """Voeg aan elke eerdere voorspelling de werkelijke uitkomst toe (alleen als die al bekend is)."""
    if history.empty:
        return history.assign(realized_dir=np.nan, realized_60m=np.nan)
    h = history.copy()
    h["candle_time"] = pd.to_datetime(h["candle_time"], utc=True)
    h = h.drop_duplicates("candle_time", keep="last")
    step = pd.Timedelta(minutes=5)
    dirs, p60 = [], []
    for _, row in h.iterrows():
        t0 = row["candle_time"]
        hz = int(row.get("model_horizon_min", 15) or 15) // 5
        later = _bar_close(candles, t0 + hz * step)
        dirs.append(np.nan if later is None else float(later > row["price"]))
        end = _bar_close(candles, t0 + 12 * step)
        p60.append(np.nan if end is None else end)
    h["realized_dir"] = dirs
    h["realized_60m"] = p60
    return h


def track_record(ev: pd.DataFrame) -> dict:
    """Prestatiecijfers van de eigen live voorspellingen."""
    out: dict = {"n_direction": 0, "n_range": 0}
    if ev.empty:
        return out
    d = ev.dropna(subset=["realized_dir"])
    if len(d):
        p_cal = d["p_up"].astype(float)
        p_raw = d["p_up_raw"].astype(float) if "p_up_raw" in d else p_cal
        y = d["realized_dir"].astype(float)
        out.update({
            "n_direction": int(len(d)),
            "hit_rate": float(((p_cal > 0.5) == (y > 0.5)).mean()),
            "share_up": float(y.mean()),
            "brier_raw": float(((p_raw.fillna(p_cal) - y) ** 2).mean()),
            "brier_calibrated": float(((p_cal - y) ** 2).mean()),
        })
    r = ev.dropna(subset=["realized_60m", "p05_60m", "p95_60m"])
    if len(r):
        inside90 = (r["realized_60m"] >= r["p05_60m"]) & (r["realized_60m"] <= r["p95_60m"])
        out.update({"n_range": int(len(r)), "coverage_90": float(inside90.mean())})
        if {"p25_60m", "p75_60m"} <= set(r.columns):
            q = r.dropna(subset=["p25_60m", "p75_60m"])
            if len(q):
                inside50 = (q["realized_60m"] >= q["p25_60m"]) & (q["realized_60m"] <= q["p75_60m"])
                out["coverage_50"] = float(inside50.mean())
    out["since"] = str(ev["candle_time"].min())[:16]
    src = ev["source"].astype(str) if "source" in ev else pd.Series(dtype=str)
    out["n_replay"] = int(src.str.startswith("replay").sum())
    out["n_live"] = int(len(ev) - out["n_replay"])
    return out


def vol_scale_from_candles(candles: pd.DataFrame, horizon: int = 12) -> float:
    """Hoe vaak lag de 60m-beweging buiten ±1.645·σ·√h? Geeft de factor waarmee σ moet."""
    lr = np.log(candles["close"]).diff()
    sigma = ewma_sigma(lr)
    fwd = np.log(candles["close"]).shift(-horizon) - np.log(candles["close"])
    z = (fwd / (sigma * np.sqrt(horizon))).replace([np.inf, -np.inf], np.nan).dropna()
    z = z.iloc[300:]  # EWMA laten inlopen
    if len(z) < 200:
        return 1.0
    return float(np.clip(np.quantile(np.abs(z), 0.90) / Z90, 0.6, 1.8))


def vol_scale_from_history(ev: pd.DataFrame) -> tuple[float | None, int]:
    if ev.empty or not {"realized_60m", "p05_60m", "p50_60m", "p95_60m"} <= set(ev.columns):
        return None, 0
    r = ev.dropna(subset=["realized_60m", "p05_60m", "p95_60m"])
    if len(r) < 10:
        return None, len(r)
    center = np.log(r["p50_60m"].astype(float))
    half = (np.log(r["p95_60m"].astype(float)) - np.log(r["p05_60m"].astype(float))) / 2
    used = r["vol_scale"].astype(float).fillna(1.0) if "vol_scale" in r else 1.0
    # Afwijking uitgedrukt in "ongeschaalde" banden: zo telt een eerder toegepaste factor niet dubbel
    u = np.abs(np.log(r["realized_60m"].astype(float)) - center) / half * used
    return float(np.clip(np.quantile(u, 0.90), 0.6, 1.8)), len(r)


def dir_scale_fit(p: np.ndarray, y: np.ndarray) -> float:
    """Kleinste-kwadraten (Brier) schaal s voor p' = 0.5 + s(p − 0.5), begrensd op [0, 1]."""
    x = np.asarray(p, float) - 0.5
    t = np.asarray(y, float) - 0.5
    denom = float(np.sum(x * x))
    return float(np.clip(np.sum(x * t) / denom, 0.0, 1.0)) if denom > 1e-9 else 0.0


def calibrate(candles: pd.DataFrame, ev: pd.DataFrame, oos_pairs: tuple | None) -> dict:
    k_prior = vol_scale_from_candles(candles)
    k_hist, n_hist = vol_scale_from_history(ev)
    k = k_prior if k_hist is None else (PRIOR_N * k_prior + n_hist * k_hist) / (PRIOR_N + n_hist)

    ps, ys = [], []
    if oos_pairs is not None:
        ps.append(oos_pairs[0])
        ys.append(oos_pairs[1])
    d = ev.dropna(subset=["realized_dir"]) if not ev.empty and "realized_dir" in ev else ev.iloc[0:0]
    if len(d):
        raw = d["p_up_raw"] if "p_up_raw" in d else d["p_up"]
        raw = raw.astype(float).fillna(d["p_up"].astype(float))
        # eigen live voorspellingen tellen dubbel: dat is de echte test
        ps += [raw.to_numpy()] * 2
        ys += [d["realized_dir"].to_numpy()] * 2
    s = dir_scale_fit(np.concatenate(ps), np.concatenate(ys)) if ps else 0.5

    return {
        "vol_scale": round(float(k), 4),
        "vol_scale_from_candles": round(k_prior, 4),
        "vol_scale_from_history": None if k_hist is None else round(k_hist, 4),
        "dir_scale": round(s, 4),
        "n_history_range": n_hist,
        "n_history_direction": int(len(d)),
    }


def apply_dir_scale(p_raw: float, s: float) -> float:
    return 0.5 + s * (p_raw - 0.5)


def recent_live(ev: pd.DataFrame, n: int = 12) -> list[dict]:
    """Laatste `n` getoetste live voorspellingen met uitkomst (voor het dashboard)."""
    if ev.empty or "realized_dir" not in ev:
        return []
    live = ev[~ev["source"].astype(str).str.startswith("replay")].dropna(subset=["realized_dir"])
    rows = []
    for _, r in live.tail(n).iloc[::-1].iterrows():
        inside = None
        if pd.notna(r.get("realized_60m")) and pd.notna(r.get("p05_60m")):
            inside = bool(r["p05_60m"] <= r["realized_60m"] <= r["p95_60m"])
        rows.append({
            "time": r["candle_time"].isoformat(), "price": float(r["price"]), "p_up": float(r["p_up"]),
            "went_up": bool(r["realized_dir"] > 0.5), "correct": bool((r["p_up"] > 0.5) == (r["realized_dir"] > 0.5)),
            "price_60m": None if pd.isna(r.get("realized_60m")) else float(r["realized_60m"]),
            "inside_90": inside,
        })
    return rows
