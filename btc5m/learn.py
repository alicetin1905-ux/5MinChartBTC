"""Zelflerend deel: eigen voorspellingen toetsen en daarmee de modellen kalibreren.

Twee dingen worden geleerd:

1. **Bandkalibratie (c50, c90)** – hoe ver de koers na 60 min werkelijk van de mediaan ligt, in
   eenheden van de voorspelde volatiliteit, apart voor de 50%- en de 90%-band. BTC heeft dikke
   staarten (meestal rustig, soms een sprong); één factor voor alles maakte de 50%-band te breed
   en de 90%-band te smal. Startpunt is een schatting uit de candles (30 dagen); naarmate er meer
   eigen live voorspellingen getoetst zijn, weegt het eigen track record zwaarder.
2. **dir_scale** – hoeveel vertrouwen de richtingskans verdient. De ruwe kans p wordt
   p' = 0.5 + dir_scale·(p − 0.5). dir_scale = 0 betekent "het model voegt niets toe"
   (altijd 50%), 1 = de ruwe kans klopt. Geschat uit out-of-sample backtest + eigen track record.

Alles gebruikt alleen informatie die op het moment van voorspellen bekend was.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .forecast import _vol, deseasonalized_sigma, event_var_mult, season_bucket

Z50, Z90 = 0.6745, 1.6449  # standaardnormaal: 50%- en 90%-band (tweezijdig)
PRIOR_N = 200  # hoeveel "virtuele" waarnemingen de startschatting waard is


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


def band_targets_from_candles(candles: pd.DataFrame, vol, horizon: int = 12) -> tuple[float, float]:
    """Gestandaardiseerde 60m-afstanden |r60| / σ̂60 uit de candles: 50e en 90e percentiel."""
    v = _vol(vol)
    sig = deseasonalized_sigma(candles, v).to_numpy()
    s2b = v["s2"][season_bucket(candles.index)] * event_var_mult(candles.index, v)
    cs = np.concatenate([[0.0], np.cumsum(s2b)])
    n = len(candles)
    fut = np.full(n, np.nan)
    k = np.arange(max(0, n - horizon))
    fut[k] = cs[k + 1 + horizon] - cs[k + 1]
    lc = np.log(candles["close"].to_numpy())
    r = np.full(n, np.nan)
    r[: n - horizon] = lc[horizon:] - lc[: n - horizon]
    denom = sig * np.sqrt(fut)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.abs(r / np.where(denom > 0, denom, np.nan))[300:]
    z = z[np.isfinite(z)]
    if len(z) < 200:
        return Z50, Z90
    c50, c90 = np.quantile(z, [0.5, 0.9])
    return float(c50), float(c90)


def band_targets_from_history(ev: pd.DataFrame) -> tuple[float | None, float | None, int]:
    """Dezelfde maat uit eigen getoetste voorspellingen (alleen rijen met opgeslagen sd60)."""
    need = {"realized_60m", "p50_60m", "sd60"}
    if ev.empty or not need <= set(ev.columns):
        return None, None, 0
    r = ev.dropna(subset=list(need))
    r = r[r["sd60"].astype(float) > 0]
    if len(r) < 20:
        return None, None, len(r)
    z = np.abs(np.log(r["realized_60m"].astype(float) / r["p50_60m"].astype(float))) / r["sd60"].astype(float)
    c50, c90 = np.quantile(z, [0.5, 0.9])
    return float(c50), float(c90), len(r)


def dir_scale_fit(p: np.ndarray, y: np.ndarray) -> float:
    """Kleinste-kwadraten (Brier) schaal s voor p' = 0.5 + s(p − 0.5), begrensd op [0, 1]."""
    x = np.asarray(p, float) - 0.5
    t = np.asarray(y, float) - 0.5
    denom = float(np.sum(x * x))
    return float(np.clip(np.sum(x * t) / denom, 0.0, 1.0)) if denom > 1e-9 else 0.0


def calibrate(candles: pd.DataFrame, ev: pd.DataFrame, oos_pairs: tuple | None, vol=None) -> dict:
    c50_c, c90_c = band_targets_from_candles(candles, vol)
    c50_h, c90_h, n_hist = band_targets_from_history(ev)
    if c50_h is None:
        c50, c90 = c50_c, c90_c
    else:
        c50 = (PRIOR_N * c50_c + n_hist * c50_h) / (PRIOR_N + n_hist)
        c90 = (PRIOR_N * c90_c + n_hist * c90_h) / (PRIOR_N + n_hist)

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
        "c50": round(float(c50), 4), "c90": round(float(c90), 4),
        "c50_candles": round(c50_c, 4), "c90_candles": round(c90_c, 4),
        "c50_history": None if c50_h is None else round(c50_h, 4),
        "c90_history": None if c90_h is None else round(c90_h, 4),
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


def evaluated_rows(ev: pd.DataFrame, n: int = 400) -> list[dict]:
    """Laatste `n` getoetste voorspellingen (live én replay) voor de grafieken op het dashboard."""
    if ev.empty or "realized_60m" not in ev:
        return []
    cols = ["price", "p_up", "p_up_raw", "p05_60m", "p25_60m", "p50_60m", "p75_60m", "p95_60m",
            "realized_dir", "realized_60m"]
    r = ev.dropna(subset=["realized_60m", "p05_60m", "p95_60m"]).tail(n)
    out = []
    for _, row in r.iterrows():
        d = {"t": row["candle_time"].isoformat(), "live": not str(row.get("source", "")).startswith("replay")}
        for c in cols:
            v = row.get(c)
            d[c] = None if v is None or pd.isna(v) else round(float(v), 5 if c.startswith("p_up") else 2)
        out.append(d)
    return out


def orderflow_features(df: pd.DataFrame, of: pd.DataFrame) -> pd.DataFrame:
    """Orderflow-features op de candle-tijdas (alles bekend bij het sluiten van de candle)."""
    j = of.reindex(df.index)
    tot = j["taker_buy"] + j["taker_sell"]
    buy3 = j["taker_buy"].rolling(3).sum()
    tot3 = tot.rolling(3).sum()
    loi = np.log(j["oi_ccy"])
    f = pd.DataFrame(index=df.index)
    f["of_taker"] = (j["taker_buy"] / tot.replace(0, np.nan)) - 0.5
    f["of_taker3"] = (buy3 / tot3.replace(0, np.nan)) - 0.5
    f["of_oi1"] = loi.diff().clip(-0.02, 0.02)
    f["of_oi6"] = loi.diff(6).clip(-0.05, 0.05)
    f["of_ls6"] = j["ls_ratio"].diff(6)
    return f


def orderflow_test(df: pd.DataFrame, of: pd.DataFrame | None, min_bars: int = 2000) -> dict:
    """Voegt orderflow iets toe aan het richtingsmodel? Eerlijke walk-forward vergelijking,
    pas zodra er genoeg verzamelde data is."""
    from .forecast import DirectionModel

    if of is None or of.empty:
        return {"status": "verzamelen", "bars": 0, "needed": min_bars, "days": 0.0}
    f = orderflow_features(df, of)
    valid = f.notna().all(axis=1)
    n = int(valid.sum())
    out = {"bars": n, "needed": min_bars, "days": round(n / 288, 1),
           "since": str(f.index[valid.to_numpy()][0])[:16] if n else None}
    if n < min_bars:
        return {"status": "verzamelen", **out}
    sub = df.loc[f.index[valid.to_numpy()][0]:]
    base = DirectionModel(3).walk_forward(sub)
    ext = DirectionModel(3, extra=f).walk_forward(sub)
    if "error" in base or "error" in ext:
        return {"status": "verzamelen", **out}
    better = ext["brier"] < base["brier"] - 0.0005 and ext["accuracy"] > base["accuracy"]
    return {"status": "getest", **out,
            "base": {"accuracy": base["accuracy"], "brier": base["brier"], "n": base["oos_predictions"]},
            "with_orderflow": {"accuracy": ext["accuracy"], "brier": ext["brier"], "n": ext["oos_predictions"]},
            "verdict": ("orderflow verbetert het model out-of-sample" if better
                        else "orderflow voegt (nog) niets aantoonbaars toe")}


# ---------------------------------------------------------------- scorebord

def _wis(y, m, l50, u50, l90, u90):
    """Weighted interval score (lager = beter) voor mediaan + 50%- en 90%-interval.
    Bestraft zowel te brede banden als uitkomsten buiten de band."""
    def interval(lo, hi, a):
        return (hi - lo) + 2 / a * np.maximum(lo - y, 0) + 2 / a * np.maximum(y - hi, 0)
    return (0.5 * np.abs(y - m) + 0.25 * interval(l50, u50, 0.5) + 0.05 * interval(l90, u90, 0.1)) / 2.5


def scoreboard(ev: pd.DataFrame, candles: pd.DataFrame) -> dict:
    """Kwaliteit van de 60m-banden per dag, vergeleken met een naïeve band (spreiding van de 60m-
    rendementen in de 24 uur ervoor, zonder seizoen, kalibratie of macro). Vaardigheid = 1 − WIS/WIS_naïef:
    > 0 betekent beter dan naïef. Gebruikt alleen informatie die op het voorspelmoment bekend was."""
    need = ["realized_60m", "price", "p05_60m", "p25_60m", "p50_60m", "p75_60m", "p95_60m"]
    if ev.empty or not set(need) <= set(ev.columns):
        return {"daily": [], "last7": None, "by_version": {}}
    r = ev.dropna(subset=need).copy()
    lc = np.log(candles["close"])
    base_sd = (lc - lc.shift(12)).rolling(288, min_periods=200).std()
    r["base_sd"] = base_sd.reindex(pd.DatetimeIndex(r["candle_time"])).to_numpy()
    r = r.dropna(subset=["base_sd"])
    if r.empty:
        return {"daily": [], "last7": None, "by_version": {}}
    rel = lambda col: np.log(r[col].astype(float).to_numpy() / r["price"].astype(float).to_numpy()) * 100  # noqa: E731
    y = rel("realized_60m")
    sd = r["base_sd"].to_numpy() * 100
    r["wis"] = _wis(y, rel("p50_60m"), rel("p25_60m"), rel("p75_60m"), rel("p05_60m"), rel("p95_60m"))
    r["wis_base"] = _wis(y, 0.0, -0.6745 * sd, 0.6745 * sd, -1.6449 * sd, 1.6449 * sd)
    r["in50"] = (r["realized_60m"] >= r["p25_60m"]) & (r["realized_60m"] <= r["p75_60m"])
    r["in90"] = (r["realized_60m"] >= r["p05_60m"]) & (r["realized_60m"] <= r["p95_60m"])
    r["version"] = r["model_version"].fillna("v1") if "model_version" in r else "v1"
    r["live"] = ~r["source"].astype(str).str.startswith("replay") if "source" in r else True
    r["date"] = pd.DatetimeIndex(r["candle_time"]).strftime("%Y-%m-%d")

    def agg(g):
        return {"n": int(len(g)), "wis": round(float(g["wis"].mean()), 5), "wis_base": round(float(g["wis_base"].mean()), 5),
                "skill": round(float(1 - g["wis"].mean() / g["wis_base"].mean()), 4),
                "cov50": round(float(g["in50"].mean()), 3), "cov90": round(float(g["in90"].mean()), 3),
                "live_share": round(float(g["live"].mean()), 2), "version": str(g["version"].mode().iloc[0])}

    daily = [{"date": d, **agg(g)} for d, g in r.groupby("date")]
    cutoff = pd.DatetimeIndex(r["candle_time"]).max() - pd.Timedelta(days=7)
    last7 = r[pd.DatetimeIndex(r["candle_time"]) > cutoff]
    return {"daily": daily, "last7": agg(last7) if len(last7) else None,
            "by_version": {v: agg(g) for v, g in r.groupby("version")}}
