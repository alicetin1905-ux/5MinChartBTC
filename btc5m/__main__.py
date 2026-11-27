"""Command line: `python -m btc5m analyse` / `python -m btc5m fetch`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import fetch_candles, fetch_derivatives_context, load_csv
from .report import analyse, to_markdown


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="btc5m", description="Analyse van de BTC futures 5m chart")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyse", aliases=["analyze"], help="Volledige analyse + kansverwachting")
    a.add_argument("--source", choices=["okx", "binance"], default="okx")
    a.add_argument("--bars", type=int, default=3000, help="Aantal 5m candles (standaard 3000 ≈ 10 dagen)")
    a.add_argument("--csv", help="Gebruik lokale CSV in plaats van live data")
    a.add_argument("--horizons", default="1,3,6,12", help="Horizons in candles, bijv. 1,3,6,12")
    a.add_argument("--model-horizon", type=int, default=3, help="Horizon richtingsmodel in candles")
    a.add_argument("--no-backtest", action="store_true", help="Sla walk-forward backtest over (sneller)")
    a.add_argument("--json", action="store_true", help="Geef JSON i.p.v. Markdown")
    a.add_argument("--out", help="Schrijf het resultaat ook naar dit bestand")
    a.add_argument("--history", help="history.csv van eerdere runs: toetsen en daarvan leren")

    f = sub.add_parser("fetch", help="Sla candles op als CSV")
    f.add_argument("--source", choices=["okx", "binance"], default="okx")
    f.add_argument("--bars", type=int, default=3000)
    f.add_argument("--out", default="data/btc_5m.csv")

    r = sub.add_parser("run", help="Eén geplande run: analyse, rapporten en geschiedenis bijwerken")
    r.add_argument("--outdir", default="reports")
    r.add_argument("--sources", default="okx,binance", help="Bronnen in volgorde van voorkeur")
    r.add_argument("--bars", type=int, default=8640, help="Standaard 8640 ≈ 30 dagen (meer leerdata)")
    r.add_argument("--agent", choices=["auto", "on", "off"], default="auto",
                   help="Claude-samenvatting: auto = alleen als ANTHROPIC_API_KEY gezet is")
    r.add_argument("--backfill-days", type=int, default=14,
                   help="Bij een (bijna) lege history.csv eerst zoveel dagen terugspelen (0 = uit)")

    args = p.parse_args(argv)

    if args.cmd == "run":
        from .scheduled import run_once
        return run_once(args.outdir, args.sources.split(","), args.bars, args.agent, args.backfill_days)

    if args.cmd == "fetch":
        df = fetch_candles(args.bars, args.source)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out)
        print(f"{len(df)} candles opgeslagen in {args.out} ({df.index[0]} → {df.index[-1]})")
        return 0

    if args.csv:
        df, deriv = load_csv(args.csv), {}
    else:
        df = fetch_candles(args.bars, args.source)
        deriv = fetch_derivatives_context(args.source)
    horizons = tuple(int(x) for x in args.horizons.split(","))
    history = None
    if args.history:
        from .scheduled import load_history
        history = load_history(Path(args.history))
    result = analyse(df, horizons=horizons, model_horizon=args.model_horizon,
                     derivatives=deriv, backtest=not args.no_backtest, history=history)
    result.pop("extras", None)  # modelparameters/z-sample alleen voor het dashboard
    text = json.dumps(result, indent=2, ensure_ascii=False) if args.json else to_markdown(result)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
