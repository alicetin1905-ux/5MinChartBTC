"""Eén geplande run (bijv. elke 15 minuten via GitHub Actions of cron).

Schrijft naar `outdir`:
- latest.json / latest.md  : volledige analyse van deze run
- candles.json             : laatste 24 uur aan 5m candles (voor het dashboard)
- agent.md                 : Claude-interpretatie (als ingeschakeld en gelukt)
- history.csv              : één regel per run; volgende runs toetsen deze voorspellingen en
                             leren daarvan (zie learn.py)
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import pandas as pd

from .data import fetch_candles, fetch_derivatives_context
from .report import analyse, to_markdown

HISTORY_FIELDS = ["generated_at", "candle_time", "source", "price", "bias", "score", "volatility_regime",
                  "sigma_5m_pct", "p_up", "p_up_raw", "model_horizon_min", "p05_60m", "p25_60m", "p50_60m",
                  "p75_60m", "p95_60m", "vol_scale", "dir_scale", "funding_rate", "oos_accuracy", "oos_baseline"]


def _history_row(a: dict, source: str) -> dict:
    s, mc, dm, bt = a["snapshot"], a["monte_carlo"], a["direction_model"], a["backtest"]
    h60 = mc["horizons"].get("60m", {})
    return {
        "generated_at": a["generated_at"], "candle_time": s["time"], "source": source,
        "price": round(s["price"], 2), "bias": s["bias"], "score": s["score"],
        "volatility_regime": s["volatility_regime"], "sigma_5m_pct": round(mc["sigma_5m_pct"], 4),
        "p_up": round(dm["p_up"], 4), "p_up_raw": round(dm["p_up_raw"], 4), "model_horizon_min": dm["horizon_min"],
        **{f"{q}_60m": round(h60.get(q, float("nan")), 1) for q in ("p05", "p25", "p50", "p75", "p95")},
        "vol_scale": a["learning"]["calibration"]["vol_scale"],
        "dir_scale": a["learning"]["calibration"]["dir_scale"],
        "funding_rate": a["derivatives"].get("funding_rate", ""),
        "oos_accuracy": round(bt["accuracy"], 4) if "accuracy" in bt else "",
        "oos_baseline": round(bt["baseline_best"], 4) if "baseline_best" in bt else "",
    }


def load_history(path: Path) -> pd.DataFrame:
    """Lees history.csv en zet het om naar de huidige kolomindeling (oudere runs missen kolommen)."""
    if not path.exists():
        return pd.DataFrame(columns=HISTORY_FIELDS)
    h = pd.read_csv(path)
    if list(h.columns) != HISTORY_FIELDS:
        h = h.reindex(columns=HISTORY_FIELDS)
        h.to_csv(path, index=False)
    return h


def replay_history(df: pd.DataFrame, days: int = 14, every: int = 3, lookback: int = 3000,
                   source: str = "okx") -> pd.DataFrame:
    """Bootstrap het track record: doe alsof de analyse de afgelopen `days` dagen elke `every` candles
    draaide, telkens met alleen de data die toen bekend was. Rijen krijgen source 'replay:<bron>'."""
    rows = []
    last = len(df) - 1
    first = max(lookback, last - days * 288)
    for i in range(first, last, every):
        window = df.iloc[i - lookback + 1:i + 1]
        a = analyse(window, backtest=False)
        row = _history_row(a, f"replay:{source}")
        row["generated_at"] = a["snapshot"]["time"]
        rows.append(row)
    return pd.DataFrame(rows, columns=HISTORY_FIELDS)


def run_once(outdir: str, sources: list[str], bars: int = 8640, agent: str = "auto",
             backfill_days: int = 14) -> int:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    df, source, errors = None, None, []
    for src in sources:
        try:
            df, source = fetch_candles(bars, src), src
            break
        except Exception as err:
            errors.append(f"{src}: {err}")
    if df is None:
        print("Geen data op te halen:\n" + "\n".join(errors), file=sys.stderr)
        return 1

    hist = out / "history.csv"
    history = load_history(hist)
    has_replay = history["source"].astype(str).str.startswith("replay").any()
    if backfill_days and not has_replay and len(history) < 200:
        print(f"Track record opbouwen: replay van de laatste {backfill_days} dagen...", file=sys.stderr)
        replay = replay_history(df, days=backfill_days, source=source)
        history = pd.concat([replay, history], ignore_index=True) if len(history) else replay
        history = history.sort_values("candle_time", kind="stable")
        history.to_csv(hist, index=False)
    a = analyse(df, derivatives=fetch_derivatives_context(source), history=history)
    a["source"] = source
    md = to_markdown(a)
    (out / "latest.md").write_text(md, encoding="utf-8")

    recent = df.tail(288)  # laatste 24 uur voor de grafiek op het dashboard
    candles = [[int(t.timestamp() * 1000), *(round(float(x), 2) for x in row)]
               for t, row in zip(recent.index, recent[["open", "high", "low", "close", "volume"]].to_numpy())]
    (out / "candles.json").write_text(json.dumps(candles), encoding="utf-8")
    (out / "learning.json").write_text(json.dumps(a["learning"], indent=2, ensure_ascii=False), encoding="utf-8")
    new_file = not hist.exists()
    with hist.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(_history_row(a, source))

    use_agent = agent == "on" or (agent == "auto" and os.environ.get("ANTHROPIC_API_KEY"))
    a["agent_summary"] = None
    if use_agent:
        try:
            from .agent import summarise
            a["agent_summary"] = summarise(a)
        except Exception as err:  # analyse blijft bruikbaar, ook als Claude faalt
            print(f"Claude-samenvatting mislukt: {err}", file=sys.stderr)
    agent_file = out / "agent.md"
    if a["agent_summary"]:
        agent_file.write_text(a["agent_summary"] + "\n", encoding="utf-8")
    elif agent_file.exists():
        agent_file.unlink()  # geen verouderde interpretatie laten staan
    (out / "latest.json").write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8")
    print(a["agent_summary"] or md)
    return 0
