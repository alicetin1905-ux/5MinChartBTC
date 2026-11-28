# BTC perpetual – 5m analyse (2026-11-28 19:55 UTC)

**Prijs:** 59 318.9 USDT  |  5m -0.00%  ·  1u -0.23%  ·  4u -0.24%  ·  24u -0.94%

**Technische bias:** bearish (score -8)  |  trend 5m: sterk dalend  |  volatiliteit: laag

## Indicatoren
- EMA9 / 21 / 50 / 200: 59 322.6 / 59 351.3 / 59 371.2 / 59 518.2
- RSI14: 44.4  ·  MACD-hist: -1.87  ·  Bollinger %B: 0.36 (breedte 0.50%)
- ATR14: 60.2 (0.101% · percentiel 2%)  ·  dag-VWAP: 59 512.5  ·  volume z-score: -0.85
- Hogere tijdframes: 15min: sterk dalend, 1h: sterk dalend
- Scorekaart: -1 EMA9 onder EMA21; -1 prijs onder EMA50; -1 prijs onder EMA200; -1 MACD-histogram negatief; -1 prijs onder dag-VWAP; -1 RSI bearish zone; -1 15min-trend sterk dalend; -1 1h-trend sterk dalend

## Niveaus (laatste 24 uur)
- Weerstand: 59 364.7 (3×), 59 434.6 (1×), 59 480.9 (2×)  (24u-high 60 244.9)
- Steun: 59 277.2 (2×), 59 181.7 (3×), 59 052.7 (1×)  (24u-low 58 903.8)

## Futures-context
- Funding rate: +0.0059% per 8u (volgende: 2026-11-29T00:00)
- Premium t.o.v. index: -0.0352%
- Open interest: 32 419 BTC (≈ $1.92 mld)

## Kansverdeling komende candles (Monte Carlo, drift-neutraal)
Verwachte 5m-volatiliteit nu: 0.091% per candle (seizoensfactor dit uur ×0.94); bandkalibratie 50% ×0.94, 90% ×1.08.

| Horizon | 5% | 25% | mediaan | 75% | 95% | gem. beweging |
|---|---|---|---|---|---|---|
| 5m | 59 232.9 | 59 291.2 | 59 319.0 | 59 348.8 | 59 404.5 | ±0.07% |
| 15m | 59 172.4 | 59 269.8 | 59 319.2 | 59 368.9 | 59 469.5 | ±0.11% |
| 30m | 59 109.0 | 59 248.4 | 59 318.5 | 59 388.4 | 59 529.6 | ±0.16% |
| 60m | 59 022.6 | 59 218.6 | 59 318.6 | 59 416.9 | 59 618.2 | ±0.23% |

Kans om binnen 60m te raken: +0.25%: 27%, -0.25%: 27%, +0.5%: 7%, -0.5%: 7%, +1.0%: 1%, -1.0%: 1%, resistance@59364.7: 63%, support@59277.2: 64%

## Richtingsmodel (15 min vooruit)
- Kans op hogere close over 15 min: **50.3%** (ruw model 50.5%, na kalibratie; basisratio 47.5%, n=3000)
- Zwaarste features: r12 (-0.06), volz (-0.06), macd (-0.04), hour_cos (+0.03), r6 (-0.03)
- Walk-forward backtest (5587 out-of-sample voorspellingen): trefzekerheid 53.4% vs. beste constante gok 53.0%; Brier 0.2486 (muntworp 0.25)
- Bij kans ≥55%/≤45% (36% van de tijd): trefzekerheid 54.9%
- Gem. netto rendement per trade na 0.05% kosten: -0.045% (1863 trades)
- **Oordeel:** marginaal beter dan toeval – vooral als tiebreaker gebruiken

## Zelflerend: eigen track record & kalibratie
- Eigen richtingsvoorspellingen (sinds 2026-11-13 10:35, 128 live + 1344 teruggespeeld): 1472 getoetst, trefzekerheid 52.3% (aandeel stijgingen 48.7%); Brier ruw 0.2505 → gekalibreerd 0.2495 (muntworp 0.25)
- Bandbreedte 60m getoetst op 1469 voorspellingen: 90%-band raak in 88% (doel 90%), 50%-band 55% (doel 50%)
- Geleerde bandafstanden (in σ): 50% 0.53, 90% 1.60 (candles 0.55/1.63, eigen track record 0.51/1.55 op n=120) → correctie 50%-band ×0.94, 90%-band ×1.08
- Vertrouwen in richtingsmodel: 0.63 (0 = negeren, 1 = ruwe kans klopt)
- Scorebord (laatste 7 dagen, 666 voorspellingen): +4.3% beter dan een naïeve band (WIS 0.277% vs 0.290%); dekking 50% 55%, 90% 88%
- Orderflow: 476 van 2000 candles verzameld (1.7 dagen); test volgt zodra er genoeg is

_Data: 8640 candles, 2026-10-29T20:00 → 2026-11-28T19:55 UTC. Dit is een statistische analyse, geen financieel advies. 5-minuten bewegingen van BTC zijn grotendeels ruis; gebruik de kansen als bandbreedte, niet als zekerheid._