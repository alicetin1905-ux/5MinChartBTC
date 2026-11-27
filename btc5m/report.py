"""Volledige analyse samenstellen en als Markdown (Nederlands) weergeven."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from .forecast import DirectionModel, model_verdict, monte_carlo
from .learn import apply_dir_scale, calibrate, evaluate_history, track_record
from .signals import snapshot

DISCLAIMER = ("Dit is een statistische analyse, geen financieel advies. 5-minuten bewegingen van BTC "
              "zijn grotendeels ruis; gebruik de kansen als bandbreedte, niet als zekerheid.")


def analyse(df: pd.DataFrame, horizons=(1, 3, 6, 12), model_horizon: int = 3,
            derivatives: dict | None = None, backtest: bool = True,
            history: pd.DataFrame | None = None) -> dict:
    """`history` = eerdere voorspellingen (history.csv); daaruit leert de analyse bij."""
    if len(df) < 400:
        raise ValueError(f"Minstens 400 candles nodig, kreeg {len(df)}")
    snap = snapshot(df)
    model = DirectionModel(horizon=model_horizon)
    bt = model.walk_forward(df) if backtest else {}
    ev = evaluate_history(history if history is not None else pd.DataFrame(), df)
    cal = calibrate(df, ev, getattr(model, "last_oos", None))
    mc = monte_carlo(df, horizons=horizons, levels=snap["levels"], vol_scale=cal["vol_scale"])
    now = model.predict_now(df)
    now["p_up_raw"] = now["p_up"]
    now["p_up"] = apply_dir_scale(now["p_up_raw"], cal["dir_scale"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candles": len(df),
        "data_from": df.index[0].isoformat(),
        "data_to": df.index[-1].isoformat(),
        "snapshot": snap,
        "derivatives": derivatives or {},
        "monte_carlo": mc,
        "direction_model": now,
        "backtest": bt,
        "model_verdict": model_verdict(bt) if backtest else "niet getest",
        "learning": {"calibration": cal, "track_record": track_record(ev)},
        "disclaimer": DISCLAIMER,
    }


def _f(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", " ")


def to_markdown(a: dict) -> str:
    s, mc, dm, bt = a["snapshot"], a["monte_carlo"], a["direction_model"], a["backtest"]
    ind, lv = s["indicators"], s["levels"]
    L: list[str] = []
    L.append(f"# BTC perpetual – 5m analyse ({s['time'][:16].replace('T', ' ')} UTC)\n")
    ch = s["change_pct"]
    L.append(f"**Prijs:** {_f(s['price'])} USDT  |  5m {ch['5m']:+.2f}%  ·  1u {ch['1h']:+.2f}%  ·  "
             f"4u {ch['4h']:+.2f}%  ·  24u {ch['24h']:+.2f}%\n")
    L.append(f"**Technische bias:** {s['bias']} (score {s['score']:+d})  |  trend 5m: {s['trend_5m']}  |  "
             f"volatiliteit: {s['volatility_regime']}\n")

    L.append("## Indicatoren")
    htf = ", ".join(f"{k}: {v['trend']}" for k, v in s["higher_timeframes"].items()) or "n.v.t."
    vz = "n.v.t." if ind["volume_z"] is None else f"{ind['volume_z']:+.2f}"
    L.append(f"- EMA9 / 21 / 50 / 200: {_f(ind['ema9'])} / {_f(ind['ema21'])} / {_f(ind['ema50'])} / {_f(ind['ema200'])}")
    L.append(f"- RSI14: {ind['rsi14']:.1f}  ·  MACD-hist: {ind['macd_hist']:+.2f}  ·  Bollinger %B: {ind['bb_pctb']:.2f}"
             f" (breedte {ind['bb_width_pct']:.2f}%)")
    L.append(f"- ATR14: {_f(ind['atr14'])} ({ind['atr_pct_of_price']:.3f}% · percentiel {ind['atr_percentile_1000']:.0%})"
             f"  ·  dag-VWAP: {_f(ind['vwap'])}  ·  volume z-score: {vz}")
    L.append(f"- Hogere tijdframes: {htf}")
    L.append("- Scorekaart: " + "; ".join(s["reasons"]) + "\n")

    L.append("## Niveaus (laatste 24 uur)")
    res = ", ".join(f"{_f(x)} ({n}×)" for x, n in zip(lv["resistance"], lv["resistance_touches"]))
    sup = ", ".join(f"{_f(x)} ({n}×)" for x, n in zip(lv["support"], lv["support_touches"]))
    L.append(f"- Weerstand: {res or '—'}  (24u-high {_f(lv['high_24h'])})")
    L.append(f"- Steun: {sup or '—'}  (24u-low {_f(lv['low_24h'])})\n")

    d = a.get("derivatives") or {}
    if d and "error" not in d:
        L.append("## Futures-context")
        L.append(f"- Funding rate: {d['funding_rate'] * 100:+.4f}% per 8u (volgende: {d['next_funding_time'][:16]})")
        if d.get("premium") is not None:
            L.append(f"- Premium t.o.v. index: {d['premium'] * 100:+.4f}%")
        L.append(f"- Open interest: {_f(d['open_interest_btc'], 0)} BTC (≈ ${d['open_interest_usd'] / 1e9:.2f} mld)\n")

    L.append("## Kansverdeling komende candles (Monte Carlo, drift-neutraal)")
    L.append(f"Huidige 5m-volatiliteit (EWMA, geleerde factor ×{mc['vol_scale']:.2f}): "
             f"{mc['sigma_5m_pct']:.3f}% per candle.\n")
    L.append("| Horizon | 5% | 25% | mediaan | 75% | 95% | gem. beweging |")
    L.append("|---|---|---|---|---|---|---|")
    for k, h in mc["horizons"].items():
        L.append(f"| {k} | {_f(h['p05'])} | {_f(h['p25'])} | {_f(h['p50'])} | {_f(h['p75'])} | {_f(h['p95'])} | "
                 f"±{h['expected_abs_move_pct']:.2f}% |")
    last_h = list(mc["horizons"])[-1]
    tp = mc["horizons"][last_h]["touch_prob"]
    L.append(f"\nKans om binnen {last_h} te raken: " + ", ".join(f"{k}: {v:.0%}" for k, v in tp.items()) + "\n")

    L.append(f"## Richtingsmodel ({dm['horizon_min']} min vooruit)")
    L.append(f"- Kans op hogere close over {dm['horizon_min']} min: **{dm['p_up']:.1%}** "
             f"(ruw model {dm['p_up_raw']:.1%}, na kalibratie; basisratio {dm['base_rate_up']:.1%}, "
             f"n={dm['train_samples']})")
    L.append("- Zwaarste features: " + ", ".join(f"{k} ({v:+.2f})" for k, v in dm["top_features"].items()))
    if bt and "error" not in bt:
        ca = bt["confident_accuracy"]
        L.append(f"- Walk-forward backtest ({bt['oos_predictions']} out-of-sample voorspellingen): "
                 f"trefzekerheid {bt['accuracy']:.1%} vs. beste constante gok {bt['baseline_best']:.1%}; "
                 f"Brier {bt['brier']:.4f} (muntworp 0.25)")
        L.append(f"- Bij kans ≥55%/≤45% ({bt['confident_share']:.0%} van de tijd): trefzekerheid "
                 f"{'n.v.t.' if ca is None else f'{ca:.1%}'}")
        L.append(f"- Gem. netto rendement per trade na {bt['fee_roundtrip_pct']}% kosten: "
                 f"{bt['avg_net_return_pct_per_trade']:+.3f}% ({bt['trades_nonoverlap']} trades)")
    elif bt:
        L.append(f"- Backtest: {bt['error']}")
    L.append(f"- **Oordeel:** {a['model_verdict']}\n")

    lr = a.get("learning")
    if lr:
        c, tr = lr["calibration"], lr["track_record"]
        L.append("## Zelflerend: eigen track record & kalibratie")
        if tr.get("n_direction"):
            L.append(f"- Eigen richtingsvoorspellingen (sinds {tr['since']}, {tr['n_live']} live + "
                     f"{tr['n_replay']} teruggespeeld): {tr['n_direction']} getoetst, "
                     f"trefzekerheid {tr['hit_rate']:.1%} (aandeel stijgingen {tr['share_up']:.1%}); "
                     f"Brier ruw {tr['brier_raw']:.4f} → gekalibreerd {tr['brier_calibrated']:.4f} (muntworp 0.25)")
        else:
            L.append("- Nog geen eigen voorspellingen getoetst; kalibratie leunt op de backtest en de candles.")
        if tr.get("n_range"):
            cov50 = f", 50%-band {tr['coverage_50']:.0%} (doel 50%)" if "coverage_50" in tr else ""
            L.append(f"- Bandbreedte 60m getoetst op {tr['n_range']} voorspellingen: 90%-band raak in "
                     f"{tr['coverage_90']:.0%} (doel 90%){cov50}")
        hist = c["vol_scale_from_history"]
        L.append(f"- Geleerde volatiliteitsfactor: ×{c['vol_scale']:.2f} (candles ×{c['vol_scale_from_candles']:.2f}"
                 + (f", eigen track record ×{hist:.2f} op n={c['n_history_range']}" if hist else "") + ")")
        L.append(f"- Vertrouwen in richtingsmodel: {c['dir_scale']:.2f} (0 = negeren, 1 = ruwe kans klopt)\n")

    L.append(f"_Data: {a['candles']} candles, {a['data_from'][:16]} → {a['data_to'][:16]} UTC. {a['disclaimer']}_")
    return "\n".join(L)
