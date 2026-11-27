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
- komen de rapporten op de aparte branch **`btc-reports`**. Die branch wordt elke run vervangen door
  één verse commit, zodat de repository niet ongemerkt groeit; de groeiende datasets
  (`history.csv`, `orderflow.csv`, `book.csv`) gaan elke keer mee:
  - `latest.md` en `latest.json`: de meest recente analyse;
  - `agent.md`: de interpretatie door Claude (alleen als die is ingeschakeld, zie hieronder);
  - `history.csv`: één regel per run; volgende runs toetsen deze voorspellingen en leren ervan;
  - `learning.json`: het track record en de geleerde kalibratie.

- staat er een **interactief live dashboard** op GitHub Pages:
  <https://alicetin1905-ux.github.io/5MinChartBTC/>. Eenmalig instellen: Settings → Pages →
  *Deploy from a branch* → `btc-reports` / `(root)`.

### Het dashboard

Het dashboard haalt de koersen **rechtstreeks in je browser** binnen, van OKX: elke trade via
een websocket. Alle berekeningen draaien zichtbaar mee in `site/engine.js`, met dezelfde formules
als de Python-code. Een test (`tests/test_engine_parity.py`) controleert dat de uitkomsten
gelijk zijn.

- **Live candlestick-grafiek**: de lopende candle beweegt mee met elke trade. Je kunt EMA 9/21/50,
  VWAP, Bollinger en steun/weerstand aan- en uitzetten, het bereik kiezen (3 tot 24 uur) en met de
  muis of de pijltjestoetsen over de candles en de verwachting bewegen.
- **Monte Carlo, live te zien**: je ziet tot 10.000 koerspaden uitwaaieren en het histogram van de
  eindprijzen opbouwen, waarna de 50%- en 90%-banden verschijnen. Horizon, aantal paden en
  een extra volatiliteitsfactor (wat-als) stel je zelf in, bovenop de geleerde kalibratie. De simulatie
  draait opnieuw bij elke nieuwe candle.
- **Richtingsmodel open en bloot**: per feature zie je hoeveel die de kans omhoog of omlaag duwt.
  Daaronder staan de som en de sigmoïde, en hoe dat via de geleerde vertrouwensfactor de
  eindkans geeft. Dit wordt bij elke tick herberekend.
- **Indicatoren live**: RSI-meter, MACD-histogram, Bollinger %B, volatiliteit en de scorekaart.
  Een stem die omslaat, licht even op.
- **Live transacties**: elke trade verschijnt meteen.
- **Zelflerend**: voorspelde banden naast de werkelijke uitkomsten, een kalibratiegrafiek, en wat
  het systeem daarvan geleerd heeft.

Kan de websocket geen verbinding maken, dan valt het dashboard terug op REST (elke 3 seconden).
Met `?demo=1` achter de URL zie je een gesimuleerde feed.

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
| **Kalibratie per band** (`c50`, `c90` → `k50`, `k90`) | Hoe ver lag de koers na 60 min werkelijk van de mediaan, gemeten in de voorspelde volatiliteit? Dit wordt apart bepaald voor de 50%- en de 90%-band, eerst uit 30 dagen candles en daarna steeds meer uit het eigen track record. | BTC heeft dikke staarten: meestal rustig, af en toe een sprong. Daarom worden de banden apart gerekt of gekrompen, zodat beide kloppen |
| **Volatiliteit per uur** | Variantie per uur van de dag, apart voor werkdag en weekend (met krimp naar het gemiddelde) | Banden zijn breder rond de Amerikaanse opening (13–15 uur UTC, ongeveer ×1,5) en smaller 's nachts en in het weekend (ongeveer ×0,85) |
| **Vertrouwen in de richting** (`dir_scale`) | Hoe goed voorspelden eerdere kansen de echte richting (backtest + eigen track record)? | De ruwe kans wordt naar 50% getrokken als het model weinig voorspelt |
| **Macro-agenda** (`events.csv`) | Op het moment van CPI-cijfers, banenrapporten en Fed-besluiten beweegt BTC veel harder. Het model leert uit eerdere publicaties hoeveel harder: op 5 publicaties in okt–nov 2026 was de variantie in de publicatie-candle gemiddeld ~29× normaal (mediaan ~7×). | Banden worden rond zo'n moment automatisch breder. In een leave-one-out test steeg de log-likelihood van voorspellingen met een macro-moment van 2,54 naar 3,17. Vul de agenda aan met de officiële planning (bls.gov, federalreserve.gov) |
| **Orderflow** | Elke run slaat de verhouding agressieve kopers/verkopers, open interest, de long/short-verhouding en een momentopname van het orderboek op (`orderflow.csv`, `book.csv`). OKX bewaart die data zelf maar 8 tot 48 uur. | Na ongeveer 7 dagen volgt automatisch een eerlijke walk-forward test: voegt orderflow iets toe aan het richtingsmodel? |
| **Model** | Wordt elke run opnieuw getraind op de nieuwste ~30 dagen | Past zich aan nieuwe marktomstandigheden aan |

**Getoetst op 60 dagen data, op 360 momenten die buiten de schattingsperiode vielen:**

| | 50%-band raak | 90%-band raak | gemiddelde breedte 90%-band |
|---|---|---|---|
| oud | 55,0% | 88,3% | 1,57% |
| nieuw (kalibratie per band + seizoen) | 52,2% | 89,2% | 1,54% |

De volatiliteit per uur verbeterde daarnaast de log-likelihood van de 60m-bewegingen
van 3,757 naar 3,805.

**Getest en niet ingebouwd:** een extra factor rond de funding-momenten (00:00, 08:00 en 16:00 UTC).
Na correctie voor het uur was de variantie daar ×1,10 (trainingsperiode) en ×1,04 (testperiode),
en dat valt binnen de ruis.

**Scorebord:** elke getoetste voorspelling krijgt een *weighted interval score*. Die bestraft
zowel te brede banden als uitkomsten buiten de band. De score wordt vergeleken met een naïeve band:
de spreiding van de 60m-bewegingen in de 24 uur ervoor. Het dashboard toont per dag hoeveel beter
het model is dan naïef, per modelversie (`model_version` in `history.csv`). Zo kun je elke
aanpassing objectief beoordelen.

Bij de eerste run speelt hij de afgelopen 14 dagen terug, zodat hij meteen een track record
heeft. Leren betekent hier vooral **eerlijker worden over onzekerheid**, niet dat hij de
richting gaandeweg beter gaat voorspellen.

## Wat er berekend wordt

| Onderdeel | Inhoud |
|---|---|
| Data | OKX `BTC-USDT-SWAP` (standaard); reserve: Kraken Futures `PF_XBTUSD` (perpetual), dan Coinbase `BTC-USD` (spot); Binance `BTCUSDT` alleen handmatig, alleen afgesloten 5m candles, plus funding rate, premium en open interest |
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
