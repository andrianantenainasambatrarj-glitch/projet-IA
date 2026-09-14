"""Récupération des données de marché, avec repli hors-ligne.

Ordre des tentatives (provider = ``auto``) :

1. **yfinance** (si le paquet est installé) ;
2. **API publique Yahoo Finance** (JSON, sans clé) ;
3. **Stooq** (CSV, sans clé, actions/indices US & EU) ;
4. **données de démonstration** générées localement — l'application
   reste donc toujours utilisable, même sans Internet ni clé API.

Aucune clé API n'est nécessaire pour les données de marché.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .analysis import Candle, Instrument
from .config import Settings, get_settings

# ------------------------------------------------------------------ #
#  Paramètres de marché
# ------------------------------------------------------------------ #

PERIODS: dict[str, str] = {
    "1d": "1 jour",
    "5d": "5 jours",
    "1mo": "1 mois",
    "3mo": "3 mois",
    "6mo": "6 mois",
    "1y": "1 an",
    "2y": "2 ans",
    "5y": "5 ans",
    "max": "max",
}

INTERVALS: dict[str, str] = {
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h": "1 heure",
    "1d": "journalier",
    "1wk": "hebdomadaire",
    "1mo": "mensuel",
}

#: Durée approximative (en jours) couverte par un period.
_PERIOD_DAYS = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 732, "5y": 1830, "max": 3650,
}

POPULAR_SYMBOLS: list[dict[str, str]] = [
    {"symbol": "AAPL", "name": "Apple Inc.", "asset": "Action US"},
    {"symbol": "MSFT", "name": "Microsoft", "asset": "Action US"},
    {"symbol": "NVDA", "name": "NVIDIA", "asset": "Action US"},
    {"symbol": "TSLA", "name": "Tesla", "asset": "Action US"},
    {"symbol": "AMZN", "name": "Amazon", "asset": "Action US"},
    {"symbol": "^GSPC", "name": "S&P 500", "asset": "Indice US"},
    {"symbol": "^IXIC", "name": "Nasdaq Composite", "asset": "Indice US"},
    {"symbol": "^FCHI", "name": "CAC 40", "asset": "Indice FR"},
    {"symbol": "^GDAXI", "name": "DAX 40", "asset": "Indice DE"},
    {"symbol": "MC.PA", "name": "LVMH", "asset": "Action FR"},
    {"symbol": "TTE.PA", "name": "TotalEnergies", "asset": "Action FR"},
    {"symbol": "AI.PA", "name": "Air Liquide", "asset": "Action FR"},
    {"symbol": "BTC-USD", "name": "Bitcoin", "asset": "Crypto"},
    {"symbol": "ETH-USD", "name": "Ethereum", "asset": "Crypto"},
    {"symbol": "SOL-USD", "name": "Solana", "asset": "Crypto"},
    {"symbol": "EURUSD=X", "name": "EUR/USD", "asset": "Devise"},
    {"symbol": "GBPUSD=X", "name": "GBP/USD", "asset": "Devise"},
    {"symbol": "USDJPY=X", "name": "USD/JPY", "asset": "Devise"},
    {"symbol": "GC=F", "name": "Or (futures)", "asset": "Matière première"},
    {"symbol": "CL=F", "name": "Pétrole WTI", "asset": "Matière première"},
]


class MarketDataError(RuntimeError):
    """Erreur de récupération des données de marché."""


@dataclass
class MarketQuote:
    """Cotation synthétique (utile pour l'affichage)."""

    symbol: str
    price: float
    change_pct: float
    source: str
    currency: str = "USD"
    as_of: str = ""


# ------------------------------------------------------------------ #
#  Cache mémoire
# ------------------------------------------------------------------ #

_CACHE: dict[str, tuple[float, Instrument]] = {}
_CACHE_TTL = 180.0  # secondes


def _cache_key(symbol: str, period: str, interval: str, provider: str) -> str:
    return f"{symbol.upper()}|{period}|{interval}|{provider}"


def clear_cache() -> None:
    _CACHE.clear()


# ------------------------------------------------------------------ #
#  Validation
# ------------------------------------------------------------------ #

def normalize_symbol(symbol: str) -> str:
    cleaned = (symbol or "").strip()
    if not cleaned:
        raise MarketDataError("Symbole vide.")
    if len(cleaned) > 24:
        raise MarketDataError("Symbole trop long.")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.^=-_")
    if not set(cleaned).issubset(allowed):
        raise MarketDataError(
            "Symbole invalide : utilisez des lettres, chiffres et les caractères . ^ = - _ "
            "(exemples : AAPL, BTC-USD, EURUSD=X, ^FCHI, MC.PA)."
        )
    return cleaned.upper()


def validate_params(period: str, interval: str) -> tuple[str, str]:
    period = (period or "6mo").lower()
    interval = (interval or "1d").lower()
    if period not in PERIODS:
        raise MarketDataError(f"Période invalide. Choix : {', '.join(PERIODS)}")
    if interval not in INTERVALS:
        raise MarketDataError(f"Unité de temps invalide. Choix : {', '.join(INTERVALS)}")
    if interval.endswith("m") and _PERIOD_DAYS.get(period, 186) > 60:
        # Yahoo limite l'historique intraday ; on ajuste automatiquement.
        period = "1mo"
    return period, interval


# ------------------------------------------------------------------ #
#  Fournisseurs
# ------------------------------------------------------------------ #

def _from_yfinance(symbol: str, period: str, interval: str, timeout: float) -> Instrument:
    """Données via le paquet yfinance (le plus fiable, si installé)."""
    try:
        import yfinance  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise MarketDataError("yfinance non installé.") from exc

    ticker = yfinance.Ticker(symbol)
    frame = ticker.history(period=period, interval=interval, auto_adjust=False)
    if frame is None or frame.empty:
        raise MarketDataError(f"Aucune donnée yfinance pour {symbol}.")
    candles: list[Candle] = []
    for timestamp, row in frame.iterrows():
        try:
            moment = timestamp.to_pydatetime()
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    t=int(moment.timestamp()),
                    o=float(row["Open"]),
                    h=float(row["High"]),
                    l=float(row["Low"]),
                    c=float(row["Close"]),
                    v=float(row.get("Volume") or 0.0),
                )
            )
        except Exception:
            continue
    if len(candles) < 10:
        raise MarketDataError(f"Historique insuffisant pour {symbol}.")
    info: dict[str, Any] = {}
    try:
        info = ticker.fast_info or {}
    except Exception:
        info = {}
    return Instrument(
        symbol=symbol,
        name=str(info.get("name", symbol)) if isinstance(info, dict) else symbol,
        currency=str(info.get("currency", "USD")) if isinstance(info, dict) else "USD",
        timeframe=interval,
        source="yfinance",
        candles=candles,
    )


def _from_yahoo_api(symbol: str, period: str, interval: str, timeout: float) -> Instrument:
    """API JSON publique de Yahoo Finance (aucune clé requise)."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": period, "interval": interval, "includePrePost": "false"}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = client.get(url, params=params)
    if response.status_code >= 400:
        raise MarketDataError(f"Yahoo a répondu {response.status_code} pour {symbol}.")
    payload = response.json()
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        raise MarketDataError(f"Réponse Yahoo vide pour {symbol}.")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes, volumes = (
        quote.get("open") or [],
        quote.get("high") or [],
        quote.get("low") or [],
        quote.get("close") or [],
        quote.get("volume") or [],
    )
    candles: list[Candle] = []
    for index, stamp in enumerate(timestamps):
        try:
            open_value = opens[index]
            high_value = highs[index]
            low_value = lows[index]
            close_value = closes[index]
            if None in (open_value, high_value, low_value, close_value):
                continue
            candles.append(
                Candle(
                    t=int(stamp),
                    o=float(open_value),
                    h=float(high_value),
                    l=float(low_value),
                    c=float(close_value),
                    v=float(volumes[index] or 0.0) if index < len(volumes) else 0.0,
                )
            )
        except Exception:
            continue
    if len(candles) < 10:
        raise MarketDataError(f"Historique Yahoo insuffisant pour {symbol}.")
    meta = result.get("meta") or {}
    return Instrument(
        symbol=str(meta.get("symbol", symbol)),
        name=str(meta.get("longName") or meta.get("shortName") or symbol),
        currency=str(meta.get("currency", "USD")),
        timeframe=str(meta.get("dataGranularity", interval)),
        source="yahoo",
        candles=candles,
    )


def _from_stooq(symbol: str, period: str, interval: str, timeout: float) -> Instrument:
    """Stooq (CSV, sans clé) — unités journalières/hébdomadaires uniquement."""
    if interval not in {"1d", "1wk"}:
        raise MarketDataError("Stooq ne fournit que du journalier/hebdomadaire.")
    ticker = symbol.lower().replace("^", "")
    if "." in ticker or "-" in ticker:
        base, _, suffix = ticker.partition(".")
        if suffix in {"pa", "de", "us", "uk", "jp"}:
            ticker = f"{base}.{suffix}"
        else:
            ticker = f"{base}.us"
    else:
        ticker = f"{ticker}.us"
    url = f"https://stooq.com/q/d/l/?s={ticker}&i={'w' if interval == '1wk' else 'd'}"
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
    text = response.text.strip()
    if response.status_code >= 400 or not text.lower().startswith("date"):
        raise MarketDataError(f"Stooq n'a pas de données pour {symbol}.")
    candles: list[Candle] = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 5:
            continue
        try:
            moment = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    t=int(moment.timestamp()),
                    o=float(parts[1]),
                    h=float(parts[2]),
                    l=float(parts[3]),
                    c=float(parts[4]),
                    v=float(parts[5]) if len(parts) > 5 and parts[5] else 0.0,
                )
            )
        except Exception:
            continue
    days = _PERIOD_DAYS.get(period, 186)
    if interval == "1wk":
        days *= 1  # déjà hebdo
        candles = candles[-max(20, days // 7) :]
    else:
        candles = candles[-max(30, days) :]
    if len(candles) < 10:
        raise MarketDataError(f"Historique Stooq insuffisant pour {symbol}.")
    return Instrument(symbol=symbol, name=symbol, timeframe=interval, source="stooq", candles=candles)


# ------------------------------------------------------------------ #
#  Mode démonstration (hors-ligne, déterministe par symbole)
# ------------------------------------------------------------------ #

_DEMO_PROFILES: dict[str, tuple[float, float, float]] = {
    # symbole : (prix de départ, volatilité quotidienne, dérive annualisée)
    "BTC-USD": (62000.0, 0.028, 0.45),
    "ETH-USD": (3100.0, 0.032, 0.30),
    "SOL-USD": (145.0, 0.042, 0.55),
    "EURUSD=X": (1.0850, 0.0045, 0.01),
    "GBPUSD=X": (1.2700, 0.005, -0.01),
    "USDJPY=X": (152.0, 0.006, 0.05),
    "GC=F": (2350.0, 0.011, 0.12),
    "CL=F": (78.0, 0.021, -0.05),
    "^GSPC": (5400.0, 0.009, 0.11),
    "^IXIC": (17200.0, 0.012, 0.14),
    "^FCHI": (7600.0, 0.011, 0.06),
    "^GDAXI": (18300.0, 0.011, 0.08),
}


def demo_instrument(symbol: str, period: str = "6mo", interval: str = "1d", candles: int = 260) -> Instrument:
    """Génère une série réaliste et déterministe (mêmes données à chaque appel)."""
    seed = int(hashlib.sha256(f"{symbol}|{interval}".encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    base_price, daily_vol, drift = _DEMO_PROFILES.get(
        symbol.upper(), (100.0 + (seed % 400), 0.016, 0.08)
    )
    if interval.endswith("m"):
        daily_vol *= 0.35
    elif interval == "1wk":
        daily_vol *= 2.2
    elif interval == "1mo":
        daily_vol *= 4.5

    steps = max(60, min(int(candles), 800))
    step_days = 1 if interval in {"1d", "1wk", "1mo"} else 1
    price = base_price * (1.0 - drift * 0.15)
    series: list[Candle] = []
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    regime_length = max(12, steps // 6)
    regime = 0
    for index in range(steps):
        if index % regime_length == 0:
            regime = rng.choice([1.0, 1.0, -1.0, -0.4, 0.4, 0.0])
        momentum = drift / 252.0 * regime * rng.uniform(1.4, 2.4)
        shock = rng.gauss(momentum, daily_vol)
        open_price = price
        close_price = max(0.0001, open_price * (1.0 + shock))
        wick = abs(rng.gauss(0, daily_vol * 0.6)) * open_price
        high = max(open_price, close_price) + wick
        low = max(0.0001, min(open_price, close_price) - abs(rng.gauss(0, daily_vol * 0.6)) * open_price)
        volume = abs(rng.gauss(1_000_000, 250_000)) * (1 + abs(shock) * 12)
        moment = end - timedelta(days=step_days * (steps - index - 1))
        series.append(
            Candle(
                t=int(moment.timestamp()),
                o=round(open_price, 6),
                h=round(high, 6),
                l=round(low, 6),
                c=round(close_price, 6),
                v=round(volume, 2),
            )
        )
        price = close_price

    name = next(
        (item["name"] for item in POPULAR_SYMBOLS if item["symbol"] == symbol.upper()),
        f"{symbol.upper()} (démo)",
    )
    currency = "EUR" if symbol.upper().endswith(("=X", ".PA", ".DE")) else "USD"
    return Instrument(
        symbol=symbol.upper(),
        name=name,
        currency=currency,
        timeframe=interval,
        source="demo",
        candles=series,
    )


# ------------------------------------------------------------------ #
#  API publique
# ------------------------------------------------------------------ #

def get_instrument(
    symbol: str,
    period: str | None = None,
    interval: str | None = None,
    *,
    settings: Settings | None = None,
    force_refresh: bool = False,
) -> Instrument:
    """Renvoie la série de prix d'un symbole, avec repli automatique."""
    settings = settings or get_settings()
    symbol = normalize_symbol(symbol)
    period, interval = validate_params(period or settings.default_period, interval or settings.default_interval)
    provider = (settings.market_provider or "auto").strip().lower()

    key = _cache_key(symbol, period, interval, provider)
    now = time.time()
    if not force_refresh and key in _CACHE:
        created, instrument = _CACHE[key]
        if now - created < _CACHE_TTL:
            return instrument

    errors: list[str] = []

    def try_provider(name: str, function) -> Optional[Instrument]:
        try:
            instrument = function(symbol, period, interval, settings.market_timeout_s)
            instrument.timeframe = interval
            return instrument
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            return None

    instrument: Optional[Instrument] = None
    if provider in {"auto", "yfinance"}:
        instrument = try_provider("yfinance", _from_yfinance)
    if instrument is None and provider in {"auto", "yahoo", "yfinance"}:
        instrument = try_provider("yahoo", _from_yahoo_api)
    if instrument is None and provider in {"auto", "stooq"}:
        instrument = try_provider("stooq", _from_stooq)
    if instrument is None and provider in {"auto", "demo"}:
        instrument = demo_instrument(symbol, period, interval, settings.demo_candles)
        instrument.name = instrument.name or symbol
        if errors:
            instrument.notes.append(
                "Données réelles inaccessibles ("
                + " ; ".join(error[:90] for error in errors[:2])
                + ") — série de démonstration utilisée."
            )

    if instrument is None:
        raise MarketDataError(
            "Impossible de récupérer les données de marché : " + " ; ".join(errors[:3])
        )

    _CACHE[key] = (now, instrument)
    return instrument


def get_quote(symbol: str, *, settings: Settings | None = None) -> MarketQuote:
    """Dernière cotation + variation en %."""
    instrument = get_instrument(symbol, "5d", "1d", settings=settings)
    closes = instrument.closes
    price = closes[-1]
    change = ((price - closes[-2]) / closes[-2] * 100.0) if len(closes) > 1 else 0.0
    return MarketQuote(
        symbol=instrument.symbol,
        price=round(price, 6),
        change_pct=round(change, 3),
        source=instrument.source,
        currency=instrument.currency,
        as_of=instrument.candles[-1].day,
    )


#: Cache du diagnostic (évite de sonder le réseau à chaque appel de /api/health).
_STATUS_CACHE: dict[str, Any] = {"time": 0.0, "provider": "", "data": None}
_STATUS_TTL = 300.0


def market_status(settings: Settings | None = None) -> dict[str, Any]:
    """Diagnostic : quels fournisseurs répondent réellement ? (mis en cache 5 min)"""
    settings = settings or get_settings()
    provider = (settings.market_provider or "auto").strip().lower()
    now = time.time()
    if (
        _STATUS_CACHE["data"] is not None
        and _STATUS_CACHE["provider"] == provider
        and now - _STATUS_CACHE["time"] < _STATUS_TTL
    ):
        return _STATUS_CACHE["data"]
    checks: list[dict[str, Any]] = []
    probe_timeout = min(8.0, max(3.0, settings.market_timeout_s / 2))
    mapping = {
        "yfinance": lambda: _from_yfinance("AAPL", "1mo", "1d", probe_timeout),
        "yahoo": lambda: _from_yahoo_api("AAPL", "1mo", "1d", probe_timeout),
        "stooq": lambda: _from_stooq("AAPL", "1mo", "1d", probe_timeout),
    }
    for name, function in mapping.items():
        if provider not in {"auto", name}:
            checks.append({"provider": name, "status": "désactivé"})
            continue
        try:
            instrument = function()
            checks.append(
                {
                    "provider": name,
                    "status": "disponible",
                    "candles": len(instrument.candles),
                    "dernier": instrument.candles[-1].day,
                }
            )
        except Exception as exc:
            checks.append({"provider": name, "status": "indisponible", "detail": str(exc)[:160]})
    checks.append({"provider": "demo", "status": "toujours disponible (hors-ligne)"})
    active = next((item["provider"] for item in checks if item["status"] == "disponible"), "demo")
    resultat = {
        "provider_configure": provider,
        "provider_actif": active,
        "checks": checks,
        "symboles_populaires": POPULAR_SYMBOLS,
        "periodes": PERIODS,
        "intervalles": INTERVALS,
    }
    _STATUS_CACHE.update({"time": now, "provider": provider, "data": resultat})
    return resultat
