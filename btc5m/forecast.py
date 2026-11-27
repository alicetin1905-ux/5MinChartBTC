"""Kansverwachting voor de komende candles.

Twee bewust gescheiden onderdelen:

1. `monte_carlo`  – Filtered Historical Simulation: historische, gestandaardiseerde
   5m-rendementen worden opnieuw getrokken en geschaald met een EWMA-volatiliteit die
   per stap wordt bijgewerkt (volatiliteitsclustering). Zonder drift: dit geeft de
   *bandbreedte* (waar kan de prijs redelijkerwijs komen), niet de richting.
2. `DirectionModel` – logistische regressie op technische features die de kans schat
   dat de prijs over `h` candles hoger staat. Wordt walk-forward gebacktest zodat je
   ziet of het model op deze data überhaupt beter is dan toeval.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .indicators import add_indicators

LAMBDA = 0.94  # RiskMetrics EWMA


def ewma_sigma(logret: pd.Series, lam: float = LAMBDA) -> pd.Series:
    r2 = logret.fillna(0.0) ** 2
    var = r2.ewm(alpha=1 - lam, adjust=False).mean()
    return np.sqrt(var)


EVENTS_FILE = Path(__file__).resolve().parent.parent / "events.csv"


def season_bucket(times: pd.DatetimeIndex) -> np.ndarray:
    """0-23 = UTC-uur op een werkdag, 24-47 = UTC-uur in het weekend."""
    return (times.hour + 24 * (times.dayofweek >= 5)).to_numpy()


def load_events(path: str | Path | None = None) -> pd.DataFrame:
    """Macro-agenda (events.csv): time_utc, name, impact. Tijden worden op 5 minuten afgerond."""
    path = Path(path) if path else EVENTS_FILE
    if not path.exists():
        return pd.DataFrame(columns=["time", "name", "impact"])
    ev = pd.read_csv(path, comment="#")
    ev["time"] = pd.to_datetime(ev["time_utc"], utc=True).dt.floor("5min")
    return ev[["time", "name", "impact"]].sort_values("time").reset_index(drop=True)


def event_var_mult(times: pd.DatetimeIndex, vol: dict | None) -> np.ndarray:
    """Variantie-vermenigvuldiger per candle: m0 in de candle van de publicatie, m1 in de 2 candles erna.
    Middelgrote events (impact 2) krijgen de wortel van die factoren."""
    mult = np.ones(len(times))
    ev = (vol or {}).get("events")
    if ev is None or len(ev) == 0:
        return mult
    m0, m1 = vol.get("m0", 1.0), vol.get("m1", 1.0)
    pos = {t: k for k, t in enumerate(times)}
    step = pd.Timedelta(minutes=5)
    for t, imp in zip(ev["time"], ev["impact"]):
        w = 1.0 if imp >= 3 else 0.5
        for off, m in ((0, m0), (1, m1), (2, m1)):
            k = pos.get(t + off * step)
            if k is not None:
                mult[k] *= m ** w
    return mult


def _vol(vol) -> dict:
    """Accepteer een volatiliteitsprofiel-dict of alleen seizoensfactoren (ndarray)."""
    if vol is None:
        return {"s2": np.ones(48), "events": None}
    if isinstance(vol, np.ndarray):
        return {"s2": vol, "events": None}
    return vol


def event_mask(times: pd.DatetimeIndex, events: pd.DataFrame | None) -> np.ndarray:
    """True voor candles in een event-venster (publicatie + 2 candles erna)."""
    m = np.zeros(len(times), dtype=bool)
    if events is None or len(events) == 0:
        return m
    ts = set()
    for t in events["time"]:
        ts.update(t + pd.Timedelta(minutes=5 * k) for k in range(3))
    return np.array([t in ts for t in times])


def seasonal_factors(df: pd.DataFrame, shrink: float = 200.0, events: pd.DataFrame | None = None) -> np.ndarray:
    """Variantie-verhouding per (uur, werkdag/weekend) t.o.v. het gemiddelde, met krimp naar 1.
    Candles rond macro-publicaties tellen niet mee (die krijgen hun eigen factor).

    Een 60-dagen-test (schatten op de eerste helft, toetsen op de tweede) gaf hiermee betere
    voorspellingen van de bewegingsgrootte (log-likelihood 3,757 → 3,805) en een gelijkmatigere
    dekking over de uren. De Amerikaanse opening (13-15u UTC) is ~1,5× zo volatiel, het weekend ~0,85×.
    """
    r = np.log(df["close"]).diff().fillna(0.0).to_numpy()
    b = season_bucket(df.index)
    keep = ~event_mask(df.index, events)
    tot = float(np.mean(r[keep] ** 2)) or 1e-12
    s2 = np.ones(48)
    for k in range(48):
        m = (b == k) & keep
        n = int(m.sum())
        if n:
            s2[k] = (n * float(np.mean(r[m] ** 2)) / tot + shrink) / (n + shrink)
    return s2


def event_factors(df: pd.DataFrame, events: pd.DataFrame | None, s2: np.ndarray,
                  prior_m0: float = 6.0, prior_m1: float = 1.5) -> dict:
    """Leer uit eerdere macro-publicaties in de data hoe groot de beweging was (in variantie t.o.v.
    normaal voor dat uur). Uitschieters worden op 50 afgekapt; krimp naar een voorzichtige startwaarde.
    Op 5 publicaties (okt-nov 2026) was de publicatie-candle gemiddeld ~29× (mediaan ~7×) normaal."""
    out = {"m0": prior_m0, "m1": prior_m1, "n_events": 0}
    if events is None or len(events) == 0:
        return out
    r = np.log(df["close"]).diff() / np.sqrt(s2[season_bucket(df.index)])
    sig = ewma_sigma(r)
    z2 = ((r / sig.shift(1)) ** 2).clip(upper=50.0)
    step = pd.Timedelta(minutes=5)
    z0, z1 = [], []
    for t in events["time"]:
        if t in z2.index and t - step in z2.index and np.isfinite(z2.get(t, np.nan)):
            z0.append(float(z2[t]))
            z1 += [float(z2[t + k * step]) for k in (1, 2) if t + k * step in z2.index]
    n = len(z0)
    if n:
        out["m0"] = (sum(z0) + 2 * prior_m0) / (n + 2)
        out["m1"] = (sum(z1) + 4 * prior_m1) / (len(z1) + 4)
        out["n_events"] = n
    return out


def volatility_profile(df: pd.DataFrame, events: pd.DataFrame | None = None) -> dict:
    """Seizoen per uur + macro-factoren: alles wat de verwachte volatiliteit per candle bepaalt."""
    s2 = seasonal_factors(df, events=events)
    ef = event_factors(df, events, s2)
    return {"s2": s2, "events": events, **ef}


def deseasonalized_sigma(df: pd.DataFrame, vol) -> pd.Series:
    v = _vol(vol)
    r = np.log(df["close"]).diff()
    scale = v["s2"][season_bucket(df.index)] * event_var_mult(df.index, v)
    return ewma_sigma(r / np.sqrt(scale))


def step_scales(last_time: pd.Timestamp, H: int, vol) -> np.ndarray:
    """σ-schaal voor elke toekomstige stap 1..H: seizoen van dat uur × eventuele macro-factor."""
    v = _vol(vol)
    times = pd.DatetimeIndex([last_time + pd.Timedelta(minutes=5 * t) for t in range(1, H + 1)])
    return np.sqrt(v["s2"][season_bucket(times)] * event_var_mult(times, v))


def calibrate_paths(paths: np.ndarray, sd_steps: np.ndarray, c50: float, c90: float) -> tuple[np.ndarray, dict]:
    """Kalibratie per band: rek of krimp elk pad zó dat de gestandaardiseerde 50%- en 90%-afstanden
    tot de mediaan gelijk worden aan de geleerde waarden c50 en c90 (monotone afbeelding per stap)."""
    out = paths.copy()
    k_last = {"k50": 1.0, "k90": 1.0}
    for t in range(1, paths.shape[1]):
        col = paths[:, t]
        med = np.median(col)
        d = np.log(col / med) / sd_steps[t - 1]
        ad = np.abs(d)
        a50, a90 = np.quantile(ad, [0.5, 0.9])
        k50 = float(np.clip(c50 / a50, 0.5, 2.0))
        k90 = float(np.clip(c90 / a90, 0.5, 2.0))
        k90 = max(k90, 0.7 * k50)  # houdt de afbeelding monotoon
        w = np.clip((ad - a50) / max(a90 - a50, 1e-9), 0.0, 1.0)
        k = k50 + (k90 - k50) * w
        out[:, t] = med * np.exp(d * k * sd_steps[t - 1])
        k_last = {"k50": k50, "k90": k90}
    return out, k_last


def monte_carlo(df: pd.DataFrame, horizons=(1, 3, 6, 12), n_paths: int = 20000,
                touch_pct=(0.25, 0.5, 1.0), levels: dict | None = None, seed: int = 7,
                vol_scale: float = 1.0, season=None, targets: dict | None = None) -> dict:
    """Filtered Historical Simulation met
    - `season`: volatiliteitsprofiel (zie volatility_profile) of alleen uurfactoren; None = vlak;
    - `targets`: geleerde gestandaardiseerde bandafstanden {"c50", "c90"} (zie learn.calibrate);
    - `vol_scale`: extra handmatige factor (standaard 1).
    """
    close = df["close"]
    v = _vol(season)  # seizoensfactoren (ndarray) of volledig volatiliteitsprofiel (dict)
    sigma = deseasonalized_sigma(df, v)
    r_des = np.log(close).diff() / np.sqrt(v["s2"][season_bucket(df.index)] * event_var_mult(df.index, v))
    z = (r_des / sigma.shift(1)).replace([np.inf, -np.inf], np.nan).dropna().tail(5000).to_numpy()
    z = (z - z.mean()) / z.std()  # drift en schaal neutraliseren

    rng = np.random.default_rng(seed)
    H = max(horizons)
    s0 = float(close.iloc[-1])
    sc = step_scales(df.index[-1], H, v)
    var0 = float(sigma.iloc[-1]) ** 2 * vol_scale ** 2
    var_t = np.full(n_paths, var0)
    logp = np.zeros((n_paths, H + 1))
    for t in range(1, H + 1):
        zz = rng.choice(z, size=n_paths)
        shock = zz * np.sqrt(var_t) * sc[t - 1]
        logp[:, t] = logp[:, t - 1] + shock - 0.5 * var_t * sc[t - 1] ** 2
        var_t = LAMBDA * var_t + (1 - LAMBDA) * (zz ** 2) * var_t
    paths = s0 * np.exp(logp)
    sd_steps = np.sqrt(var0 * np.cumsum(sc ** 2))  # verwachte log-sd per stap (vóór kalibratie)
    k = {"k50": 1.0, "k90": 1.0}
    if targets:
        paths, k = calibrate_paths(paths, sd_steps, targets["c50"], targets["c90"])
    hi = np.maximum.accumulate(paths, axis=1)
    lo = np.minimum.accumulate(paths, axis=1)

    h12 = min(12, H)
    out = {"price": s0, "sigma_5m_pct": float(np.sqrt(var0) * sc[0] * 100), "vol_scale": vol_scale,
           "season_now": float(sc[0]), "sd60": float(sd_steps[h12 - 1]),
           "k50": round(k["k50"], 4), "k90": round(k["k90"], 4), "horizons": {},
           "z_sample": np.round(rng.choice(z, size=min(1500, len(z)), replace=False), 3).tolist(),
           "sigma_des": float(sigma.iloc[-1])}
    for h in horizons:
        end = paths[:, h]
        q = np.percentile(end, [5, 25, 50, 75, 95])
        touches = {}
        for p in touch_pct:
            touches[f"+{p}%"] = float((hi[:, h] >= s0 * (1 + p / 100)).mean())
            touches[f"-{p}%"] = float((lo[:, h] <= s0 * (1 - p / 100)).mean())
        if levels:
            for name in ("resistance", "support"):
                if levels.get(name):
                    lvl = levels[name][0]
                    hit = hi[:, h] >= lvl if name == "resistance" else lo[:, h] <= lvl
                    touches[f"{name}@{lvl:.1f}"] = float(hit.mean())
        out["horizons"][f"{h * 5}m"] = {
            "bars": h,
            "p05": float(q[0]), "p25": float(q[1]), "p50": float(q[2]), "p75": float(q[3]), "p95": float(q[4]),
            "expected_abs_move_pct": float(np.mean(np.abs(end / s0 - 1)) * 100),
            "touch_prob": touches,
        }
    return out


# ---------------------------------------------------------------- richtingsmodel

FEATURES = ["r1", "r3", "r6", "r12", "rsi", "macd", "pctb", "volz", "d_ema21", "ema_spread",
            "d_vwap", "bbw", "hour_sin", "hour_cos"]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    ind = add_indicators(df)
    sig = ewma_sigma(ind["logret"]).replace(0, np.nan)
    lc = np.log(ind["close"])
    f = pd.DataFrame(index=ind.index)
    for k in (1, 3, 6, 12):
        f[f"r{k}"] = lc.diff(k) / (sig * np.sqrt(k))
    f["rsi"] = (ind["rsi14"] - 50) / 50
    f["macd"] = ind["macd_hist"] / ind["atr14"]
    f["pctb"] = ind["bb_pctb"] - 0.5
    f["volz"] = ind["vol_z"].clip(-4, 4)
    f["d_ema21"] = (ind["close"] - ind["ema21"]) / ind["atr14"]
    f["ema_spread"] = (ind["ema9"] - ind["ema21"]) / ind["atr14"]
    f["d_vwap"] = ((ind["close"] - ind["vwap"]) / ind["atr14"]).clip(-10, 10)
    f["bbw"] = np.log(ind["bb_width"])
    hour = ind.index.hour + ind.index.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    return f.replace([np.inf, -np.inf], np.nan)


def _fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 300.0, iters: int = 25) -> np.ndarray:
    """Logistische regressie met L2 via Newton-Raphson (IRLS). Intercept wordt niet bestraft."""
    Xb = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(Xb.shape[1])
    reg = np.full(Xb.shape[1], l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        grad = Xb.T @ (p - y) + reg * w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(reg) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(H, grad)
        w -= step
        if np.max(np.abs(step)) < 1e-6:
            break
    return w


class DirectionModel:
    def __init__(self, horizon: int = 3, l2: float = 300.0, extra: pd.DataFrame | None = None):
        # Sterke L2-regularisatie: 5m-data is zeer ruisig; zwakke regularisatie gaf
        # overmoedige kansen (Brier > 0.25) in walk-forward tests.
        self.horizon = horizon
        self.l2 = l2
        self.extra = extra

    def _dataset(self, df: pd.DataFrame):
        f = build_features(df)
        if self.extra is not None:  # bijv. orderflow-features (zie learn.orderflow_test)
            f = f.join(self.extra.reindex(f.index))
        fwd = np.log(df["close"]).shift(-self.horizon) - np.log(df["close"])
        return f, fwd

    def _train_predict(self, X_tr, y_tr, X_te):
        mu, sd = X_tr.mean(0), X_tr.std(0)
        sd[sd == 0] = 1
        w = _fit_logit((X_tr - mu) / sd, y_tr, self.l2)
        Xb = np.column_stack([np.ones(len(X_te)), (X_te - mu) / sd])
        self.last_params = {"mu": mu, "sd": sd, "w": w}
        return 1 / (1 + np.exp(-Xb @ w)), w

    @staticmethod
    def default_window(n: int) -> int:
        return int(min(3000, max(500, n // 2)))

    def predict_now(self, df: pd.DataFrame, train_window: int | None = None) -> dict:
        train_window = train_window or self.default_window(len(df))
        f, fwd = self._dataset(df)
        valid = f.notna().all(axis=1)
        train = valid & fwd.notna()
        idx = np.flatnonzero(train.to_numpy())[-train_window:]
        X_tr = f.to_numpy()[idx]
        y_tr = (fwd.to_numpy()[idx] > 0).astype(float)
        p, w = self._train_predict(X_tr, y_tr, f.iloc[[-1]].to_numpy())
        names = list(f.columns)
        coefs = dict(zip(names, w[1:]))
        top = sorted(coefs.items(), key=lambda kv: -abs(kv[1]))[:5]
        prm = self.last_params
        return {"horizon_bars": self.horizon, "horizon_min": self.horizon * 5,
                "p_up": float(p[0]), "train_samples": int(len(idx)), "base_rate_up": float(y_tr.mean()),
                "top_features": {k: float(v) for k, v in top},
                # volledige modelparameters, zodat het dashboard de kans live kan herberekenen
                "params": {"features": names, "mu": prm["mu"].tolist(), "sd": prm["sd"].tolist(),
                           "w": prm["w"].tolist(), "x_now": f.iloc[-1].tolist()}}

    def walk_forward(self, df: pd.DataFrame, train_window: int | None = None, step: int = 50,
                     fee_roundtrip_pct: float = 0.05, threshold: float = 0.55) -> dict:
        """Out-of-sample test: train op verleden, voorspel de volgende `step` candles, schuif door.

        Labels die (deels) na het voorspelmoment liggen worden nooit in training gebruikt.
        """
        train_window = train_window or self.default_window(len(df))
        f, fwd = self._dataset(df)
        X = f.to_numpy()
        y = fwd.to_numpy()
        valid = ~np.isnan(X).any(axis=1)
        h = self.horizon
        probs, rets = [], []
        start = int(np.argmax(valid)) + train_window + h
        for t0 in range(start, len(df) - h, step):
            tr = np.arange(t0 - h - train_window, t0 - h)  # labels bekend vóór t0
            tr = tr[valid[tr] & ~np.isnan(y[tr])]
            te = np.arange(t0, min(t0 + step, len(df) - h))
            te = te[valid[te]]
            if len(tr) < 200 or len(te) == 0:
                continue
            p, _ = self._train_predict(X[tr], (y[tr] > 0).astype(float), X[te])
            probs.append(p)
            rets.append(y[te])
        if not probs:
            return {"error": "te weinig data voor walk-forward (verhoog --bars)"}
        p = np.concatenate(probs)
        r = np.concatenate(rets)
        up = (r > 0).astype(float)
        self.last_oos = (p, up)  # voor kalibratie in learn.py
        pred = (p > 0.5).astype(float)
        conf = (p >= threshold) | (p <= 1 - threshold)
        # Niet-overlappende trades (elke h candles) voor een eerlijke rendementsschatting
        sel = np.arange(0, len(p), h)
        pos = np.where(p[sel] > 0.5, 1.0, -1.0)
        gross = pos * r[sel] * 100
        net = gross - fee_roundtrip_pct
        conf_sel = sel[conf[sel]]
        pos_c = np.where(p[conf_sel] > 0.5, 1.0, -1.0)
        net_c = pos_c * r[conf_sel] * 100 - fee_roundtrip_pct
        return {
            "horizon_bars": h,
            "oos_predictions": int(len(p)),
            "accuracy": float((pred == up).mean()),
            "baseline_always_up": float(up.mean()),
            "baseline_best": float(max(up.mean(), 1 - up.mean())),
            "brier": float(np.mean((p - up) ** 2)),
            "brier_coinflip": 0.25,
            "confident_share": float(conf.mean()),
            "confident_accuracy": float((pred[conf] == up[conf]).mean()) if conf.any() else None,
            "trades_nonoverlap": int(len(sel)),
            "avg_gross_return_pct_per_trade": float(gross.mean()),
            "avg_net_return_pct_per_trade": float(net.mean()),
            "confident_trades": int(len(conf_sel)),
            "confident_avg_net_return_pct": float(net_c.mean()) if len(conf_sel) else None,
            "fee_roundtrip_pct": fee_roundtrip_pct,
        }


def model_verdict(bt: dict) -> str:
    """Korte, eerlijke beoordeling of het richtingsmodel gewicht verdient."""
    if "error" in bt:
        return "onbekend (te weinig data)"
    edge = bt["accuracy"] - bt["baseline_best"]
    if edge > 0.02 and bt["brier"] < 0.249 and bt["avg_net_return_pct_per_trade"] > 0:
        return "lichte voorsprong op toeval out-of-sample – met voorzichtigheid bruikbaar"
    if edge > 0.0 and bt["brier"] < 0.25:
        return "marginaal beter dan toeval – vooral als tiebreaker gebruiken"
    return "geen aantoonbare voorsprong op toeval – richtingskans niet zwaar laten wegen"
