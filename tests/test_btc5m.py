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

from btc5m.learn import dir_scale_fit, evaluate_history, track_record, vol_scale_from_history  # noqa: E402
from btc5m.scheduled import HISTORY_FIELDS, load_history, replay_history  # noqa: E402


def test_dir_scale_zero_for_useless_model_and_one_for_good_model():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.3, 0.7, 20000)
    assert dir_scale_fit(p, rng.random(20000) < 0.5) < 0.1          # kansen zeggen niets
    assert dir_scale_fit(p, rng.random(20000) < p) > 0.85            # kansen kloppen


def test_vol_scale_learns_when_bands_too_narrow():
    rng = np.random.default_rng(4)
    n = 500
    price = np.full(n, 100.0)
    # band ±1% (90%), maar werkelijke bewegingen zijn twee keer zo groot
    realized = 100 * np.exp(rng.normal(0, 2 * 0.01 / 1.6449, n))
    ev = pd.DataFrame({"price": price, "p05_60m": 99.0, "p50_60m": 100.0, "p95_60m": 101.0,
                       "vol_scale": 1.0, "realized_60m": realized})
    k, cnt = vol_scale_from_history(ev)
    assert cnt == n and 1.7 < k <= 1.8


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
