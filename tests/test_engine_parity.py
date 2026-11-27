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
