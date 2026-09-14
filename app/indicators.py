"""Indicateurs techniques en Python pur (aucune dépendance, testable hors-ligne).

Toutes les fonctions renvoient des listes alignées sur l'entrée, avec ``None``
pour la période de chauffe.
"""

from __future__ import annotations

from typing import Optional, Sequence

Num = Optional[float]


def _clean(values: Sequence[float]) -> list[float]:
    return [float(v) for v in values if v is not None]


# --------------------------------------------------------------------- #
#  Moyennes
# --------------------------------------------------------------------- #

def sma(values: Sequence[Num], period: int) -> list[Num]:
    """Moyenne mobile simple (tolérante aux ``None`` : la fenêtre est ignorée)."""
    period = max(1, int(period))
    out: list[Num] = [None] * len(values)
    window = 0.0
    valid = 0
    for index, value in enumerate(values):
        if value is not None:
            window += float(value)
            valid += 1
        if index >= period:
            previous = values[index - period]
            if previous is not None:
                window -= float(previous)
                valid -= 1
        if index >= period - 1 and valid == period:
            out[index] = window / period
    return out


def ema(values: Sequence[float], period: int) -> list[Num]:
    """Moyenne mobile exponentielle (amorçage par SMA)."""
    period = max(1, int(period))
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    multiplier = 2.0 / (period + 1)
    seed = sum(float(v) for v in values[:period]) / period
    out[period - 1] = seed
    previous = seed
    for index in range(period, len(values)):
        previous = (float(values[index]) - previous) * multiplier + previous
        out[index] = previous
    return out


def wilder_smooth(values: Sequence[Num], period: int) -> list[Num]:
    """Lissage de Wilder (utilisé par RSI, ATR, ADX)."""
    period = max(1, int(period))
    out: list[Num] = [None] * len(values)
    buffer: list[float] = []
    previous: Num = None
    for index, value in enumerate(values):
        if value is None:
            continue
        buffer.append(float(value))
        if len(buffer) < period:
            continue
        if previous is None:
            previous = sum(buffer[:period]) / period
        else:
            previous = (previous * (period - 1) + float(value)) / period
        out[index] = previous
    return out


def stddev(values: Sequence[Num], period: int) -> list[Num]:
    """Écart-type glissant (population), tolérant aux ``None``."""
    period = max(1, int(period))
    out: list[Num] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        if any(value is None for value in window):
            continue
        numbers = [float(value) for value in window]  # type: ignore[arg-type]
        mean = sum(numbers) / period
        variance = sum((value - mean) ** 2 for value in numbers) / period
        out[index] = variance ** 0.5
    return out


# --------------------------------------------------------------------- #
#  Momentum
# --------------------------------------------------------------------- #

def rsi(values: Sequence[float], period: int = 14) -> list[Num]:
    """RSI de Wilder."""
    out: list[Num] = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains: list[Num] = [None]
    losses: list[Num] = [None]
    for index in range(1, len(values)):
        change = float(values[index]) - float(values[index - 1])
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = wilder_smooth(gains, period)
    avg_loss = wilder_smooth(losses, period)
    for index in range(len(values)):
        gain = avg_gain[index]
        loss = avg_loss[index]
        if gain is None or loss is None:
            continue
        if loss <= 1e-12:
            out[index] = 100.0
        else:
            rs = gain / loss
            out[index] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, list[Num]]:
    """MACD : ligne, signal, histogramme."""
    fast_line = ema(values, fast)
    slow_line = ema(values, slow)
    macd_line: list[Num] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(fast_line, slow_line)
    ]
    valid = [(index, value) for index, value in enumerate(macd_line) if value is not None]
    signal_line: list[Num] = [None] * len(values)
    if len(valid) >= signal:
        smoothed = ema([value for _, value in valid], signal)
        for (index, _), value in zip(valid, smoothed):
            signal_line[index] = value
    histogram: list[Num] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def stochastic(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
    k_period: int = 14, d_period: int = 3,
) -> dict[str, list[Num]]:
    """Stochastique %K / %D."""
    k_values: list[Num] = [None] * len(closes)
    for index in range(k_period - 1, len(closes)):
        window_high = max(float(v) for v in highs[index - k_period + 1 : index + 1])
        window_low = min(float(v) for v in lows[index - k_period + 1 : index + 1])
        span = window_high - window_low
        k_values[index] = (
            50.0 if span <= 1e-12 else (float(closes[index]) - window_low) / span * 100.0
        )
    d_values = sma([v for v in k_values], d_period) if any(k_values) else [None] * len(closes)
    return {"k": k_values, "d": d_values}


def williams_r(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> list[Num]:
    """%R de Williams (-100 à 0)."""
    out: list[Num] = [None] * len(closes)
    for index in range(period - 1, len(closes)):
        window_high = max(float(v) for v in highs[index - period + 1 : index + 1])
        window_low = min(float(v) for v in lows[index - period + 1 : index + 1])
        span = window_high - window_low
        out[index] = 0.0 if span <= 1e-12 else (window_high - float(closes[index])) / span * -100.0
    return out


# --------------------------------------------------------------------- #
#  Volatilité
# --------------------------------------------------------------------- #

def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[Num]:
    """True Range."""
    out: list[Num] = [None] * len(closes)
    for index in range(len(closes)):
        if index == 0:
            out[index] = float(highs[index]) - float(lows[index])
            continue
        previous_close = float(closes[index - 1])
        out[index] = max(
            float(highs[index]) - float(lows[index]),
            abs(float(highs[index]) - previous_close),
            abs(float(lows[index]) - previous_close),
        )
    return out


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> list[Num]:
    """Average True Range (lissage Wilder)."""
    return wilder_smooth(true_range(highs, lows, closes), period)


def bollinger(
    values: Sequence[float], period: int = 20, mult: float = 2.0
) -> dict[str, list[Num]]:
    """Bandes de Bollinger (moyenne, supérieure, inférieure, largeur)."""
    middle = sma(values, period)
    deviation = stddev(values, period)
    upper: list[Num] = [
        (m + mult * s) if (m is not None and s is not None) else None
        for m, s in zip(middle, deviation)
    ]
    lower: list[Num] = [
        (m - mult * s) if (m is not None and s is not None) else None
        for m, s in zip(middle, deviation)
    ]
    width: list[Num] = [
        ((u - l) / m * 100.0) if (u is not None and l is not None and m) else None
        for u, l, m in zip(upper, lower, middle)
    ]
    return {"middle": middle, "upper": upper, "lower": lower, "width": width}


def donchian(
    highs: Sequence[float], lows: Sequence[float], period: int = 20
) -> dict[str, list[Num]]:
    """Canaux de Donchian."""
    upper: list[Num] = [None] * len(highs)
    lower: list[Num] = [None] * len(lows)
    for index in range(period - 1, len(highs)):
        upper[index] = max(float(v) for v in highs[index - period + 1 : index + 1])
        lower[index] = min(float(v) for v in lows[index - period + 1 : index + 1])
    return {"upper": upper, "lower": lower}


def adx(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> dict[str, list[Num]]:
    """ADX / +DI / -DI (force et direction de la tendance)."""
    plus_dm: list[Num] = [None]
    minus_dm: list[Num] = [None]
    for index in range(1, len(closes)):
        up_move = float(highs[index]) - float(highs[index - 1])
        down_move = float(lows[index - 1]) - float(lows[index])
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
    average_range = wilder_smooth(true_range(highs, lows, closes), period)
    plus_smoothed = wilder_smooth(plus_dm, period)
    minus_smoothed = wilder_smooth(minus_dm, period)

    plus_di: list[Num] = [None] * len(closes)
    minus_di: list[Num] = [None] * len(closes)
    dx: list[Num] = [None] * len(closes)
    for index in range(len(closes)):
        range_value = average_range[index]
        if not range_value:
            continue
        plus_value = (plus_smoothed[index] or 0.0) / range_value * 100.0
        minus_value = (minus_smoothed[index] or 0.0) / range_value * 100.0
        plus_di[index] = plus_value
        minus_di[index] = minus_value
        total = plus_value + minus_value
        dx[index] = 0.0 if total <= 1e-12 else abs(plus_value - minus_value) / total * 100.0
    return {"adx": wilder_smooth(dx, period), "plus_di": plus_di, "minus_di": minus_di}


def obv(closes: Sequence[float], volumes: Sequence[float]) -> list[Num]:
    """On-Balance Volume."""
    out: list[Num] = [0.0]
    for index in range(1, len(closes)):
        volume = float(volumes[index] or 0.0)
        if float(closes[index]) > float(closes[index - 1]):
            out.append(out[-1] + volume)
        elif float(closes[index]) < float(closes[index - 1]):
            out.append(out[-1] - volume)
        else:
            out.append(out[-1])
    return out


def volume_profile(
    closes: Sequence[float], volumes: Sequence[float], bins: int = 24
) -> list[dict[str, float]]:
    """Profil de volume simplifié : volume cumulé par tranche de prix."""
    if not closes:
        return []
    low = min(float(v) for v in closes)
    high = max(float(v) for v in closes)
    if high - low < 1e-12:
        return [{"price": low, "volume": sum(float(v or 0) for v in volumes)}]
    step = (high - low) / bins
    buckets = [0.0] * bins
    for close, volume in zip(closes, volumes):
        position = min(bins - 1, int((float(close) - low) / step))
        buckets[position] += float(volume or 0.0)
    return [
        {"price": round(low + step * (index + 0.5), 6), "volume": round(volume, 2)}
        for index, volume in enumerate(buckets)
    ]


# --------------------------------------------------------------------- #
#  Utilitaires de tendance
# --------------------------------------------------------------------- #

def slope(values: Sequence[Num], window: int = 20) -> float:
    """Pente normalisée (%) des `window` dernières valeurs valides."""
    window_values = [value for value in values if value is not None][-window:]
    size = len(window_values)
    if size < 3:
        return 0.0
    mean_x = (size - 1) / 2
    mean_y = sum(window_values) / size
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(window_values))
    denominator = sum((index - mean_x) ** 2 for index in range(size))
    if denominator <= 1e-12 or abs(mean_y) < 1e-12:
        return 0.0
    return (numerator / denominator) / abs(mean_y) * 100.0 * size


def last_valid(values: Sequence[Num]) -> Num:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def cross_signal(fast: Sequence[Num], slow: Sequence[Num], lookback: int = 5) -> str:
    """Détecte un croisement récent entre deux courbes."""
    pairs = [
        (fast[index], slow[index])
        for index in range(max(0, len(fast) - lookback), len(fast))
        if fast[index] is not None and slow[index] is not None
    ]
    for index in range(1, len(pairs)):
        previous_fast, previous_slow = pairs[index - 1]
        current_fast, current_slow = pairs[index]
        if previous_fast <= previous_slow and current_fast > current_slow:
            return "haussier"
        if previous_fast >= previous_slow and current_fast < current_slow:
            return "baissier"
    return "aucun"
