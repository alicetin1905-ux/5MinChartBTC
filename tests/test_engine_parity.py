"""Controleer dat de browser-rekenmotor (site/engine.js) dezelfde uitkomsten geeft als Python."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from btc5m.forecast import FEATURES, DirectionModel, build_features
from btc5m.indicators import add_indicators

from test_btc5m import synthetic

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

SCRIPT = r"""
const E = require(process.argv[2]);
const fs = require("fs");
const raw = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const params = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const c = raw.map(([t, o, h, l, cl, v]) => ({ t, o, h, l, c: cl, v }));
const ind = E.indicators(c);
const i = c.length - 1;
const f = E.features(c, ind, i);
const d = E.direction(f, params, 1);
const pick = ["ema9", "ema21", "ema50", "rsi14", "macd_hist", "bb_pctb", "atr14", "vwap", "vol_z", "sigma"];
const last = Object.fromEntries(pick.map((k) => [k, ind[k][i]]));
console.log(JSON.stringify({ features: f, p: d.pRaw, last }));
"""


@pytest.mark.skipif(NODE is None, reason="node niet beschikbaar")
def test_engine_matches_python(tmp_path):
    df = synthetic(1200, seed=11)
    candles = [[int(t.timestamp() * 1000), *map(float, row)]
               for t, row in zip(df.index, df[["open", "high", "low", "close", "volume"]].to_numpy())]
    (tmp_path / "c.json").write_text(json.dumps(candles))
    model = DirectionModel(horizon=3)
    py_p = model.predict_now(df)
    (tmp_path / "p.json").write_text(json.dumps(py_p["params"]))
    (tmp_path / "s.js").write_text(SCRIPT)
    out = subprocess.run([NODE, str(tmp_path / "s.js"), str(ROOT / "site" / "engine.js"),
                          str(tmp_path / "c.json"), str(tmp_path / "p.json")],
                         capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)

    f_py = build_features(df).iloc[-1]
    for name in FEATURES:
        assert js["features"][name] == pytest.approx(f_py[name], rel=1e-6, abs=1e-9), name

    ind = add_indicators(df).iloc[-1]
    for k, v in js["last"].items():
        ref = ind[k] if k != "sigma" else None
        if ref is not None:
            assert v == pytest.approx(ref, rel=1e-9, abs=1e-9), k
    assert js["p"] == pytest.approx(py_p["p_up"], rel=1e-9)
    assert np.isfinite(js["p"])


SCRIPT2 = r"""
const E = require(process.argv[2]);
const fs = require("fs");
const inp = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const c = inp.candles.map(([t, o, h, l, cl, v]) => ({ t, o, h, l, c: cl, v }));
const sig = E.deseasonalizedSigma(c, inp.season);
const sc = E.stepScales(c[c.length - 1].t, 12, inp.season);
const paths = inp.paths.map((col) => Float64Array.from(col));
const k = E.calibratePaths(paths, inp.sd, inp.c50, inp.c90);
console.log(JSON.stringify({ sigma: sig[sig.length - 1], sc, k, last: Array.from(paths[paths.length - 1]).slice(0, 50) }));
"""


@pytest.mark.skipif(NODE is None, reason="node niet beschikbaar")
def test_engine_seasonal_and_calibration_match_python(tmp_path):
    from btc5m.forecast import calibrate_paths, deseasonalized_sigma, seasonal_factors, step_scales
    df = synthetic(3000, seed=21)
    s2 = seasonal_factors(df)
    rng = np.random.default_rng(3)
    paths = 100 * np.exp(np.cumsum(np.c_[np.zeros(400), rng.standard_t(4, (400, 12)) * 0.002], axis=1))
    sd = np.sqrt(np.cumsum(np.full(12, 0.002 ** 2)))
    py_paths, py_k = calibrate_paths(paths, sd, 0.55, 1.75)
    candles = [[int(t.timestamp() * 1000), *map(float, row)]
               for t, row in zip(df.index, df[["open", "high", "low", "close", "volume"]].to_numpy())]
    inp = {"candles": candles, "season": np.sqrt(s2).tolist(), "paths": paths.T.tolist(),
           "sd": sd.tolist(), "c50": 0.55, "c90": 1.75}
    (tmp_path / "in.json").write_text(json.dumps(inp))
    (tmp_path / "s.js").write_text(SCRIPT2)
    out = subprocess.run([NODE, str(tmp_path / "s.js"), str(ROOT / "site" / "engine.js"), str(tmp_path / "in.json")],
                         capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    assert js["sigma"] == pytest.approx(float(deseasonalized_sigma(df, s2).iloc[-1]), rel=1e-9)
    assert js["sc"] == pytest.approx(step_scales(df.index[-1], 12, s2).tolist(), rel=1e-9)
    assert js["k"]["k50"] == pytest.approx(py_k["k50"], rel=1e-9)
    assert js["k"]["k90"] == pytest.approx(py_k["k90"], rel=1e-9)
    assert js["last"] == pytest.approx(py_paths[:50, -1].tolist(), rel=1e-9)


SCRIPT3 = r"""
const E = require(process.argv[2]);
const fs = require("fs");
const inp = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const c = inp.candles.map(([t, o, h, l, cl, v]) => ({ t, o, h, l, c: cl, v }));
const sig = E.deseasonalizedSigma(c, inp.season, inp.ev);
const lastT = inp.lastT;
console.log(JSON.stringify({ sigma: sig[sig.length - 1], sc: E.stepScales(lastT, 24, inp.season, inp.ev) }));
"""


@pytest.mark.skipif(NODE is None, reason="node niet beschikbaar")
def test_engine_macro_events_match_python(tmp_path):
    import pandas as pd
    from btc5m.forecast import deseasonalized_sigma, seasonal_factors, step_scales
    df = synthetic(3000, seed=31)
    s2 = seasonal_factors(df)
    t_past = df.index[-40]
    t_future = df.index[-1] + pd.Timedelta(minutes=5 * 7)
    events = pd.DataFrame({"time": [t_past, t_future, df.index[-1] + pd.Timedelta(minutes=50)],
                           "name": ["a", "b", "c"], "impact": [3, 3, 2]})
    vol = {"s2": s2, "events": events, "m0": 9.0, "m1": 1.8}
    candles = [[int(t.timestamp() * 1000), *map(float, row)]
               for t, row in zip(df.index, df[["open", "high", "low", "close", "volume"]].to_numpy())]
    ev_js = {"events": [{"t": int(t.timestamp() * 1000), "impact": int(i)} for t, i in zip(events["time"], events["impact"])],
             "m0": 9.0, "m1": 1.8}
    inp = {"candles": candles, "season": np.sqrt(s2).tolist(), "ev": ev_js, "lastT": int(df.index[-1].timestamp() * 1000)}
    (tmp_path / "in.json").write_text(json.dumps(inp))
    (tmp_path / "s.js").write_text(SCRIPT3)
    out = subprocess.run([NODE, str(tmp_path / "s.js"), str(ROOT / "site" / "engine.js"), str(tmp_path / "in.json")],
                         capture_output=True, text=True, check=True)
    js = json.loads(out.stdout)
    assert js["sigma"] == pytest.approx(float(deseasonalized_sigma(df, vol).iloc[-1]), rel=1e-9)
    py_sc = step_scales(df.index[-1], 24, vol)
    assert js["sc"] == pytest.approx(py_sc.tolist(), rel=1e-9)
    assert py_sc[6] > 2.5 * py_sc[5]  # publicatie-candle (stap 7) ~3× zo breed als de candle ervoor
