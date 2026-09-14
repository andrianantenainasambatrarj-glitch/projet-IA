"""Service d'analyse : orchestration RAG + moteur technique + LLM vision."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from ..analysis import AnalysisResult, analyze, render_report, to_rag_query
from ..config import Settings, get_settings
from ..knowledge import read_stats
from ..llm import LLMError, clear_llm_error, get_llm, record_llm_error
from ..market import POPULAR_SYMBOLS, MarketDataError, get_instrument
from ..prompts import DISCLAIMER, ANALYST_SYSTEM, build_analysis_prompt
from ..reports import get_report_store
from ..retriever import build_context, search, search_multi
from ..vision import ChartObservation, read_chart, validate_image

# --------------------------------------------------------------------- #
#  Modèles d'entrée / sortie
# --------------------------------------------------------------------- #


@dataclass
class AnalysisRequest:
    """Paramètres d'une demande d'analyse."""

    symbol: str = ""
    period: str = "6mo"
    interval: str = "1d"
    question: str = ""
    image: Optional[bytes] = None
    top_k: int = 5
    analyze_detected_symbol: bool = True
    save: bool = True


@dataclass
class AnalysisResponse:
    """Résultat complet renvoyé à l'interface."""

    answer: str
    mode: str  # "ia" | "demo"
    provider: str = ""
    model: str = ""
    analysis: Optional[dict[str, Any]] = None
    observation: Optional[dict[str, Any]] = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    rag_mode: str = ""
    warnings: list[str] = field(default_factory=list)
    report_id: str = ""
    request: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "analysis": self.analysis,
            "observation": self.observation,
            "sources": self.sources,
            "rag_mode": self.rag_mode,
            "warnings": self.warnings,
            "report_id": self.report_id,
            "request": self.request,
        }


# --------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------- #

#: Symboles en MAJUSCULES dans le texte d'origine (un symbole boursier s'écrit en
#: capitales) : AAPL, BTC-USD, EURUSD=X, ^FCHI, MC.PA…
_SYMBOL_PATTERN = re.compile(r"(\^[A-Z]{1,6}|[A-Z]{1,6}(?:[.\-=][A-Z]{1,5})+)")

#: Faux positifs fréquents (unités de temps, indicateurs, mots courants en capitales).
_NOT_A_SYMBOL = {
    "D1", "H1", "H4", "M1", "M5", "M15", "M30", "W1", "MN", "MIN", "H", "D", "W", "M",
    "RSI", "MACD", "EMA", "SMA", "ATR", "ADX", "BB", "OBV", "TP", "SL", "RR", "PDF",
    "JSON", "CSV", "IA", "OK", "OUI", "NON", "ON", "LE", "LA", "LES", "UN", "UNE",
    "DES", "DU", "DE", "ET", "OU", "EN", "SUR", "SOUS", "PAR", "POUR", "VOIR", "ON",
    "EST", "SONT", "CECI", "CELA", "ICI", "AVEC", "SANS", "TOUT", "PLUS", "MOINS",
    "JPY", "USD", "EUR", "GBP", "CHF", "AUD", "CAD", "GB", "US", "EU", "GRAPH",
}


def guess_symbol(text: str) -> str:
    """Extrait un symbole plausible d'une description d'image.

    Un symbole est écrit en majuscules dans le texte (« AAPL », « BTC-USD ») ou
    comporte un séparateur explicite ; les mots courants en capitales sont rejetés.
    """
    if not text:
        return ""

    # 1) Symboles connus (liste curée) : le plus fiable.
    upper = text.upper()
    for item in POPULAR_SYMBOLS:
        symbole = item["symbol"]
        if symbole in upper:
            return symbole

    # 2) Motifs explicites : séparateur (. - =) ou préfixe ^
    for match in _SYMBOL_PATTERN.findall(text):
        candidate = match.strip()
        if candidate.upper() in _NOT_A_SYMBOL or candidate.upper() in {
            item["symbol"] for item in POPULAR_SYMBOLS
        }:
            continue
        if len(candidate.replace("^", "").replace("-", "").replace(".", "")) >= 2:
            return candidate.upper()

    # 3) Sigle en capitales isolé (ex. « NVDA ») hors mots courants
    for match in re.findall(r"\b([A-Z]{2,6})\b", text):
        if match in _NOT_A_SYMBOL:
            continue
        return match
    return ""


def _market_context(instrument) -> str:
    if instrument is None:
        return "Aucune donnée de marché chiffrée (analyse basée sur l'image seule)."
    candles = instrument.candles
    closes = [candle.c for candle in candles]
    return (
        f"- Symbole : {instrument.symbol} ({instrument.name or 'nom inconnu'}), "
        f"devise {instrument.currency}, unité de temps {instrument.timeframe}, "
        f"source {instrument.source}\n"
        f"- Bougies analysées : {len(candles)} (dernière : {candles[-1].day})\n"
        f"- Dernier prix : {closes[-1]:.6f}\n"
        f"- Plus haut / plus bas de la période : {max(candle.h for candle in candles):.6f} / "
        f"{min(candle.l for candle in candles):.6f}"
        + ("\n- " + "\n- ".join(instrument.notes) if instrument.notes else "")
    )


def _render_demo_answer(
    *,
    analysis: Optional[AnalysisResult],
    observation: Optional[ChartObservation],
    sources: Sequence[dict[str, Any]],
    question: str,
    warnings: Sequence[str],
    rag_mode: str,
    fallback_reason: str = "",
) -> str:
    """Réponse complète produite sans LLM : moteur technique + cours indexés."""
    lines: list[str] = []
    if fallback_reason == "llm_error":
        # Une clé est configurée mais l'appel a échoué : ne pas induire en erreur.
        detail = ""
        for avertissement in warnings:
            if "Appel LLM impossible" in avertissement or "Erreur LLM" in avertissement:
                detail = avertissement
                break
        lines.append(
            "> ⚙️ **Repli local** : votre clé API est bien configurée, mais l'appel au "
            "fournisseur IA a échoué — cette réponse est donc produite par le moteur "
            "technique local et vos cours indexés.\n"
        )
        if detail:
            lines.append(f"> 🔎 *Détail technique : {detail}*\n")
        lines.append(
            "> 💡 Vérifiez la variable `GEMINI_API_KEY`, le modèle utilisé "
            "(`VISION_MODEL`, laisser vide = détection automatique) et l'état du service "
            "sur `/api/health`.\n"
        )
    else:
        lines.append(
            "> ⚙️ **Mode démo (sans clé LLM)** : cette réponse est générée par le moteur "
            "technique local et vos cours indexés. Ajoutez une clé API gratuite "
            "(GEMINI_API_KEY) pour activer la lecture d'image par IA et la rédaction par LLM.\n"
        )

    if observation is not None and not observation.available:
        lines.append(f"> 🖼️ Lecture de l'image indisponible : {observation.error}\n")

    lines.append("### 1. Lecture du graphique")
    if analysis is not None:
        trend = analysis.trend
        lines.append(
            f"- Tendance : **{trend['label']}** — {trend['structure']} "
            f"(ADX {trend['adx']}, pente SMA50 {trend['slope_ma50']:+.2f} %)."
        )
        levels = analysis.levels
        nearest_support = levels.get("nearest_support")
        nearest_resistance = levels.get("nearest_resistance")
        lines.append(
            f"- Prix : {analysis.price:.6f} | Position dans le range : "
            f"{levels['range_position_pct']} % "
            f"(haut {levels['period_high']:.6f} / bas {levels['period_low']:.6f})."
        )
        lines.append(
            "- Support le plus proche : "
            + (f"{nearest_support['price']:.6f} ({nearest_support['touches']} touches)" if nearest_support else "non identifié")
            + " | Résistance la plus proche : "
            + (f"{nearest_resistance['price']:.6f} ({nearest_resistance['touches']} touches)" if nearest_resistance else "non identifiée")
        )
        snapshot = analysis.indicators
        lines.append(
            f"- Indicateurs : RSI(14) {snapshot['rsi14']}, MACD "
            f"{'haussier' if snapshot['macd_histogram'] > 0 else 'baissier'}, "
            f"ATR(14) {snapshot['atr14']} ({snapshot['atr_pct']} % du prix), "
            f"volume {snapshot['volume_ratio']}× la moyenne."
        )
        for reading in snapshot.get("readings", [])[:4]:
            lines.append(f"- {reading}")
        if analysis.patterns:
            lines.append("- Figures détectées :")
            for pattern in analysis.patterns[:6]:
                lines.append(
                    f"  - **{pattern.name}** ({pattern.bias}, confiance "
                    f"{pattern.confidence:.0%}) : {pattern.description}"
                )
        else:
            lines.append("- Aucune figure chartiste nette sur la période analysée.")
    else:
        lines.append(
            "- Aucune donnée de marché chiffrée disponible : fournissez un symbole "
            "(ex : AAPL, BTC-USD, ^FCHI) ou configurez une clé LLM pour lire l'image."
        )

    if observation is not None and observation.available:
        lines.append("\n### 1 bis. Ce que l'IA voit sur l'image")
        lines.append(observation.render())

    lines.append("\n### 2. Ce que dit la méthode (cours)")
    if sources:
        for index, source in enumerate(sources, start=1):
            reference = source.get("title", "document")
            section = source.get("section") or ""
            page = f", p.{source['page']}" if source.get("page") else ""
            lines.append(f"- **[Source {index}] {reference}{f' › {section}' if section else ''}{page}**")
            lines.append(f"  > {source.get('extract', '')}")
    else:
        lines.append(
            "- Aucun extrait de cours récupéré : ajoutez vos PDF/notes via l'onglet "
            "« Base de connaissances » pour que l'analyse s'appuie sur votre méthode."
        )
        lines.append(f"- Mode de recherche utilisé : {rag_mode or 'indisponible'}.")

    lines.append("\n### 3. Prédiction probabiliste")
    if analysis is not None:
        direction = analysis.setup["direction"]
        lines.append(
            f"- Direction dominante : **{analysis.label}** (score {analysis.score:+.1f}/100, "
            f"confiance {analysis.confidence:.0%})."
        )
        lines.append(
            f"- Traduction opérationnelle : {direction.upper()} "
            f"(entrée {analysis.setup['entry']:.6f}, stop {analysis.setup['stop']:.6f}, "
            f"objectifs {analysis.setup['target1']:.6f} puis {analysis.setup['target2']:.6f}, "
            f"R/R {analysis.setup['risk_reward']})."
        )
        snapshot = analysis.indicators
        atr_value = float(snapshot.get("atr14") or analysis.price * 0.01)
        lines.append(
            f"- Horizon court terme (~5 bougies) : amplitude attendue ≈ {atr_value * 2.2:.6f} "
            f"soit ±{atr_value * 2.2 / analysis.price * 100:.2f} % autour du prix actuel."
        )
        lines.append(
            f"- Horizon moyen terme (~20 bougies) : amplitude attendue ≈ {atr_value * 4.5:.6f} "
            f"soit ±{atr_value * 4.5 / analysis.price * 100:.2f} %."
        )
        lines.append(
            "- Rappel : ces probabilités sont conditionnelles aux niveaux actuels ; "
            "une cassure invalide le scénario."
        )
    else:
        lines.append("- Prédiction chiffrée impossible sans série de prix.")

    lines.append("\n### 4. Plan de trading")
    if analysis is not None:
        setup = analysis.setup
        lines.append(f"- Direction : {setup['direction']}")
        lines.append(
            f"- Entrée : {setup['entry']:.6f} | Stop : {setup['stop']:.6f} | "
            f"Objectif 1 : {setup['target1']:.6f} | Objectif 2 : {setup['target2']:.6f}"
        )
        lines.append(f"- Ratio risque/rendement : {setup['risk_reward']}")
        lines.append(f"- Taille de position : {setup['position_size_hint']}")
    else:
        lines.append("- À définir après obtention des données de prix.")

    lines.append("\n### 5. Scénario alternatif et invalidation")
    if analysis is not None:
        for scenario in analysis.scenarios:
            lines.append(
                f"- **{scenario['name']}** — déclencheur : {scenario['trigger']} ; "
                f"objectifs : {', '.join(f'{value:.6f}' for value in scenario['targets'])} ; "
                f"invalidation : {scenario['invalidation']}"
            )
    else:
        lines.append("- Non calculable sans données de prix.")

    lines.append("\n### 6. Limites et incertitudes")
    combined_warnings = list(warnings)
    if analysis is not None:
        combined_warnings.extend(analysis.warnings)
    if observation is not None and observation.uncertainties:
        combined_warnings.extend(f"Lecture d'image : {item}" for item in observation.uncertainties)
    if not combined_warnings:
        combined_warnings.append(
            "Analyse fondée sur des données historiques : un événement fondamental "
            "(résultats, macro, actualité) peut invalider tout scénario technique."
        )
    for warning in dict.fromkeys(combined_warnings):
        lines.append(f"- {warning}")

    lines.append("\n### AVERTISSEMENT")
    lines.append(DISCLAIMER)
    if question:
        lines.append(f"\n*Question posée : {question.strip()}*")
    return "\n".join(lines)


# --------------------------------------------------------------------- #
#  Pipeline principal
# --------------------------------------------------------------------- #

def run_analysis(
    request: AnalysisRequest, *, settings: Settings | None = None
) -> AnalysisResponse:
    """Exécute la chaîne complète : données → technique → RAG → vision → LLM → réponse."""
    settings = settings or get_settings()
    warnings: list[str] = []
    instrument = None
    result: Optional[AnalysisResult] = None
    observation: Optional[ChartObservation] = None
    llm = get_llm(settings)

    image = validate_image(request.image, settings) if request.image else None

    # 1) Données de marché -------------------------------------------- #
    symbol = (request.symbol or "").strip()
    if symbol:
        try:
            instrument = get_instrument(
                symbol, request.period, request.interval, settings=settings
            )
        except MarketDataError as exc:
            warnings.append(f"Données indisponibles pour {symbol} : {exc}")
    elif image is None:
        warnings.append(
            "Aucun symbole ni image fournis : seules les connaissances du cours "
            "peuvent être mobilisées."
        )

    # 2) Vision ------------------------------------------------------- #
    if image is not None:
        observation = read_chart(image, llm=llm, settings=settings)
        if not observation.available:
            warnings.append(observation.error)
        # Si l'IA propose un symbole et qu'aucun n'était fourni : analyse croisée
        if (
            instrument is None
            and request.analyze_detected_symbol
            and observation.available
        ):
            detected = guess_symbol(f"{observation.symbol_guess} {observation.summary}")
            if detected:
                try:
                    instrument = get_instrument(
                        detected, request.period, request.interval, settings=settings
                    )
                    warnings.append(
                        f"Symbole « {detected} » reconnu sur l'image : les données de marché "
                        "correspondantes ont été ajoutées pour croiser l'analyse."
                    )
                except MarketDataError:
                    warnings.append(
                        f"Symbole « {detected} » suggéré par l'image, mais aucune donnée "
                        "de marché n'a pu être récupérée."
                    )

    # 3) Moteur technique --------------------------------------------- #
    if instrument is not None and len(instrument.candles) >= 5:
        try:
            result = analyze(instrument)
        except Exception as exc:  # pragma: no cover
            warnings.append(f"Analyse technique impossible : {exc}")

    # 4) RAG ---------------------------------------------------------- #
    queries: list[str] = []
    if request.question:
        queries.append(request.question)
    if result is not None:
        queries.append(to_rag_query(result))
        queries.append(
            " ".join(pattern.name for pattern in result.patterns[:4])
            + " gestion du risque stop loss taille de position"
        )
    if observation is not None and observation.available:
        queries.append(observation.rag_query)
    if symbol:
        queries.append(f"méthode analyse {symbol} unité de temps {request.interval} tendance")

    if queries:
        retrieval = search_multi(queries, top_k=request.top_k, settings=settings)
    else:
        retrieval = search("*", top_k=request.top_k, settings=settings)
    sources = retrieval.sources
    course_context = build_context(retrieval.hits, settings.max_context_chars)
    if not sources:
        warnings.append(
            "Aucun document indexé : importez vos cours (PDF, Markdown, texte) dans "
            "l'onglet « Base de connaissances » pour obtenir une analyse adossée à votre méthode."
        )

    # 5) Réponse ------------------------------------------------------ #
    mode = "ia" if llm is not None else "demo"
    provider = getattr(llm, "provider", "") if llm else ""
    model = getattr(llm, "vision_model" if (image is not None) else "text_model", "") if llm else ""
    answer = ""

    if llm is not None:
        prompt = build_analysis_prompt(
            market_context=_market_context(instrument),
            technical_report=render_report(result) if result else "",
            vision_report=observation.render() if observation else "",
            course_context=course_context,
            question=request.question,
        )
        try:
            response = llm.generate(
                system=ANALYST_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                images=[image] if image else [],
                use_vision=image is not None,
            )
            answer = response.text.strip()
            model = response.model
            if not answer:
                raise LLMError("Réponse vide du modèle.")
            clear_llm_error(provider)
        except LLMError as exc:
            message = str(exc)
            warnings.append(f"Appel LLM impossible ({message}) — repli sur le moteur local.")
            record_llm_error(provider, message)
            mode = "demo"
        except Exception as exc:  # pragma: no cover
            warnings.append(f"Erreur LLM inattendue ({exc}) — repli sur le moteur local.")
            record_llm_error(provider, str(exc))
            mode = "demo"

    if not answer:
        answer = _render_demo_answer(
            analysis=result,
            observation=observation,
            sources=sources,
            question=request.question,
            warnings=warnings,
            rag_mode=retrieval.mode,
            # Si une clé existe mais que l'appel a échoué, le dire clairement
            # (ne pas afficher « sans clé LLM » alors que la clé est configurée).
            fallback_reason=("llm_error" if (llm is not None and not answer) else ""),
        )
        mode = "demo"

    # 6) Persistance -------------------------------------------------- #
    report_id = ""
    if request.save:
        report_id = get_report_store(settings).save(
            kind="image" if image is not None else "symbole",
            symbol=result.instrument.symbol if result else symbol,
            timeframe=request.interval,
            price=result.price if result else 0.0,
            label=result.label if result else "",
            score=result.score if result else 0.0,
            confidence=result.confidence if result else 0.0,
            mode=mode,
            provider=provider,
            model=model,
            question=request.question,
            answer=answer,
            analysis=result.to_dict() if result else None,
            observation=observation.to_dict() if observation else None,
            sources=sources,
            warnings=list(dict.fromkeys(warnings)),
        )

    if result is not None:
        warnings.extend(result.warnings)

    return AnalysisResponse(
        answer=answer,
        mode=mode,
        provider=provider,
        model=model,
        analysis=result.to_dict() if result else None,
        observation=observation.to_dict() if observation else None,
        sources=sources,
        rag_mode=retrieval.mode,
        warnings=list(dict.fromkeys(warnings)),
        report_id=report_id,
        request={
            "symbol": symbol,
            "period": request.period,
            "interval": request.interval,
            "question": request.question,
            "has_image": image is not None,
            "top_k": request.top_k,
        },
    )


def quick_diagnostic(settings: Settings | None = None) -> dict[str, Any]:
    """Diagnostic complet pour la page d'accueil (état des services)."""
    from ..llm import provider_status
    from ..market import market_status

    settings = settings or get_settings()
    return {
        "llm": provider_status(settings),
        "marche": market_status(settings),
        "connaissances": read_stats(settings),
    }
