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

import numpy as np
import pandas as pd

from .indicators import add_indicators

LAMBDA = 0.94  # RiskMetrics EWMA


def ewma_sigma(logret: pd.Series, lam: float = LAMBDA) -> pd.Series:
    r2 = logret.fillna(0.0) ** 2
    var = r2.ewm(alpha=1 - lam, adjust=False).mean()
    return np.sqrt(var)


def monte_carlo(df: pd.DataFrame, horizons=(1, 3, 6, 12), n_paths: int = 20000,
                touch_pct=(0.25, 0.5, 1.0), levels: dict | None = None, seed: int = 7) -> dict:
    close = df["close"]
    r = np.log(close).diff().dropna()
    sigma = ewma_sigma(r)
    z = (r / sigma.shift(1)).replace([np.inf, -np.inf], np.nan).dropna().tail(5000).to_numpy()
    z = (z - z.mean()) / z.std()  # drift en schaal neutraliseren

    rng = np.random.default_rng(seed)
    H = max(horizons)
    s0 = float(close.iloc[-1])
    var = float(sigma.iloc[-1]) ** 2
    var_t = np.full(n_paths, var)
    logp = np.zeros((n_paths, H + 1))
    for t in range(1, H + 1):
        shock = rng.choice(z, size=n_paths) * np.sqrt(var_t)
        logp[:, t] = logp[:, t - 1] + shock - 0.5 * var_t
        var_t = LAMBDA * var_t + (1 - LAMBDA) * shock ** 2
    paths = s0 * np.exp(logp)
    hi = np.maximum.accumulate(paths, axis=1)
    lo = np.minimum.accumulate(paths, axis=1)

    out = {"price": s0, "sigma_5m_pct": float(np.sqrt(var) * 100), "horizons": {}}
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
    def __init__(self, horizon: int = 3, l2: float = 300.0):
        # Sterke L2-regularisatie: 5m-data is zeer ruisig; zwakke regularisatie gaf
        # overmoedige kansen (Brier > 0.25) in walk-forward tests.
        self.horizon = horizon
        self.l2 = l2

    def _dataset(self, df: pd.DataFrame):
        f = build_features(df)
        fwd = np.log(df["close"]).shift(-self.horizon) - np.log(df["close"])
        return f, fwd

    def _train_predict(self, X_tr, y_tr, X_te):
        mu, sd = X_tr.mean(0), X_tr.std(0)
        sd[sd == 0] = 1
        w = _fit_logit((X_tr - mu) / sd, y_tr, self.l2)
        Xb = np.column_stack([np.ones(len(X_te)), (X_te - mu) / sd])
        return 1 / (1 + np.exp(-Xb @ w)), w

    def predict_now(self, df: pd.DataFrame, train_window: int = 1500) -> dict:
        f, fwd = self._dataset(df)
        valid = f.notna().all(axis=1)
        train = valid & fwd.notna()
        idx = np.flatnonzero(train.to_numpy())[-train_window:]
        X_tr = f.to_numpy()[idx]
        y_tr = (fwd.to_numpy()[idx] > 0).astype(float)
        p, w = self._train_predict(X_tr, y_tr, f.iloc[[-1]].to_numpy())
        coefs = dict(zip(FEATURES, w[1:]))
        top = sorted(coefs.items(), key=lambda kv: -abs(kv[1]))[:5]
        return {"horizon_bars": self.horizon, "horizon_min": self.horizon * 5,
                "p_up": float(p[0]), "train_samples": int(len(idx)), "base_rate_up": float(y_tr.mean()),
                "top_features": {k: float(v) for k, v in top}}

    def walk_forward(self, df: pd.DataFrame, train_window: int = 1000, step: int = 50,
                     fee_roundtrip_pct: float = 0.05, threshold: float = 0.55) -> dict:
        """Out-of-sample test: train op verleden, voorspel de volgende `step` candles, schuif door.

        Labels die (deels) na het voorspelmoment liggen worden nooit in training gebruikt.
        """
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
