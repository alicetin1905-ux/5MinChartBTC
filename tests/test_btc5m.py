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
