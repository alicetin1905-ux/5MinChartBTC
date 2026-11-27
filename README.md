# 5MinChartBTC – BTC futures 5m analist

Een agent die de **5-minuten chart van BTC perpetual futures** analyseert en een
**kansgebaseerde verwachting** geeft voor de komende 5–60 minuten.

Het project heeft twee delen:

1. **`btc5m`**: een Python-toolkit die live candles ophaalt, indicatoren berekent en
   kansverdelingen simuleert. Je hebt er geen API-key voor nodig.
2. **De Claude Code-agent `btc-5m-analist`** (`.claude/agents/`): die draait de toolkit,
   leest de cijfers en schrijft er een Nederlandse analyse met scenario's bij.

## Snel starten

```bash
pip install -r requirements.txt
python3 -m btc5m analyse                 # Markdown-rapport
python3 -m btc5m analyse --json          # ruwe cijfers (gebruikt door de agent)
python3 -m btc5m analyse --source binance --horizons 1,3,6,12,24
python3 -m btc5m fetch --bars 4000       # candles opslaan als CSV
python3 -m btc5m analyse --csv data/btc_5m.csv   # offline analyse
```

In Claude Code (vanuit deze map):

- `/btc5m`, of
- vraag bijvoorbeeld: *"Gebruik de btc-5m-analist: wat doet BTC het komende uur?"*

## Automatisch elke 15 minuten

De workflow `.github/workflows/btc5m-schedule.yml` draait elke 15 minuten via GitHub
Actions `python -m btc5m run`. Daarna:

- staat de samenvatting bij elke run in het tabblad **Actions** (job summary);
- komen de rapporten op de aparte branch **`btc-reports`**:
  - `latest.md` en `latest.json`: de meest recente analyse;
  - `agent.md`: de interpretatie door Claude (alleen als die is ingeschakeld, zie hieronder);
  - `history.csv`: één regel per run; volgende runs toetsen deze voorspellingen en leren ervan;
  - `learning.json`: het track record en de geleerde kalibratie.

Je kunt een run ook met de hand starten: Actions → *BTC 5m analyse* → **Run workflow**.

**Claude-interpretatie aanzetten (optioneel):** voeg onder Settings → Secrets and variables →
Actions het secret `ANTHROPIC_API_KEY` toe. Elke run laat Claude dan de cijfers
interpreteren, op dezelfde manier als de agent `btc-5m-analist`. Het model kies je met de
repository-variabele `CLAUDE_MODEL` (standaard `claude-opus-5-5`). Let op: dit kost
API-tegoed voor 96 runs per dag. Zonder het secret draait alleen de gratis cijferanalyse.

Liever lokaal? Zet in je crontab:
`*/15 * * * * cd /pad/naar/5MinChartBTC && python3 -m btc5m run --outdir reports`

## Zelflerend

Bij elke run toetst de agent zijn **eigen eerdere voorspellingen** uit `history.csv` aan wat er
daarna echt gebeurde, en past zich daarop aan (`btc5m/learn.py`):

| Wat | Hoe hij leert | Effect |
|---|---|---|
| **Bandbreedte** (`vol_scale`) | Hoe vaak viel de prijs na 60 min echt binnen de 90%-band? Te vaak erbuiten: banden breder. Te vaak erbinnen: smaller. | Kansranges kloppen beter met de werkelijkheid |
| **Vertrouwen in de richting** (`dir_scale`) | Hoe goed voorspelden eerdere kansen de echte richting (backtest + eigen track record)? | De ruwe kans wordt naar 50% getrokken als het model weinig voorspelt; 0 = negeren, 1 = volledig vertrouwen |
| **Model** | Wordt elke run opnieuw getraind op de nieuwste ~30 dagen candles | Past zich aan nieuwe marktomstandigheden aan |

Bij de eerste run (lege `history.csv`) speelt hij de afgelopen 14 dagen terug, elke 15 minuten
en telkens alleen met de data die op dat moment bekend was. Zo heeft hij meteen een track record.
Die teruggespeelde voorspellingen worden apart geteld van de echte live voorspellingen.
De uitkomst van elke run staat in `learning.json` en in de sectie *Zelflerend* van het rapport.

Leren betekent hier **eerlijker worden over onzekerheid**, niet dat hij gaandeweg de koers
beter gaat voorspellen. Als de richting in de data toeval is, leert hij die richtingskans juist
minder zwaar te laten wegen.

## Wat er berekend wordt

| Onderdeel | Inhoud |
|---|---|
| Data | OKX `BTC-USDT-SWAP` (standaard) of Binance `BTCUSDT` perpetual, alleen afgesloten 5m candles, plus funding rate, premium en open interest |
| Indicatoren | EMA 9/21/50/200, RSI14, MACD, Bollinger (%B en breedte), ATR14, dag-VWAP, volume z-score |
| Context | trend op 15m en 1u, volatiliteitsregime (ATR-percentiel), steun/weerstand uit geclusterde swing-pivots |
| Scorekaart | optelsom van trend-, momentum- en VWAP-signalen, met als uitkomst een bias (bullish … bearish) |
| **Bandbreedte** | Monte Carlo (*filtered historical simulation*): historische rendementen opnieuw getrokken met EWMA-volatiliteit, 20 000 paden. Geeft de 5/25/50/75/95%-prijsniveaus per horizon en de kans om ±0,25/0,5/1% of steun/weerstand te raken |
| **Richting** | logistische regressie (L2) die de kans schat op een hogere close na N candles |
| **Eerlijkheidscheck** | walk-forward backtest van het richtingsmodel, alleen out-of-sample en zonder look-ahead: trefzekerheid tegenover de beste constante gok, Brier-score en rendement na kosten |

## Verwachtingen

Op de 5m-timeframe is de richting van BTC grotendeels ruis. In de backtests tijdens het
bouwen (4000 candles, november 2026) haalde het richtingsmodel ongeveer **50–52%**
trefzekerheid. Dat is niet beter dan de beste constante gok, en na kosten leverde het
geen winst op. Het rapport en de agent zeggen dat er daarom altijd expliciet bij.

Het betrouwbaarste onderdeel is de **bandbreedte**: hoe ver de prijs waarschijnlijk
beweegt, en hoe groot de kans is dat een bepaald niveau geraakt wordt. Gebruik de output
als hulpmiddel en niet als handelssignaal. **Geen financieel advies.**

## Tests

```bash
python3 -m pytest -q
```

De tests draaien op synthetische data en hebben geen netwerk nodig. Ze controleren onder
meer dat het model op een random walk géén voorspelkracht vindt.
