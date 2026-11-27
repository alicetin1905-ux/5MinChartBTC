"""Offline tests op synthetische data (geen netwerk nodig)."""

import numpy as np
import pandas as pd
import pytest

from btc5m.forecast import DirectionModel, monte_carlo
from btc5m.indicators import add_indicators, resample, rsi
from btc5m.report import analyse, to_markdown


def synthetic(n: int = 1600, seed: int = 1, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = drift + rng.standard_t(4, n) * 0.001
    close = 60000 * np.exp(np.cumsum(r))
    openp = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.0007, n)) * close
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"open": openp, "high": np.maximum(openp, close) + spread,
                         "low": np.minimum(openp, close) - spread, "close": close,
                         "volume": rng.gamma(2, 50, n)}, index=idx)


def test_indicators_ranges():
    ind = add_indicators(synthetic())
    assert ind["rsi14"].between(0, 100).all()
    assert (ind["atr14"].dropna() > 0).all()
    assert (ind["bb_up"].dropna() >= ind["bb_low"].dropna()).all()


def test_rsi_extremes():
    up = pd.Series(np.arange(100, 200, dtype=float))
    assert rsi(up).iloc[-1] > 99
    assert rsi(up[::-1].reset_index(drop=True)).iloc[-1] < 1


def test_resample_hourly():
    h = resample(synthetic(240), "1h")
    assert len(h) == 20


def test_monte_carlo_quantiles_ordered_and_widening():
    mc = monte_carlo(synthetic(), n_paths=4000)
    widths = []
    for h in mc["horizons"].values():
        assert h["p05"] < h["p25"] < h["p50"] < h["p75"] < h["p95"]
        widths.append(h["p95"] - h["p05"])
    assert widths == sorted(widths)


def test_walk_forward_has_no_edge_on_random_walk():
    bt = DirectionModel(horizon=3).walk_forward(synthetic(2500))
    assert "error" not in bt
    assert abs(bt["accuracy"] - 0.5) < 0.06  # random walk: geen voorspelbaarheid


def test_model_detects_strong_trend():
    df = synthetic(2000, drift=0.0006)
    assert DirectionModel(horizon=3).predict_now(df)["p_up"] > 0.5


def test_full_report_renders():
    a = analyse(synthetic(1600))
    md = to_markdown(a)
    assert "Kansverdeling" in md and "Richtingsmodel" in md


def test_too_little_data():
    with pytest.raises(ValueError):
        analyse(synthetic(100))


# ---------------------------------------------------------------- zelflerend deel

from btc5m.learn import band_targets_from_history, dir_scale_fit, evaluate_history, track_record  # noqa: E402
from btc5m.scheduled import HISTORY_FIELDS, load_history, replay_history  # noqa: E402


def test_dir_scale_zero_for_useless_model_and_one_for_good_model():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.3, 0.7, 20000)
    assert dir_scale_fit(p, rng.random(20000) < 0.5) < 0.1          # kansen zeggen niets
    assert dir_scale_fit(p, rng.random(20000) < p) > 0.85            # kansen kloppen


def test_band_targets_learn_fat_tails_from_history():
    rng = np.random.default_rng(4)
    n = 4000
    # dikke staarten: Student-t(3) geschaald naar sd60 = 1%
    z = rng.standard_t(3, n) / np.sqrt(3.0)
    ev = pd.DataFrame({"p50_60m": 100.0, "sd60": 0.01, "realized_60m": 100 * np.exp(0.01 * z)})
    c50, c90, cnt = band_targets_from_history(ev)
    assert cnt == n
    q50, q90 = np.quantile(np.abs(z), [0.5, 0.9])
    assert abs(c50 - q50) < 1e-6 and abs(c90 - q90) < 1e-6
    assert c90 / c50 > 1.6449 / 0.6745  # dikke staarten: verhouding groter dan bij een normale verdeling


def test_calibration_hits_targets_and_stays_monotone():
    from btc5m.forecast import monte_carlo
    df = synthetic(2000)
    mc = monte_carlo(df, n_paths=8000, targets={"c50": 0.5, "c90": 1.9})
    h = mc["horizons"]["60m"]
    sd = mc["sd60"]
    half50 = (np.log(h["p75"]) - np.log(h["p25"])) / 2 / sd
    half90 = (np.log(h["p95"]) - np.log(h["p05"])) / 2 / sd
    assert abs(half50 - 0.5) < 0.05 and abs(half90 - 1.9) < 0.1
    assert h["p05"] < h["p25"] < h["p50"] < h["p75"] < h["p95"]


def test_seasonal_factors_detect_busy_hour():
    from btc5m.forecast import seasonal_factors
    df = synthetic(6000, seed=5)
    r = np.log(df["close"]).diff().fillna(0).to_numpy()
    busy = df.index.hour == 14
    close = df["close"].to_numpy()[0] * np.exp(np.cumsum(np.where(busy, 3 * r, r)))
    df2 = df.assign(close=close)
    s2 = seasonal_factors(df2)
    assert s2[14] > 2.0 and s2[3] < 1.0


def test_replay_and_evaluation_on_synthetic_data():
    df = synthetic(3400)
    hist = replay_history(df, days=1, every=6, lookback=3000)
    assert len(hist) > 30 and hist["source"].str.startswith("replay").all()
    ev = evaluate_history(hist, df)
    tr = track_record(ev)
    assert tr["n_direction"] > 20 and 0.6 < tr["coverage_90"] <= 1.0


def test_old_history_is_migrated(tmp_path):
    f = tmp_path / "history.csv"
    f.write_text("generated_at,candle_time,source,price,p_up\n2026-01-01,2026-01-01T00:00:00Z,okx,1,0.5\n")
    h = load_history(f)
    assert list(h.columns) == HISTORY_FIELDS and len(h) == 1


def test_analyse_with_history_reports_track_record():
    df = synthetic(3400)
    hist = replay_history(df.iloc[:-50], days=1, every=6, lookback=3000)
    a = analyse(df, history=hist)
    assert a["learning"]["track_record"]["n_direction"] > 0
    assert "Zelflerend" in to_markdown(a)


def test_orderflow_test_collects_then_detects_signal():
    from btc5m.learn import orderflow_test
    df = synthetic(3200, seed=8)
    assert orderflow_test(df, None)["status"] == "verzamelen"
    rng = np.random.default_rng(1)
    fwd = np.log(df["close"]).shift(-3) - np.log(df["close"])
    signal = np.sign(fwd.fillna(0).to_numpy())
    buy = 100 * (1 + 0.6 * signal + rng.normal(0, 0.3, len(df))).clip(0.05)
    of = pd.DataFrame({"taker_buy": buy, "taker_sell": 100.0, "oi_ccy": 30000 + rng.normal(0, 5, len(df)),
                       "ls_ratio": 2.5}, index=df.index)
    short = orderflow_test(df, of.iloc[:500])
    assert short["status"] == "verzamelen" and short["bars"] < 2000
    res = orderflow_test(df, of)
    assert res["status"] == "getest"
    assert res["with_orderflow"]["accuracy"] > res["base"]["accuracy"] + 0.05
    assert "verbetert" in res["verdict"]


def test_scoreboard_rewards_calibrated_bands():
    from btc5m.learn import scoreboard
    df = synthetic(3000, seed=12)
    lc = np.log(df["close"])
    sd = float((lc - lc.shift(12)).std())
    t = df.index[400:-12:6]
    price = df["close"].loc[t].to_numpy()
    real = df["close"].shift(-12).loc[t].to_numpy()
    def rows(scale):
        q = {f"p{p:02d}_60m": price * np.exp(z * sd * scale) for p, z in
             [(5, -1.6449), (25, -0.6745), (50, 0.0), (75, 0.6745), (95, 1.6449)]}
        return pd.DataFrame({"candle_time": t, "price": price, "realized_60m": real, "source": "okx", **q})
    good, bad = scoreboard(rows(1.0), df), scoreboard(rows(3.0), df)
    assert good["last7"]["skill"] > bad["last7"]["skill"]
    assert 0.8 < good["last7"]["cov90"] <= 1.0 and bad["last7"]["cov90"] > 0.97
