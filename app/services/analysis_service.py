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
    #: Traçabilité de la capture envoyée (taille, réduction, modèle vision, erreur).
    image: dict[str, Any] = field(default_factory=dict)
    #: Requêtes réellement utilisées pour interroger la base de cours (RAG).
    rag_requetes: list[str] = field(default_factory=list)
    #: Confrontation capture ↔ données chiffrées (symbole demandé, lu sur l'image…).
    coherence: dict[str, Any] = field(default_factory=dict)

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
            "image": self.image,
            "rag_requetes": self.rag_requetes,
            "coherence": self.coherence,
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


#: Codes de devises reconnus (permettent de convertir « EUR/USD » en « EURUSD=X »).
_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNY", "HKD", "SGD",
    "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "MXN", "BRL", "ZAR", "TRY", "INR",
    "KRW", "RUB", "MAD", "TND", "AED", "SAR", "ILS", "THB", "IDR", "PHP", "MYR",
    "CLP", "ARS", "COP", "PEN", "VND", "NGN",
}

#: Cryptomonnaies courantes : « BTC/USD » devient « BTC-USD » (format Yahoo).
_CRYPTO_CODES = {
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "LTC", "DOT", "AVAX",
    "LINK", "TRX", "XLM", "ATOM", "NEAR", "SUI", "TON",
}

#: Noms de devises écrits en toutes lettres (« euro dollar » → EUR/USD).
_CURRENCY_NAMES: dict[str, str] = {
    "EURO": "EUR", "EUROS": "EUR", "DOLLAR": "USD", "DOLLARS": "USD",
    "LIVRE": "GBP", "LIVRES": "GBP", "YEN": "JPY", "FRANC": "CHF",
    "FRANCS": "CHF", "DOLLAR CANADIEN": "CAD", "DOLLAR AUSTRALIEN": "AUD",
}

#: Paire écrite « EUR/USD », « EUR-USD », « ETH/USDT », « USD/JPY »…
#: La devise de cotation peut compter jusqu'à 6 lettres (USDT, USDC, BUSD…).
_PAIR_PATTERN = re.compile(
    r"\b([A-Za-z]{3})\s*(?:[/\\]|-|–|—|\s)\s*([A-Za-z]{3,6})\b"
)
#: « EURUSD=X »
_PAIR_GLUED = re.compile(r"\b([A-Za-z]{3})([A-Za-z]{3})=X\b")
#: « BTCUSDT », « ETHUSDC » (crypto + stablecoin)
_PAIR_GLUED_STABLE = re.compile(r"\b([A-Za-z]{2,5})(USDT|USDC|BUSD|FDUSD|TUSD|DAI)\b")
#: « EURUSD », « GBPJPY »
_PAIR_GLUED2 = re.compile(r"\b([A-Za-z]{3})([A-Za-z]{3})\b")


#: Stablecoins : « BTC/USDT » et « BTC/USD » désignent le même marché pour un trader.
_STABLE_COINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "PYUSD"}


def _ticker_from_pair(base: str, quote: str) -> str:
    """Convertit une paire en symbole exploitable par les sources de marché."""
    base, quote = base.upper(), quote.upper()
    if quote in _STABLE_COINS:
        quote = "USD"
    if base in _STABLE_COINS:
        base = "USD"
    if base in _CRYPTO_CODES and quote in _CURRENCY_CODES:
        return f"{base}-{quote}"
    if base in _CURRENCY_CODES and quote in _CURRENCY_CODES:
        return f"{base}{quote}=X"
    return ""


def find_pair_symbol(text: str) -> str:
    """Détecte une paire (devises ou crypto) dans un texte libre.

    Corrige un angle mort important : « EUR/USD » (le format affiché par tous les
    plateformes de trading) n'était reconnu par aucune des règles historiques, qui
    n'acceptaient que les séparateurs « . », « - » ou « = ». Une capture de graphique
    Forex ne pouvait donc jamais être rattachée à des données de marché.
    """
    if not text:
        return ""
    upper = text.upper()
    # « EURUSD=X », « BTC-USD=X »…
    for match in _PAIR_GLUED.finditer(upper):
        ticker = _ticker_from_pair(match.group(1), match.group(2))
        if ticker:
            return ticker
    # « EUR/USD », « EUR-USD », « EUR USD », « ETH/USDT »
    for match in _PAIR_PATTERN.finditer(upper):
        ticker = _ticker_from_pair(match.group(1), match.group(2))
        if ticker:
            return ticker
    # « BTCUSDT »
    for match in _PAIR_GLUED_STABLE.finditer(upper):
        ticker = _ticker_from_pair(match.group(1), match.group(2))
        if ticker:
            return ticker
    # « EURUSD »
    for match in _PAIR_GLUED2.finditer(upper):
        ticker = _ticker_from_pair(match.group(1), match.group(2))
        if ticker:
            return ticker
    # « euro dollar », « dollar-yen »
    mots = re.findall(r"[A-ZÉÈÊÀÂÎÔÛÇ]+", upper)
    devises = [_CURRENCY_NAMES.get(mot, "") for mot in mots]
    devises = [code for code in devises if code]
    if len(devises) >= 2:
        return _ticker_from_pair(devises[0], devises[1])
    return ""


def guess_symbol(text: str) -> str:
    """Extrait un symbole exploitable d'une description d'image.

    Un symbole est écrit en majuscules dans le texte (« AAPL », « BTC-USD ») ou
    comporte un séparateur explicite ; les paires de devises/crypto sont converties
    au format des fournisseurs (« EUR/USD » → « EURUSD=X », « BTC/USD » → « BTC-USD »).
    Les mots courants en capitales sont rejetés.
    """
    if not text:
        return ""

    # 0) Paires de devises ou de cryptomonnaies (formats « EUR/USD », « EURUSD »…)
    paire = find_pair_symbol(text)
    if paire:
        return paire

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


def _model_label(llm: Optional[Any], *, use_vision: bool) -> str:
    """Modèle réellement utilisé (résolu par auto-découverte si nécessaire)."""
    if llm is None:
        return ""
    if use_vision:
        return getattr(llm, "vision_model_effective", "") or getattr(llm, "vision_model", "")
    # Les fournisseurs qui distinguent texte et vision (Gemini) exposent le modèle
    # effectivement résolu ; les autres n'ont qu'un seul modèle.
    return (
        getattr(llm, "_resolved_text_model", "")
        or getattr(llm, "text_model", "")
        or getattr(llm, "vision_model_effective", "")
        or getattr(llm, "vision_model", "")
        or getattr(llm, "model", "")
    )


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


def _coherence_context(
    coherence: dict[str, Any],
    *,
    capture_fournie: bool,
    observation: Optional[ChartObservation],
) -> str:
    """Texte injecté dans le prompt : ce que montre la capture vs ce qui est chiffré.

    Sans cette section, le modèle recevait un rapport technique « chiffres fiables »
    pour un actif et une image d'un autre actif, sans consigne de confrontation :
    il répondait donc sur l'actif des chiffres en laissant croire qu'il décrivait
    l'image.
    """
    if not capture_fournie:
        return "- Aucune capture fournie : l'analyse repose uniquement sur les données chiffrées."

    if observation is None or not observation.available:
        erreur = (observation.error if observation is not None else "") or "raison inconnue"
        return (
            "- Une capture a bien été fournie, mais sa lecture automatique a échoué "
            f"({erreur}) : ne t'appuie pas sur elle et signale-le dans la section 6."
        )

    lignes = [
        f"- Actif lu sur la capture : {coherence.get('capture_lue') or 'non lisible'} "
        f"(symbole exploitable : {coherence.get('symbole_capture') or 'aucun'})",
        f"- Unité de temps lue sur la capture : {observation.timeframe or 'non lisible'}",
        f"- Actif des données chiffrées : {coherence.get('symbole_demande') or 'aucun'}",
    ]
    if coherence.get("incoherent"):
        lignes += [
            "INCOHÉRENCE : la capture NE correspond PAS à l'actif des données chiffrées.",
            "Consignes impératives :",
            "  1. commence la section 1 en annonçant cette incohérence (actif de la capture "
            "puis actif des données chiffrées) ;",
            "  2. appuie toutes les figures et tous les niveaux de lecture sur la CAPTURE ;",
            "  3. ne présente les niveaux calculés que comme ceux de "
            f"{coherence.get('symbole_demande')}, jamais comme ceux de la capture ;",
            "  4. termine la section 1 en conseillant de relancer l'analyse avec le symbole "
            f"{coherence.get('symbole_capture') or 'lu sur la capture'} pour croiser capture "
            "et données de marché.",
        ]
    else:
        lignes.append("- Cohérence : la capture et les données chiffrées portent sur le même actif.")
    return "\n".join(lignes)


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
    coherence: dict[str, Any] = {
        "symbole_demande": instrument.symbol if instrument is not None else symbol,
        "capture_lue": "",
        "symbole_capture": "",
        "incoherent": False,
        "message": "",
    }
    if image is not None:
        # On annonce au modèle vision l'actif que l'application va analyser : il peut
        # ainsi signaler une différence au lieu de la laisser passer inaperçue.
        actif_attendu = instrument.symbol if instrument is not None else symbol
        instruction = ""
        if actif_attendu:
            instruction = (
                f"L'application analyse par ailleurs le symbole « {actif_attendu} ». "
                "Lis l'actif réellement affiché sur l'image et, s'il diffère, écris-le "
                "explicitement dans « marche_ou_symbole_estime »."
            )
        observation = read_chart(
            image, llm=llm, settings=settings, extra_instruction=instruction
        )
        if not observation.available:
            warnings.append(observation.error)

        # Confrontation capture ↔ symboles : sans ce contrôle, une capture d'un autre
        # actif était analysée en silence avec les chiffres du symbole saisi.
        if observation.available:
            capture_lue = (observation.symbol_guess or "").strip()
            detecte = guess_symbol(f"{capture_lue} {observation.summary}")
            coherence["capture_lue"] = capture_lue
            coherence["symbole_capture"] = detecte

            if detecte and instrument is None and request.analyze_detected_symbol:
                try:
                    instrument = get_instrument(
                        detecte, request.period, request.interval, settings=settings
                    )
                    coherence["symbole_demande"] = detecte
                    warnings.append(
                        f"Symbole « {detecte} » reconnu sur l'image : les données de marché "
                        "correspondantes ont été ajoutées pour croiser l'analyse."
                    )
                except MarketDataError:
                    warnings.append(
                        f"Symbole « {detecte} » suggéré par l'image, mais aucune donnée "
                        "de marché n'a pu être récupérée."
                    )
            elif instrument is not None and detecte and detecte.upper() != instrument.symbol.upper():
                coherence["incoherent"] = True
                coherence["message"] = (
                    f"La capture semble montrer « {capture_lue or detecte} » alors que les "
                    f"données chiffrées portent sur {instrument.symbol}. Relancez l'analyse "
                    f"avec le symbole {detecte} pour croiser la capture et le marché."
                )
                warnings.append(
                    "Incohérence détectée : la capture ne correspond pas au symbole analysé "
                    f"({capture_lue or detecte} sur l'image, {instrument.symbol} pour les "
                    "données chiffrées). Les figures lues sur l'image et les niveaux calculés "
                    "ne concernent donc pas le même actif."
                )
            elif instrument is not None and detecte:
                coherence["message"] = (
                    f"Capture et données chiffrées concordent : {instrument.symbol}."
                )

    # Les notes de la source (période ajustée, repli démo…) doivent être visibles.
    if instrument is not None:
        for note in instrument.notes:
            if note not in warnings:
                warnings.append(note)

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
    model = _model_label(llm, use_vision=image is not None)
    llm_erreur = False
    answer = ""

    contexte_coherence = _coherence_context(
        coherence, capture_fournie=image is not None, observation=observation
    )

    if llm is not None:
        prompt = build_analysis_prompt(
            market_context=_market_context(instrument),
            technical_report=render_report(result) if result else "",
            vision_report=observation.render() if observation else "",
            coherence_context=contexte_coherence,
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
            llm_erreur = True
        except Exception as exc:  # pragma: no cover
            warnings.append(f"Erreur LLM inattendue ({exc}) — repli sur le moteur local.")
            record_llm_error(provider, str(exc))
            mode = "demo"
            llm_erreur = True

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
            fallback_reason=("llm_error" if llm_erreur else ""),
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

    image_info = {
        "fournie": image is not None,
        "taille_ko": round(len(image) / 1024, 1) if image else 0.0,
        "taille_origine_ko": round(len(request.image) / 1024, 1) if request.image else 0.0,
        "reduite": bool(request.image and image and len(image) != len(request.image)),
        "transmise_au_modele": bool(image is not None and llm is not None),
        "lecture_reussie": bool(observation is not None and observation.available),
        "modele_vision": (observation.model if observation else "") or "",
        "erreur": (
            observation.error if observation is not None and not observation.available else ""
        ),
    }

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
        image=image_info,
        rag_requetes=queries,
        coherence=coherence,
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
