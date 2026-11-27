"""Eén geplande run (bijv. elke 15 minuten via GitHub Actions of cron).

Schrijft naar `outdir`:
- latest.json / latest.md  : volledige analyse van deze run
- agent.md                 : Claude-interpretatie (als ingeschakeld en gelukt)
- history.csv              : één regel per run, om de voorspellingen later te kunnen toetsen
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from .data import fetch_candles, fetch_derivatives_context
from .report import analyse, to_markdown

HISTORY_FIELDS = ["generated_at", "candle_time", "source", "price", "bias", "score", "volatility_regime",
                  "sigma_5m_pct", "p_up", "model_horizon_min", "p05_60m", "p50_60m", "p95_60m",
                  "funding_rate", "oos_accuracy", "oos_baseline"]


def _history_row(a: dict, source: str) -> dict:
    s, mc, dm, bt = a["snapshot"], a["monte_carlo"], a["direction_model"], a["backtest"]
    h60 = mc["horizons"].get("60m", {})
    return {
        "generated_at": a["generated_at"], "candle_time": s["time"], "source": source,
        "price": round(s["price"], 2), "bias": s["bias"], "score": s["score"],
        "volatility_regime": s["volatility_regime"], "sigma_5m_pct": round(mc["sigma_5m_pct"], 4),
        "p_up": round(dm["p_up"], 4), "model_horizon_min": dm["horizon_min"],
        "p05_60m": round(h60.get("p05", float("nan")), 1), "p50_60m": round(h60.get("p50", float("nan")), 1),
        "p95_60m": round(h60.get("p95", float("nan")), 1),
        "funding_rate": a["derivatives"].get("funding_rate", ""),
        "oos_accuracy": round(bt["accuracy"], 4) if "accuracy" in bt else "",
        "oos_baseline": round(bt["baseline_best"], 4) if "baseline_best" in bt else "",
    }


def run_once(outdir: str, sources: list[str], bars: int = 3000, agent: str = "auto") -> int:
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

    a = analyse(df, derivatives=fetch_derivatives_context(source))
    a["source"] = source
    md = to_markdown(a)
    (out / "latest.json").write_text(json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "latest.md").write_text(md, encoding="utf-8")

    hist = out / "history.csv"
    new_file = not hist.exists()
    with hist.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(_history_row(a, source))

    use_agent = agent == "on" or (agent == "auto" and os.environ.get("ANTHROPIC_API_KEY"))
    if use_agent:
        try:
            from .agent import summarise
            text = summarise(a)
            (out / "agent.md").write_text(text + "\n", encoding="utf-8")
            print(text)
        except Exception as err:  # analyse blijft bruikbaar, ook als Claude faalt
            print(f"Claude-samenvatting mislukt: {err}", file=sys.stderr)
            print(md)
    else:
        print(md)
    return 0
