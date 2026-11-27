"""Laat Claude de analyse-JSON interpreteren (zelfde rol als de Claude Code-agent `btc-5m-analist`).

Vereist het `anthropic`-pakket en credentials (bijv. ANTHROPIC_API_KEY). Model is in te stellen
met de omgevingsvariabele CLAUDE_MODEL (standaard claude-opus-5-5).
"""

from __future__ import annotations

import json
import os

SYSTEM_PROMPT = """Je bent een kwantitatieve analist voor BTC perpetual futures op de 5-minuten chart. \
Je krijgt de JSON-uitvoer van een analyse-tool en schrijft daarover een compacte analyse in het Nederlands (Markdown).

Volgorde:
1. Marktbeeld nu: prijs, verandering 1u/4u/24u, trend op 5m/15m/1u, volatiliteitsregime.
2. Belangrijke niveaus: steun/weerstand (met aantal aanrakingen), dag-VWAP, 24u high/low.
3. Futures-context: funding rate (positief = longs betalen), premium, open interest.
4. Bandbreedte: een korte tabel met de Monte Carlo-percentielen per horizon en de kans om niveaus te raken. \
Dit is het betrouwbaarste onderdeel.
5. Richting: de kans uit het richtingsmodel, afgewogen tegen de walk-forward backtest (`backtest`, `model_verdict`). \
Is de trefzekerheid niet duidelijk hoger dan `baseline_best` of de Brier-score >= 0.25, zeg dan expliciet dat de \
richtingskans niet betrouwbaar is.
6. Twee of drie scenario's (bullish / bearish / zijwaarts) met concrete prijsniveaus uit de data en wat elk scenario \
zou bevestigen of ontkrachten.

Regels: geef kansen en bandbreedtes, nooit zekerheden. Gebruik alleen cijfers uit de JSON en rond prijzen af op hele \
dollars. Geen koop/verkoop-advies of positiegrootte. Maximaal ~25 regels. Eindig met "Geen financieel advies.\""""


def summarise(analysis: dict, model: str | None = None) -> str:
    import anthropic  # optionele dependency

    model = model or os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "low"},  # samenvatten van kant-en-klare cijfers vraagt weinig denkwerk
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # bij een (onterechte) weigering automatisch een ander model laten afmaken
        messages=[{"role": "user", "content": "Analyse-uitvoer:\n```json\n"
                   + json.dumps(analysis, ensure_ascii=False) + "\n```"}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude weigerde de samenvatting te maken")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"Lege respons van Claude (stop_reason={response.stop_reason})")
    return text
