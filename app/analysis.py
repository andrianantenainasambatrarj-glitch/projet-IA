"""Moteur d'analyse technique déterministe.

Rôle : avant de solliciter le LLM, on calcule *objectivement* tout ce qui peut
l'être (tendance, supports/résistances, figures, indicateurs, scénarios,
niveaux d'entrée/stop/objectifs). Le LLM reçoit ensuite ce rapport chiffré
+ les extraits de vos cours (RAG) + l'image du graphique.

Avantages : résultats reproductibles, pas d'hallucination sur les chiffres,
et l'application reste 100 % fonctionnelle même sans clé API (mode démo).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from . import indicators as ind

# --------------------------------------------------------------------- #
#  Structures de données
# --------------------------------------------------------------------- #


@dataclass
class Candle:
    """Bougie OHLCV."""

    t: int  # timestamp epoch (secondes)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(self.t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    @property
    def day(self) -> str:
        return datetime.fromtimestamp(self.t, tz=timezone.utc).strftime("%Y-%m-%d")

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def range(self) -> float:
        return max(1e-12, self.h - self.l)

    @property
    def bullish(self) -> bool:
        return self.c >= self.o


@dataclass
class Instrument:
    """Série de prix avec ses métadonnées."""

    symbol: str
    name: str = ""
    currency: str = "USD"
    timeframe: str = "1d"
    source: str = "demo"
    candles: list[Candle] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def closes(self) -> list[float]:
        return [candle.c for candle in self.candles]

    @property
    def highs(self) -> list[float]:
        return [candle.h for candle in self.candles]

    @property
    def lows(self) -> list[float]:
        return [candle.l for candle in self.candles]

    @property
    def opens(self) -> list[float]:
        return [candle.o for candle in self.candles]

    @property
    def volumes(self) -> list[float]:
        return [candle.v for candle in self.candles]

    @property
    def last(self) -> Candle:
        return self.candles[-1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "currency": self.currency,
            "timeframe": self.timeframe,
            "source": self.source,
            "candles": len(self.candles),
            "last_date": self.candles[-1].day if self.candles else "",
            "notes": list(self.notes),
        }


@dataclass
class Level:
    """Support ou résistance."""

    price: float
    kind: str  # "support" | "resistance"
    touches: int
    distance_pct: float
    last_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": round(self.price, 6),
            "kind": self.kind,
            "touches": self.touches,
            "distance_pct": round(self.distance_pct, 3),
        }


@dataclass
class DetectedPattern:
    """Figure détectée (chandelier ou graphique)."""

    name: str
    family: str  # "chandelier" | "graphique" | "divergence" | "niveau"
    bias: str  # "haussier" | "baissier" | "neutre"
    confidence: float  # 0 → 1
    index: int
    description: str
    target: Optional[float] = None
    invalidation: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "family": self.family,
            "bias": self.bias,
            "confidence": round(self.confidence, 2),
            "description": self.description,
            "index": self.index,
        }
        if self.target is not None:
            payload["target"] = round(self.target, 6)
        if self.invalidation is not None:
            payload["invalidation"] = round(self.invalidation, 6)
        return payload


@dataclass
class AnalysisResult:
    """Rapport complet, prêt à être affiché ou envoyé au LLM."""

    instrument: Instrument
    price: float
    trend: dict[str, Any]
    indicators: dict[str, Any]
    levels: dict[str, Any]
    patterns: list[DetectedPattern]
    score: float
    label: str
    confidence: float
    setup: dict[str, Any]
    scenarios: list[dict[str, Any]]
    stats: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument.to_dict(),
            "price": round(self.price, 6),
            "trend": self.trend,
            "indicators": self.indicators,
            "levels": self.levels,
            "patterns": [pattern.to_dict() for pattern in self.patterns],
            "score": round(self.score, 1),
            "label": self.label,
            "confidence": round(self.confidence, 2),
            "setup": self.setup,
            "scenarios": self.scenarios,
            "stats": self.stats,
            "warnings": self.warnings,
            "generated_at": self.generated_at,
        }


# --------------------------------------------------------------------- #
#  Détection des pivots et des niveaux
# --------------------------------------------------------------------- #

def find_pivots(
    highs: Sequence[float], lows: Sequence[float], left: int = 2, right: int = 2
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Pivots fractals (sommets/creux locaux)."""
    pivot_highs: list[tuple[int, float]] = []
    pivot_lows: list[tuple[int, float]] = []
    for index in range(left, len(highs) - right):
        window_highs = highs[index - left : index + right + 1]
        window_lows = lows[index - left : index + right + 1]
        if highs[index] >= max(window_highs) and highs[index] > max(
            highs[index - left : index]
        ):
            pivot_highs.append((index, float(highs[index])))
        if lows[index] <= min(window_lows) and lows[index] < min(lows[index - left : index]):
            pivot_lows.append((index, float(lows[index])))
    return pivot_highs, pivot_lows


def cluster_levels(
    points: Sequence[tuple[int, float]], *, tolerance_pct: float = 0.6
) -> list[tuple[float, int, int]]:
    """Regroupe des pivots proches en un niveau. Renvoie (prix, touches, dernier_index)."""
    if not points:
        return []
    ordered = sorted(points, key=lambda item: item[1])
    clusters: list[list[tuple[int, float]]] = [[ordered[0]]]
    for index, price in ordered[1:]:
        reference = sum(p for _, p in clusters[-1]) / len(clusters[-1])
        if abs(price - reference) / max(reference, 1e-9) * 100.0 <= tolerance_pct:
            clusters[-1].append((index, price))
        else:
            clusters.append([(index, price)])
    result: list[tuple[float, int, int]] = []
    for cluster in clusters:
        average = sum(price for _, price in cluster) / len(cluster)
        last_index = max(index for index, _ in cluster)
        result.append((average, len(cluster), last_index))
    return sorted(result, key=lambda item: item[1], reverse=True)


def build_levels(instrument: Instrument, max_levels: int = 6) -> dict[str, Any]:
    """Construit les supports/résistances autour du prix courant."""
    highs, lows, closes = instrument.highs, instrument.lows, instrument.closes
    price = closes[-1]
    pivot_highs, pivot_lows = find_pivots(highs, lows)

    resistance_points = [(index, value) for index, value in pivot_highs if value > price]
    support_points = [(index, value) for index, value in pivot_lows if value < price]

    resistances = [
        Level(price=value, kind="resistance", touches=touches, distance_pct=(value - price) / price * 100.0, last_index=last)
        for value, touches, last in cluster_levels(resistance_points)
    ]
    supports = [
        Level(price=value, kind="support", touches=touches, distance_pct=(price - value) / price * 100.0, last_index=last)
        for value, touches, last in cluster_levels(support_points)
    ]

    # On garde les niveaux les plus proches, mais on privilégie ceux qui comptent
    # (plusieurs touches + récence).
    def rank(level: Level) -> tuple[float, int]:
        return (level.distance_pct, -level.touches)

    supports = sorted(supports, key=rank)[:max_levels]
    resistances = sorted(resistances, key=rank)[:max_levels]

    period_high = max(highs)
    period_low = min(lows)
    return {
        "supports": [level.to_dict() for level in supports],
        "resistances": [level.to_dict() for level in resistances],
        "nearest_support": supports[0].to_dict() if supports else None,
        "nearest_resistance": resistances[0].to_dict() if resistances else None,
        "period_high": round(period_high, 6),
        "period_low": round(period_low, 6),
        "range_position_pct": round(
            (price - period_low) / max(period_high - period_low, 1e-12) * 100.0, 2
        ),
        "psychological_levels": [
            round(value, 6) for value in _round_numbers(price, period_low, period_high)
        ],
    }


def _round_numbers(price: float, low: float, high: float) -> list[float]:
    """Niveaux psychologiques (nombres ronds) situés dans le range."""
    magnitude = 10 ** math.floor(math.log10(max(abs(price), 1e-9)))
    step = magnitude / 10 if price < magnitude * 10 else magnitude
    candidates: list[float] = []
    start = math.floor(low / step) * step
    value = start
    while value <= high and len(candidates) < 40:
        if low <= value <= high and abs(value - price) / price * 100.0 < 8:
            candidates.append(value)
        value += step
    return sorted(candidates, key=lambda item: abs(item - price))[:4]


# --------------------------------------------------------------------- #
#  Tendance
# --------------------------------------------------------------------- #

def detect_trend(instrument: Instrument) -> dict[str, Any]:
    """Analyse de tendance : moyennes, structure de marché, ADX."""
    closes, highs, lows = instrument.closes, instrument.highs, instrument.lows
    price = closes[-1]

    sma20 = ind.sma(closes, 20)
    sma50 = ind.sma(closes, 50)
    sma200 = ind.sma(closes, 200)
    ema20 = ind.ema(closes, 20)
    ema50 = ind.ema(closes, 50)
    adx_data = ind.adx(highs, lows, closes)
    adx_value = ind.last_valid(adx_data["adx"]) or 0.0
    plus_di = ind.last_valid(adx_data["plus_di"]) or 0.0
    minus_di = ind.last_valid(adx_data["minus_di"]) or 0.0

    scores: list[tuple[str, float]] = []
    details: list[str] = []

    def add(component: str, value: float, text: str) -> None:
        scores.append((component, value))
        details.append(text)

    ema50_value = ind.last_valid(ema50)
    sma200_value = ind.last_valid(sma200)
    ema20_value = ind.last_valid(ema20)

    if ema20_value and ema50_value:
        if price > ema20_value > ema50_value:
            add("moyennes", 20, "Prix > EMA20 > EMA50 : structure haussière court terme.")
        elif price < ema20_value < ema50_value:
            add("moyennes", -20, "Prix < EMA20 < EMA50 : structure baissière court terme.")
        else:
            add("moyennes", 0, "Prix imbriqué dans les moyennes : absence de direction nette.")
    if sma200_value:
        if price > sma200_value:
            add("tendance_longue", 12, "Prix au-dessus de la SMA200 (tendance de fond haussière).")
        else:
            add("tendance_longue", -12, "Prix sous la SMA200 (tendance de fond baissière).")

    slope_50 = ind.slope(sma50, 20)
    if slope_50 > 0.15:
        add("pente", 10, f"Pente de la SMA50 positive ({slope_50:.2f} %/20 périodes).")
    elif slope_50 < -0.15:
        add("pente", -10, f"Pente de la SMA50 négative ({slope_50:.2f} %/20 périodes).")
    else:
        add("pente", 0, f"Pente de la SMA50 quasi plate ({slope_50:.2f} %).")

    # --- structure de marché (sommets/creux) ------------------------- #
    pivot_highs, pivot_lows = find_pivots(highs, lows)
    structure = "indéterminée"
    structure_score = 0.0
    recent_highs = [value for _, value in pivot_highs[-3:]]
    recent_lows = [value for _, value in pivot_lows[-3:]]
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        higher_highs = recent_highs[-1] > recent_highs[0]
        higher_lows = recent_lows[-1] > recent_lows[0]
        lower_highs = recent_highs[-1] < recent_highs[0]
        lower_lows = recent_lows[-1] < recent_lows[0]
        if higher_highs and higher_lows:
            structure, structure_score = "haussière (sommets et creux ascendants)", 15.0
        elif lower_highs and lower_lows:
            structure, structure_score = "baissière (sommets et creux descendants)", -15.0
        elif higher_lows and lower_highs:
            structure, structure_score = "compression (triangle / range)", 0.0
        else:
            structure, structure_score = "mitigée", 0.0
    add("structure", structure_score, f"Structure de marché : {structure}.")

    if adx_value >= 25:
        direction = 12 if plus_di > minus_di else -12
        add(
            "adx",
            direction,
            f"ADX {adx_value:.1f} : tendance {'haussière' if plus_di > minus_di else 'baissière'} "
            f"installée (+DI {plus_di:.0f} / -DI {minus_di:.0f}).",
        )
    elif adx_value >= 20:
        direction = 6 if plus_di > minus_di else -6
        add("adx", direction, f"ADX {adx_value:.1f} : tendance en formation.")
    else:
        add("adx", 0, f"ADX {adx_value:.1f} : marché sans tendance (range probable).")

    total = sum(value for _, value in scores)
    if total >= 25:
        label = "haussière"
    elif total >= 10:
        label = "haussier modéré"
    elif total <= -25:
        label = "baissière"
    elif total <= -10:
        label = "baissier modéré"
    else:
        label = "neutre / range"

    return {
        "label": label,
        "score": round(max(-50.0, min(50.0, total)), 1),
        "structure": structure,
        "slope_ma50": round(slope_50, 3),
        "adx": round(adx_value, 2),
        "plus_di": round(plus_di, 2),
        "minus_di": round(minus_di, 2),
        "ma": {
            "ema20": _round(ind.last_valid(ema20)),
            "ema50": _round(ind.last_valid(ema50)),
            "sma50": _round(ind.last_valid(sma50)),
            "sma200": _round(ind.last_valid(sma200)),
            "sma20": _round(ind.last_valid(sma20)),
        },
        "details": details,
    }


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    return None if value is None else round(float(value), digits)


# --------------------------------------------------------------------- #
#  Indicateurs (instantané)
# --------------------------------------------------------------------- #

def indicator_snapshot(instrument: Instrument) -> dict[str, Any]:
    """Valeurs actuelles des indicateurs + lecture rapide."""
    closes, highs, lows, volumes = (
        instrument.closes,
        instrument.highs,
        instrument.lows,
        instrument.volumes,
    )
    price = closes[-1]

    rsi_values = ind.rsi(closes, 14)
    rsi_value = ind.last_valid(rsi_values) or 50.0
    macd_data = ind.macd(closes)
    macd_value = ind.last_valid(macd_data["macd"]) or 0.0
    signal_value = ind.last_valid(macd_data["signal"]) or 0.0
    histogram = ind.last_valid(macd_data["histogram"]) or 0.0
    atr_values = ind.atr(highs, lows, closes, 14)
    atr_value = ind.last_valid(atr_values) or (price * 0.01)
    boll = ind.bollinger(closes, 20, 2.0)
    upper = ind.last_valid(boll["upper"])
    lower = ind.last_valid(boll["lower"])
    middle = ind.last_valid(boll["middle"])
    stoch = ind.stochastic(highs, lows, closes)
    stoch_k = ind.last_valid(stoch["k"]) or 50.0
    stoch_d = ind.last_valid(stoch["d"]) or 50.0
    williams = ind.last_valid(ind.williams_r(highs, lows, closes)) or -50.0
    obv_values = ind.obv(closes, volumes)
    obv_slope = ind.slope(obv_values, 20) if any(obv_values) else 0.0
    volume_sma = ind.sma(volumes, 20)
    average_volume = ind.last_valid(volume_sma) or (sum(volumes[-20:]) / max(1, len(volumes[-20:])))
    last_volume = volumes[-1] if volumes else 0.0
    volume_ratio = (last_volume / average_volume) if average_volume > 0 else 1.0

    readings: list[str] = []
    if rsi_value >= 70:
        readings.append(f"RSI {rsi_value:.1f} : surachat (risque de repli ou de consolidation).")
    elif rsi_value <= 30:
        readings.append(f"RSI {rsi_value:.1f} : survente (risque de rebond technique).")
    else:
        readings.append(f"RSI {rsi_value:.1f} : zone neutre, marge de manœuvre des deux côtés.")
    if histogram > 0:
        readings.append(
            f"MACD au-dessus de son signal (histogramme {histogram:+.4f}) : momentum haussier."
        )
    else:
        readings.append(
            f"MACD sous son signal (histogramme {histogram:+.4f}) : momentum baissier."
        )
    if upper and lower and middle:
        if price >= upper:
            readings.append("Clôture au-dessus de la bande de Bollinger supérieure : extension forte.")
        elif price <= lower:
            readings.append("Clôture sous la bande de Bollinger inférieure : extension baissière.")
    readings.append(
        f"ATR(14) = {atr_value:.4f} ({atr_value / price * 100:.2f} % du prix) : "
        "volatilité utilisée pour le stop et les objectifs."
    )
    readings.append(
        f"Volume du jour : {volume_ratio:.2f}× la moyenne 20 périodes"
        + (" (participation forte)." if volume_ratio > 1.4 else " (participation normale ou faible).")
    )
    if stoch_k >= 80:
        readings.append(f"Stochastique %K {stoch_k:.1f} : haut de canal.")
    elif stoch_k <= 20:
        readings.append(f"Stochastique %K {stoch_k:.1f} : bas de canal.")

    return {
        "rsi14": round(rsi_value, 2),
        "macd": round(macd_value, 6),
        "macd_signal": round(signal_value, 6),
        "macd_histogram": round(histogram, 6),
        "macd_cross": ind.cross_signal(macd_data["macd"], macd_data["signal"], 6),
        "atr14": round(atr_value, 6),
        "atr_pct": round(atr_value / price * 100.0, 3),
        "bollinger_upper": _round(upper),
        "bollinger_middle": _round(middle),
        "bollinger_lower": _round(lower),
        "bollinger_width_pct": _round(ind.last_valid(boll["width"]), 3),
        "stochastic_k": round(stoch_k, 2),
        "stochastic_d": round(stoch_d, 2),
        "williams_r": round(williams, 2),
        "obv_slope": round(obv_slope, 3),
        "volume_ratio": round(volume_ratio, 3),
        "average_volume": round(average_volume, 2),
        "readings": readings,
    }


# --------------------------------------------------------------------- #
#  Figures chartistes et chandeliers
# --------------------------------------------------------------------- #

def detect_candlestick_patterns(instrument: Instrument, lookback: int = 5) -> list[DetectedPattern]:
    """Figures de chandeliers japonais sur les dernières bougies."""
    patterns: list[DetectedPattern] = []
    candles = instrument.candles
    size = len(candles)
    if size < 4:
        return patterns
    atr_value = ind.last_valid(ind.atr(instrument.highs, instrument.lows, instrument.closes, 14)) or (
        candles[-1].c * 0.01
    )

    for offset in range(min(lookback, size - 2)):
        index = size - 1 - offset
        candle = candles[index]
        previous = candles[index - 1]
        body = candle.body
        upper_wick = candle.h - max(candle.o, candle.c)
        lower_wick = min(candle.o, candle.c) - candle.l
        decile = max(body, candle.range * 0.1, atr_value * 0.05)

        if body <= candle.range * 0.12:
            patterns.append(
                DetectedPattern(
                    name="Doji",
                    family="chandelier",
                    bias="neutre",
                    confidence=0.45,
                    index=index,
                    description=(
                        f"Doji le {candle.day} : indécision marquée "
                        f"(corps {body:.4f} pour un range de {candle.range:.4f})."
                    ),
                )
            )
        if lower_wick > body * 2 and upper_wick < body * 0.8 and body > 0:
            bias = "haussier" if candle.c > candle.o or offset == 0 else "neutre"
            patterns.append(
                DetectedPattern(
                    name="Marteau (hammer)",
                    family="chandelier",
                    bias=bias,
                    confidence=0.6,
                    index=index,
                    description=(
                        f"Marteau le {candle.day} : longue mèche basse, rejet des vendeurs "
                        f"autour de {candle.l:.4f}."
                    ),
                    invalidation=candle.l,
                )
            )
        if upper_wick > body * 2 and lower_wick < body * 0.8 and body > 0:
            patterns.append(
                DetectedPattern(
                    name="Étoile filante (shooting star)",
                    family="chandelier",
                    bias="baissier" if offset == 0 else "neutre",
                    confidence=0.6,
                    index=index,
                    description=(
                        f"Étoile filante le {candle.day} : mèche haute marquée "
                        f"jusqu'à {candle.h:.4f}, pression vendeuse."
                    ),
                    invalidation=candle.h,
                )
            )
        engulfing_up = (
            candle.bullish
            and not previous.bullish
            and candle.c > previous.o
            and candle.o <= previous.c
            and body > previous.body
        )
        engulfing_down = (
            not candle.bullish
            and previous.bullish
            and candle.c < previous.o
            and candle.o >= previous.c
            and body > previous.body
        )
        if engulfing_up:
            patterns.append(
                DetectedPattern(
                    name="Avalement haussier (bullish engulfing)",
                    family="chandelier",
                    bias="haussier",
                    confidence=0.7,
                    index=index,
                    description=(
                        f"Avalement haussier le {candle.day} : la bougie englobe la précédente "
                        f"et clôture à {candle.c:.4f}."
                    ),
                    target=candle.c + atr_value * 2,
                    invalidation=min(candle.l, previous.l),
                )
            )
        if engulfing_down:
            patterns.append(
                DetectedPattern(
                    name="Avalement baissier (bearish engulfing)",
                    family="chandelier",
                    bias="baissier",
                    confidence=0.7,
                    index=index,
                    description=(
                        f"Avalement baissier le {candle.day} : pression vendeuse qui efface "
                        f"la hausse de la veille, clôture {candle.c:.4f}."
                    ),
                    target=candle.c - atr_value * 2,
                    invalidation=max(candle.h, previous.h),
                )
            )
        if body < decile and previous.body > 0 and candle.h < previous.h and candle.l > previous.l:
            patterns.append(
                DetectedPattern(
                    name="Bougie intérieure (inside bar)",
                    family="chandelier",
                    bias="neutre",
                    confidence=0.5,
                    index=index,
                    description=(
                        f"Inside bar le {candle.day} : compression avant expansion "
                        "(surveiller la cassure du range)."
                    ),
                )
            )
        # Étoile du matin / du soir (3 bougies)
        if index >= 2:
            third = candles[index - 2]
            mid = candles[index - 1]
            if (
                not third.bullish
                and mid.body < third.body * 0.5
                and candle.bullish
                and candle.c > (third.o + third.c) / 2
            ):
                patterns.append(
                    DetectedPattern(
                        name="Étoile du matin (morning star)",
                        family="chandelier",
                        bias="haussier",
                        confidence=0.75,
                        index=index,
                        description=(
                            f"Étoile du matin sur {third.day} → {candle.day} : "
                            "retournement haussier en trois temps."
                        ),
                        target=candle.c + atr_value * 2,
                        invalidation=min(mid.l, candle.l),
                    )
                )
            if (
                third.bullish
                and mid.body < third.body * 0.5
                and not candle.bullish
                and candle.c < (third.o + third.c) / 2
            ):
                patterns.append(
                    DetectedPattern(
                        name="Étoile du soir (evening star)",
                        family="chandelier",
                        bias="baissier",
                        confidence=0.75,
                        index=index,
                        description=(
                            f"Étoile du soir sur {third.day} → {candle.day} : "
                            "retournement baissier en trois temps."
                        ),
                        target=candle.c - atr_value * 2,
                        invalidation=max(mid.h, candle.h),
                    )
                )
    return patterns


def detect_chart_patterns(instrument: Instrument) -> list[DetectedPattern]:
    """Figures graphiques : doubles sommets/creux, épaule-tête-épaule, triangles, canaux."""
    patterns: list[DetectedPattern] = []
    highs, lows, closes = instrument.highs, instrument.lows, instrument.closes
    price = closes[-1]
    atr_value = ind.last_valid(ind.atr(highs, lows, closes, 14)) or price * 0.01
    pivot_highs, pivot_lows = find_pivots(highs, lows)

    # --- double sommet / double creux ------------------------------- #
    for first, second in zip(pivot_highs[-4:], pivot_highs[-3:]):
        if first[0] == second[0]:
            continue
        gap = abs(second[1] - first[1]) / max(first[1], 1e-9) * 100.0
        if gap <= 1.5 and second[0] - first[0] >= 8:
            neckline = min(lows[first[0] : second[0] + 1])
            height = first[1] - neckline
            target = neckline - height
            patterns.append(
                DetectedPattern(
                    name="Double sommet (M)",
                    family="graphique",
                    bias="baissier",
                    confidence=0.65 if price < neckline * 1.01 else 0.55,
                    index=second[0],
                    description=(
                        f"Deux sommets proches ({first[1]:.4f} et {second[1]:.4f}) "
                        f"avec col (neckline) à {neckline:.4f}. "
                        + (
                            "Cassure du col confirmée."
                            if price < neckline
                            else "Cassure du col non encore validée."
                        )
                    ),
                    target=target,
                    invalidation=max(first[1], second[1]),
                )
            )
    for first, second in zip(pivot_lows[-4:], pivot_lows[-3:]):
        if first[0] == second[0]:
            continue
        gap = abs(second[1] - first[1]) / max(first[1], 1e-9) * 100.0
        if gap <= 1.5 and second[0] - first[0] >= 8:
            neckline = max(highs[first[0] : second[0] + 1])
            height = neckline - first[1]
            target = neckline + height
            patterns.append(
                DetectedPattern(
                    name="Double creux (W)",
                    family="graphique",
                    bias="haussier",
                    confidence=0.65 if price > neckline * 0.99 else 0.55,
                    index=second[0],
                    description=(
                        f"Deux creux proches ({first[1]:.4f} et {second[1]:.4f}) "
                        f"avec col à {neckline:.4f}. "
                        + (
                            "Cassure haussière confirmée."
                            if price > neckline
                            else "Cassure haussière non encore validée."
                        )
                    ),
                    target=target,
                    invalidation=min(first[1], second[1]),
                )
            )

    # --- épaule-tête-épaule ------------------------------------------ #
    if len(pivot_highs) >= 3:
        left, head, right = pivot_highs[-3], pivot_highs[-2], pivot_highs[-1]
        if head[1] > left[1] and head[1] > right[1]:
            symmetry = abs(left[1] - right[1]) / max(head[1], 1e-9) * 100.0
            if symmetry <= 3.0 and head[1] - max(left[1], right[1]) > atr_value * 0.6:
                neckline = min(
                    lows[min(left[0], right[0]) : max(left[0], right[0]) + 1]
                )
                patterns.append(
                    DetectedPattern(
                        name="Épaule-tête-épaule (ETE)",
                        family="graphique",
                        bias="baissier",
                        confidence=0.7,
                        index=head[0],
                        description=(
                            f"Épaule-tête-épaule : gauche {left[1]:.4f}, tête {head[1]:.4f}, "
                            f"droite {right[1]:.4f}, col à {neckline:.4f}. "
                            "Figure de retournement baissier, valide sous la tête."
                        ),
                        target=neckline - (head[1] - neckline),
                        invalidation=head[1],
                    )
                )
    if len(pivot_lows) >= 3:
        left, head, right = pivot_lows[-3], pivot_lows[-2], pivot_lows[-1]
        if head[1] < left[1] and head[1] < right[1]:
            symmetry = abs(left[1] - right[1]) / max(head[1], 1e-9) * 100.0
            if symmetry <= 3.0 and min(left[1], right[1]) - head[1] > atr_value * 0.6:
                neckline = max(highs[min(left[0], right[0]) : max(left[0], right[0]) + 1])
                patterns.append(
                    DetectedPattern(
                        name="Épaule-tête-épaule inversée (ETEi)",
                        family="graphique",
                        bias="haussier",
                        confidence=0.7,
                        index=head[0],
                        description=(
                            f"ETE inversée : creux {left[1]:.4f} / {head[1]:.4f} / {right[1]:.4f}, "
                            f"col à {neckline:.4f}. Retournement haussier."
                        ),
                        target=neckline + (neckline - head[1]),
                        invalidation=head[1],
                    )
                )

    # --- triangles / canaux (régressions sur pivots) ----------------- #
    if len(pivot_highs) >= 3 and len(pivot_lows) >= 3:
        high_slope = _pivot_slope(pivot_highs[-4:], price)
        low_slope = _pivot_slope(pivot_lows[-4:], price)
        span = max(1, pivot_highs[-1][0] - pivot_lows[-1][0])
        if high_slope < -0.02 and low_slope > 0.02:
            patterns.append(
                DetectedPattern(
                    name="Triangle symétrique",
                    family="graphique",
                    bias="neutre",
                    confidence=0.55,
                    index=len(closes) - 1,
                    description=(
                        f"Sommets descendants ({high_slope:+.2f} %/bougie) et creux ascendants "
                        f"({low_slope:+.2f} %/bougie) : compression avant cassure. "
                        "Stratégie : attendre la sortie du triangle avec volume."
                    ),
                )
            )
        elif abs(high_slope) < 0.03 and low_slope > 0.05:
            patterns.append(
                DetectedPattern(
                    name="Triangle ascendant",
                    family="graphique",
                    bias="haussier",
                    confidence=0.6,
                    index=len(closes) - 1,
                    description=(
                        "Résistance horizontale testée à répétition avec des creux ascendants : "
                        "figure de continuation haussière."
                    ),
                    target=price + atr_value * 3,
                )
            )
        elif abs(low_slope) < 0.03 and high_slope < -0.05:
            patterns.append(
                DetectedPattern(
                    name="Triangle descendant",
                    family="graphique",
                    bias="baissier",
                    confidence=0.6,
                    index=len(closes) - 1,
                    description=(
                        "Support horizontal testé à répétition avec des sommets descendants : "
                        "figure de continuation baissière."
                    ),
                    target=price - atr_value * 3,
                )
            )
        elif high_slope > 0.05 and low_slope > 0.05:
            patterns.append(
                DetectedPattern(
                    name="Canal ascendant",
                    family="graphique",
                    bias="haussier",
                    confidence=0.5,
                    index=len(closes) - 1,
                    description="Canal orienté à la hausse : acheter les replis sur le bas du canal.",
                )
            )
        elif high_slope < -0.05 and low_slope < -0.05:
            patterns.append(
                DetectedPattern(
                    name="Canal descendant",
                    family="graphique",
                    bias="baissier",
                    confidence=0.5,
                    index=len(closes) - 1,
                    description="Canal orienté à la baisse : vendre les rebonds sur le haut du canal.",
                )
            )
        if span > 0 and abs(high_slope) < 0.02 and abs(low_slope) < 0.02:
            patterns.append(
                DetectedPattern(
                    name="Range horizontal",
                    family="graphique",
                    bias="neutre",
                    confidence=0.5,
                    index=len(closes) - 1,
                    description="Marché en range : jouer les bornes plutôt que le milieu du range.",
                )
            )
    return patterns


def _pivot_slope(pivots: Sequence[tuple[int, float]], reference: float) -> float:
    """Pente en % par bougie d'une série de pivots."""
    if len(pivots) < 2:
        return 0.0
    (x1, y1), (x2, y2) = pivots[0], pivots[-1]
    if x2 == x1:
        return 0.0
    return (y2 - y1) / max(abs(reference), 1e-9) * 100.0 / (x2 - x1)


def detect_divergences(instrument: Instrument) -> list[DetectedPattern]:
    """Divergences prix / RSI (signaux d'essoufflement)."""
    patterns: list[DetectedPattern] = []
    closes, highs, lows = instrument.closes, instrument.highs, instrument.lows
    rsi_values = ind.rsi(closes, 14)
    pivot_highs, pivot_lows = find_pivots(highs, lows)

    def rsi_at(index: int) -> float:
        value = rsi_values[index] if index < len(rsi_values) else None
        return float(value) if value is not None else 50.0

    if len(pivot_highs) >= 2:
        (first_index, first_price), (second_index, second_price) = pivot_highs[-2], pivot_highs[-1]
        if (
            second_price > first_price
            and rsi_at(second_index) < rsi_at(first_index) - 3
            and second_index - first_index >= 4
        ):
            patterns.append(
                DetectedPattern(
                    name="Divergence baissière (prix/RSI)",
                    family="divergence",
                    bias="baissier",
                    confidence=0.65,
                    index=second_index,
                    description=(
                        f"Nouveau sommet de prix ({second_price:.4f} > {first_price:.4f}) alors que "
                        f"le RSI baisse ({rsi_at(second_index):.1f} < {rsi_at(first_index):.1f}) : "
                        "essoufflement de la hausse."
                    ),
                    invalidation=second_price,
                )
            )
    if len(pivot_lows) >= 2:
        (first_index, first_price), (second_index, second_price) = pivot_lows[-2], pivot_lows[-1]
        if (
            second_price < first_price
            and rsi_at(second_index) > rsi_at(first_index) + 3
            and second_index - first_index >= 4
        ):
            patterns.append(
                DetectedPattern(
                    name="Divergence haussière (prix/RSI)",
                    family="divergence",
                    bias="haussier",
                    confidence=0.65,
                    index=second_index,
                    description=(
                        f"Nouveau creux de prix ({second_price:.4f} < {first_price:.4f}) alors que "
                        f"le RSI remonte ({rsi_at(second_index):.1f} > {rsi_at(first_index):.1f}) : "
                        "épuisement de la baisse."
                    ),
                    invalidation=second_price,
                )
            )
    return patterns


def detect_breakouts(instrument: Instrument, levels: dict[str, Any]) -> list[DetectedPattern]:
    """Cassures de niveaux avec confirmation de volume."""
    patterns: list[DetectedPattern] = []
    candles = instrument.candles
    if len(candles) < 25:
        return patterns
    price = candles[-1].c
    volumes = instrument.volumes
    average_volume = sum(volumes[-21:-1]) / max(1, len(volumes[-21:-1]))
    volume_ratio = (volumes[-1] / average_volume) if average_volume > 0 else 1.0
    previous_close = candles[-2].c

    for level in levels.get("resistances", [])[:3]:
        value = float(level["price"])
        if previous_close <= value < price:
            patterns.append(
                DetectedPattern(
                    name="Cassure de résistance",
                    family="niveau",
                    bias="haussier",
                    confidence=0.6 if volume_ratio > 1.2 else 0.5,
                    index=len(candles) - 1,
                    description=(
                        f"Clôture {price:.4f} au-dessus de la résistance {value:.4f} "
                        f"({level['touches']} touches, volume {volume_ratio:.2f}× la moyenne)."
                    ),
                    invalidation=value,
                )
            )
    for level in levels.get("supports", [])[:3]:
        value = float(level["price"])
        if previous_close >= value > price:
            patterns.append(
                DetectedPattern(
                    name="Cassure de support",
                    family="niveau",
                    bias="baissier",
                    confidence=0.6 if volume_ratio > 1.2 else 0.5,
                    index=len(candles) - 1,
                    description=(
                        f"Clôture {price:.4f} sous le support {value:.4f} "
                        f"({level['touches']} touches, volume {volume_ratio:.2f}× la moyenne)."
                    ),
                    invalidation=value,
                )
            )
    return patterns


# --------------------------------------------------------------------- #
#  Score global, scénarios, plan de trading
# --------------------------------------------------------------------- #

def rsi_contribution(rsi_value: float) -> tuple[float, str]:
    """Contribution du RSI au score directionnel + explication lisible.

    Chaque zone a son propre traitement. La zone neutre (45 → 55) ne doit être ni
    pénalisée ni confondue avec la survente : auparavant un RSI à 50 était compté
    comme « survente » et retirait 3 points au score.
    """
    if rsi_value >= 70:
        return 3.0, f"RSI {rsi_value:.0f} en surachat (prudence sur les achats tardifs)."
    if rsi_value >= 55:
        return 8.0, f"RSI {rsi_value:.0f} orienté à la hausse."
    if rsi_value <= 30:
        return -3.0, f"RSI {rsi_value:.0f} en survente (prudence sur les ventes tardives)."
    if rsi_value <= 45:
        return -8.0, f"RSI {rsi_value:.0f} orienté à la baisse."
    return 0.0, f"RSI {rsi_value:.0f} neutre (pas de signal de momentum)."


def compute_score(
    trend: dict[str, Any],
    snapshot: dict[str, Any],
    patterns: Sequence[DetectedPattern],
    levels: dict[str, Any],
    price: float,
) -> tuple[float, float, list[str]]:
    """Score directionnel -100 (baissier) → +100 (haussier) + confiance."""
    score = float(trend.get("score", 0.0))
    notes: list[str] = []

    rsi_value = float(snapshot.get("rsi14", 50.0))
    contribution_rsi, note_rsi = rsi_contribution(rsi_value)
    score += contribution_rsi
    notes.append(note_rsi)

    histogram = float(snapshot.get("macd_histogram", 0.0))
    # Un histogramme exactement nul ne doit pas être compté comme baissier.
    if histogram > 0:
        score += 8
        notes.append("MACD haussier.")
    elif histogram < 0:
        score -= 8
        notes.append("MACD baissier.")
    else:
        notes.append("MACD neutre (histogramme nul).")

    volume_ratio = float(snapshot.get("volume_ratio", 1.0))
    if volume_ratio > 1.4:
        score += 4 if price > float(snapshot.get("bollinger_middle") or price) else -4
        notes.append(f"Volume élevé ({volume_ratio:.2f}×) : les mouvements sont soutenus.")

    levels_score = 0.0
    if levels.get("range_position_pct") is not None:
        position = float(levels["range_position_pct"])
        if position > 85:
            levels_score = -6
            notes.append(f"Prix à {position:.0f} % du haut de range : peu de marge avant résistance.")
        elif position < 15:
            levels_score = 6
            notes.append(f"Prix à {position:.0f} % du range : proche d'un support majeur.")
    score += levels_score

    weights = {"chandelier": 6.0, "graphique": 12.0, "divergence": 10.0, "niveau": 10.0}
    for pattern in patterns:
        if pattern.bias == "haussier":
            score += weights.get(pattern.family, 6.0) * pattern.confidence
        elif pattern.bias == "baissier":
            score -= weights.get(pattern.family, 6.0) * pattern.confidence

    score = max(-100.0, min(100.0, score))

    # Confiance : accord entre les composantes + qualité des données.
    directional = [
        1 if trend.get("score", 0) > 0 else -1,
        1 if histogram > 0 else -1,
        1 if rsi_value >= 50 else -1,
    ]
    agreement = abs(sum(directional)) / len(directional)
    pattern_agreement = 0.0
    if patterns:
        bullish = sum(1 for p in patterns if p.bias == "haussier")
        bearish = sum(1 for p in patterns if p.bias == "baissier")
        total = max(1, bullish + bearish)
        pattern_agreement = abs(bullish - bearish) / total
    confidence = 0.35 + 0.3 * agreement + 0.2 * pattern_agreement + 0.15 * min(1.0, abs(score) / 70)
    return score, max(0.1, min(0.95, confidence)), notes


def label_for_score(score: float) -> str:
    if score >= 55:
        return "haussier fort"
    if score >= 22:
        return "haussier"
    if score >= 8:
        return "légèrement haussier"
    if score <= -55:
        return "baissier fort"
    if score <= -22:
        return "baissier"
    if score <= -8:
        return "légèrement baissier"
    return "neutre (range)"


def build_setup(
    instrument: Instrument,
    score: float,
    label: str,
    levels: dict[str, Any],
    snapshot: dict[str, Any],
    patterns: Sequence[DetectedPattern],
) -> dict[str, Any]:
    """Plan de trading : entrée, stop, objectifs, ratio risque/rendement."""
    price = instrument.closes[-1]
    atr_value = float(snapshot.get("atr14") or price * 0.01)
    supports = [float(level["price"]) for level in levels.get("supports", [])]
    resistances = [float(level["price"]) for level in levels.get("resistances", [])]
    nearest_support = supports[0] if supports else price - atr_value * 2
    nearest_resistance = resistances[0] if resistances else price + atr_value * 2

    if score >= 8:
        direction = "achat"
        entry = price
        stop = max(nearest_support - atr_value * 0.3, price - atr_value * 2.2)
        pattern_targets = [p.target for p in patterns if p.bias == "haussier" and p.target]
        target1 = min(
            [candidate for candidate in [nearest_resistance, *pattern_targets] if candidate and candidate > entry]
            or [entry + atr_value * 2]
        )
        target2 = max(
            [candidate for candidate in [*pattern_targets, entry + atr_value * 3.5] if candidate and candidate > target1]
            or [target1 + atr_value]
        )
    elif score <= -8:
        direction = "vente"
        entry = price
        stop = min(nearest_resistance + atr_value * 0.3, price + atr_value * 2.2)
        pattern_targets = [p.target for p in patterns if p.bias == "baissier" and p.target]
        target1 = max(
            [candidate for candidate in [nearest_support, *pattern_targets] if candidate and candidate < entry]
            or [entry - atr_value * 2]
        )
        target2 = min(
            [candidate for candidate in [*pattern_targets, entry - atr_value * 3.5] if candidate and candidate < target1]
            or [target1 - atr_value]
        )
    else:
        direction = "attendre"
        entry = price
        stop = price - atr_value * 2
        target1 = nearest_resistance
        target2 = nearest_resistance + atr_value

    risk = abs(entry - stop)
    reward = abs(target1 - entry)
    ratio = (reward / risk) if risk > 1e-12 else 0.0

    return {
        "direction": direction,
        "entry": round(entry, 6),
        "stop": round(stop, 6),
        "target1": round(target1, 6),
        "target2": round(target2, 6),
        "risk_reward": round(ratio, 2),
        "risk_amount": round(risk, 6),
        "reward_amount": round(reward, 6),
        "position_size_hint": (
            "Risquez 1 % du capital maximum : taille = capital × 1 % ÷ écart entrée/stop."
        ),
        "note": (
            "Niveau de prix calculé sur la volatilité (ATR) et les niveaux clés ; "
            "à adapter à votre gestion du risque."
        ),
    }


def build_scenarios(
    instrument: Instrument,
    levels: dict[str, Any],
    snapshot: dict[str, Any],
    score: float = 0.0,
) -> list[dict[str, Any]]:
    """Scénarios haussier / baissier avec déclencheurs, invalidation et probabilités.

    Les deux probabilités sont **dérivées du score directionnel** et complémentaires
    (elles totalisent 100 %) : auparavant elles étaient calculées séparément à partir
    de l'éloignement aux niveaux, ce qui pouvait donner 65 % + 65 % = 130 %.
    """
    price = instrument.closes[-1]
    atr_value = float(snapshot.get("atr14") or price * 0.01)
    supports = levels.get("supports", [])
    resistances = levels.get("resistances", [])
    nearest_support = float(supports[0]["price"]) if supports else price - atr_value * 2
    nearest_resistance = float(resistances[0]["price"]) if resistances else price + atr_value * 2

    # 50 % au neutre, jusqu'à 85 %/15 % pour un score extrême ; jamais 100 %
    # (une probabilité de certitude n'existe pas en trading).
    probabilite_haussiere = max(15.0, min(85.0, 50.0 + float(score) / 4.0))
    probabilite_baissiere = 100.0 - probabilite_haussiere

    return [
        {
            "name": "Scénario haussier",
            "trigger": f"Clôture au-dessus de {nearest_resistance:.4f} avec volume supérieur à la moyenne.",
            "targets": [round(nearest_resistance + atr_value * 1.5, 6), round(nearest_resistance + atr_value * 3, 6)],
            "invalidation": f"Retour sous {nearest_support:.4f}.",
            "probability_hint": round(probabilite_haussiere, 1),
        },
        {
            "name": "Scénario baissier",
            "trigger": f"Clôture sous {nearest_support:.4f} avec accélération baissière.",
            "targets": [round(nearest_support - atr_value * 1.5, 6), round(nearest_support - atr_value * 3, 6)],
            "invalidation": f"Reprise au-dessus de {nearest_resistance:.4f}.",
            "probability_hint": round(probabilite_baissiere, 1),
        },
    ]


def compute_stats(instrument: Instrument) -> dict[str, Any]:
    """Statistiques descriptives et performance récente."""
    closes = instrument.closes
    volumes = instrument.volumes
    price = closes[-1]

    def variation(bars: int) -> Optional[float]:
        if len(closes) <= bars:
            return None
        reference = closes[-bars - 1]
        return round((price - reference) / reference * 100.0, 2)

    returns = [
        (closes[index] - closes[index - 1]) / closes[index - 1]
        for index in range(1, len(closes))
        if closes[index - 1]
    ]
    volatility = 0.0
    if len(returns) > 2:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        volatility = round(math.sqrt(variance) * math.sqrt(252) * 100.0, 2)

    period = f"{instrument.candles[0].day} → {instrument.candles[-1].day}"
    return {
        "period": period,
        "candles": len(instrument.candles),
        "variation_5": variation(5),
        "variation_20": variation(20),
        "variation_60": variation(60),
        "variation_120": variation(120),
        "annualized_volatility_pct": volatility,
        "average_volume_20": round(
            sum(volumes[-20:]) / max(1, len(volumes[-20:])), 2
        ),
        "period_high": round(max(instrument.highs), 6),
        "period_low": round(min(instrument.lows), 6),
        "consecutive_up_days": _streak(closes, up=True),
        "consecutive_down_days": _streak(closes, up=False),
    }


def _streak(closes: Sequence[float], *, up: bool) -> int:
    count = 0
    for index in range(len(closes) - 1, 0, -1):
        change = closes[index] - closes[index - 1]
        if (up and change > 0) or (not up and change < 0):
            count += 1
        else:
            break
    return count


# --------------------------------------------------------------------- #
#  Point d'entrée public
# --------------------------------------------------------------------- #

def analyze(instrument: Instrument) -> AnalysisResult:
    """Analyse technique complète d'une série de prix."""
    warnings: list[str] = list(instrument.notes)
    if len(instrument.candles) < 30:
        warnings.append(
            f"Seulement {len(instrument.candles)} bougies : les indicateurs longs "
            "(SMA200, structure) sont peu fiables."
        )
    if instrument.source == "demo":
        warnings.append(
            "Données de démonstration générées hors-ligne (les données de marché "
            "n'étaient pas accessibles) : les niveaux sont illustratifs."
        )

    price = instrument.closes[-1]
    trend = detect_trend(instrument)
    snapshot = indicator_snapshot(instrument)
    levels = build_levels(instrument)
    patterns = (
        detect_candlestick_patterns(instrument)
        + detect_chart_patterns(instrument)
        + detect_divergences(instrument)
        + detect_breakouts(instrument, levels)
    )
    patterns = sorted(patterns, key=lambda p: p.confidence, reverse=True)
    score, confidence, score_notes = compute_score(trend, snapshot, patterns, levels, price)
    label = label_for_score(score)
    setup = build_setup(instrument, score, label, levels, snapshot, patterns)
    scenarios = build_scenarios(instrument, levels, snapshot, score)
    stats = compute_stats(instrument)

    # Cohérence : beaucoup de figures contradictoires -> confiance réduite.
    biases = {p.bias for p in patterns if p.bias != "neutre"}
    if len(biases) > 1:
        confidence *= 0.85
        warnings.append(
            "Des figures haussières et baissières coexistent : signaux contradictoires, "
            "prudence sur la taille de position."
        )
    if float(snapshot.get("adx", 0.0) or trend.get("adx", 0)) < 18:
        confidence *= 0.9
    confidence = max(0.1, min(0.95, confidence))

    return AnalysisResult(
        instrument=instrument,
        price=price,
        trend=trend,
        indicators={**snapshot, "score_notes": score_notes},
        levels=levels,
        patterns=patterns,
        score=score,
        label=label,
        confidence=confidence,
        setup=setup,
        scenarios=scenarios,
        stats=stats,
        warnings=warnings,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# --------------------------------------------------------------------- #
#  Rendu texte (affichage + prompt LLM)
# --------------------------------------------------------------------- #

def render_report(result: AnalysisResult) -> str:
    """Rapport technique en français, lisible par un humain *et* par le LLM."""
    instrument = result.instrument
    lines: list[str] = []
    lines.append(f"### Instrument : {instrument.symbol} — {instrument.name or instrument.symbol}")
    lines.append(
        f"- Source des données : {instrument.source} | Unité de temps : {instrument.timeframe} "
        f"| Bougies : {len(instrument.candles)} | Période : {result.stats['period']}"
    )
    lines.append(f"- Dernier prix : {result.price:.6f} {instrument.currency}")

    lines.append("\n### Tendance")
    lines.append(
        f"- Tendance détectée : **{result.trend['label']}** (score interne {result.trend['score']:+.1f})"
    )
    lines.append(f"- Structure de marché : {result.trend['structure']}")
    for detail in result.trend.get("details", []):
        lines.append(f"- {detail}")

    lines.append("\n### Indicateurs")
    snapshot = result.indicators
    lines.append(
        f"- RSI(14) : {snapshot['rsi14']} | MACD : {snapshot['macd']} "
        f"(histogramme {snapshot['macd_histogram']}) | Croisement récent : {snapshot['macd_cross']}"
    )
    lines.append(
        f"- ATR(14) : {snapshot['atr14']} ({snapshot['atr_pct']} % du prix) "
        f"| Bollinger : {snapshot['bollinger_lower']} / {snapshot['bollinger_middle']} / "
        f"{snapshot['bollinger_upper']}"
    )
    lines.append(
        f"- Stochastique %K/%D : {snapshot['stochastic_k']} / {snapshot['stochastic_d']} "
        f"| Williams %R : {snapshot['williams_r']} | Volume : {snapshot['volume_ratio']}× moyenne"
    )
    for reading in snapshot.get("readings", []):
        lines.append(f"- {reading}")

    lines.append("\n### Niveaux clés (calculés sur les pivots du cours)")
    levels = result.levels
    supports = ", ".join(
        f"{level['price']:.4f} ({level['touches']} touches, {level['distance_pct']:.2f} %)"
        for level in levels.get("supports", [])[:4]
    ) or "aucun support identifié sous le prix"
    resistances = ", ".join(
        f"{level['price']:.4f} ({level['touches']} touches, {level['distance_pct']:.2f} %)"
        for level in levels.get("resistances", [])[:4]
    ) or "aucune résistance identifiée au-dessus du prix"
    lines.append(f"- Supports : {supports}")
    lines.append(f"- Résistances : {resistances}")
    lines.append(
        f"- Plus haut du range : {levels['period_high']:.4f} | Plus bas : {levels['period_low']:.4f} "
        f"| Position dans le range : {levels['range_position_pct']} %"
    )

    lines.append("\n### Figures détectées")
    if result.patterns:
        for pattern in result.patterns[:8]:
            extra = ""
            if pattern.target is not None:
                extra += f" (objectif {pattern.target:.4f})"
            if pattern.invalidation is not None:
                extra += f" (invalidation {pattern.invalidation:.4f})"
            lines.append(
                f"- **{pattern.name}** [{pattern.bias}, confiance {pattern.confidence:.0%}] "
                f": {pattern.description}{extra}"
            )
    else:
        lines.append("- Aucune figure chartiste nette détectée sur la période analysée.")

    lines.append("\n### Synthèse technique")
    lines.append(f"- Score directionnel : {result.score:+.1f} / 100 → **{result.label}**")
    lines.append(f"- Confiance du moteur : {result.confidence:.0%}")
    for note in result.indicators.get("score_notes", []):
        lines.append(f"- {note}")

    setup = result.setup
    lines.append("\n### Plan de trading proposé (à valider)")
    lines.append(
        f"- Direction : {setup['direction']} | Entrée : {setup['entry']:.4f} | "
        f"Stop : {setup['stop']:.4f} | Objectif 1 : {setup['target1']:.4f} | "
        f"Objectif 2 : {setup['target2']:.4f} | Ratio R/R : {setup['risk_reward']}"
    )
    lines.append(f"- Gestion du risque : {setup['position_size_hint']}")

    lines.append("\n### Scénarios")
    for scenario in result.scenarios:
        lines.append(
            f"- **{scenario['name']}** : {scenario['trigger']} → objectifs "
            f"{', '.join(f'{value:.4f}' for value in scenario['targets'])} ; "
            f"invalidation : {scenario['invalidation']}"
        )

    lines.append("\n### Statistiques")
    stats = result.stats
    lines.append(
        f"- Variation 5/20/60 périodes : {stats['variation_5']} % / {stats['variation_20']} % / "
        f"{stats['variation_60']} % | Volatilité annualisée estimée : "
        f"{stats['annualized_volatility_pct']} %"
    )
    lines.append(
        f"- Séquence en cours : {stats['consecutive_up_days']} hausse(s) consécutive(s), "
        f"{stats['consecutive_down_days']} baisse(s) consécutive(s)"
    )
    if result.warnings:
        lines.append("\n### Réserves")
        for warning in result.warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def to_rag_query(result: AnalysisResult) -> str:
    """Transforme l'analyse en requête de recherche pour la base de connaissances."""
    parts = [
        f"analyse graphique {result.instrument.symbol} tendance {result.trend['label']}",
        f"structure {result.trend['structure']}",
        f"indicateurs RSI {result.indicators['rsi14']} MACD "
        f"{'haussier' if result.indicators['macd_histogram'] > 0 else 'baissier'} "
        f"ADX {result.trend['adx']}",
        " ".join(pattern.name for pattern in result.patterns[:5]),
        "supports résistances niveaux cassure",
        f"scénario {'haussier' if result.score > 0 else 'baissier'} gestion du risque stop loss",
    ]
    return " | ".join(part for part in parts if part.strip())
