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
import re
import threading
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
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h": "1 heure",
    "4h": "4 heures",
    "1d": "journalier",
    "1wk": "hebdomadaire",
    "1mo": "mensuel",
}

#: Unité de temps lue sur une capture → unité de temps des données de marché.
#: Couvre les écritures des plateformes (MT4/MT5 : M5, H1, D1, W1, MN) ET celles
#: de TradingView (5m, 15m, 1h, 4h, 1D, 1W, 1M) ainsi que le français écrit.
_TIMEFRAME_MINUTES: dict[int, str] = {
    1: "1m", 2: "1m", 3: "1m", 5: "5m", 10: "5m", 15: "15m",
    30: "30m", 45: "30m", 60: "1h",
}
_TIMEFRAME_HOURS: dict[int, str] = {
    1: "1h", 2: "1h", 3: "1h", 4: "4h", 6: "4h", 8: "4h", 12: "4h", 24: "1d",
}

#: Unités de temps fixes (aucun chiffre à convertir) — testées AVANT les numériques.
#: Attention à la casse : « 1M » = mensuel, alors que « 1m » = 1 minute.
_TF_FIXES: tuple[tuple[str, str], ...] = (
    (r"\b(?:D\s?1|1\s?[Dd]|daily|journali\w*|quotidien\w*|jour\w*)\b", "1d"),
    (r"\b(?:W\s?1|1\s?[Ww]|weekly|hebdo\w*|semaine\w*)\b", "1wk"),
    (r"\b(?:MN|1\s?M|monthly|mensuel\w*|mois)\b", "1mo"),
)

#: Unités de temps numériques : (motif, groupe de lecture, nature).
_TF_NUMERIQUES: tuple[tuple[str, str], ...] = (
    (r"\bM\s?(\d{1,2})\b", "minutes"),                        # M1, M5, M15, M30 (MetaTrader)
    (r"\bH\s?(\d{1,2})\b", "heures"),                         # H1, H4 (MetaTrader)
    (r"\b(\d{1,3})\s?(?:minutes?|min|m)\b", "minutes"),        # 5m, 15 min, 30 minutes
    (r"\b(\d{1,2})\s?(?:h|heures?|hours?)\b", "heures"),       # 1h, 4 heures
)


def interval_from_timeframe(texte: str) -> str:
    """Traduit l'unité de temps lue sur une capture en unité de temps de l'application.

    « M5 » comme « 5 minutes » ou « 5m » donnent ``5m`` ; « H4 » / « 4 heures » donnent
    ``4h`` ; « D1 » / « journalier » donnent ``1d`` ; « W1 » → ``1wk`` ; « MN » /
    « mensuel » → ``1mo``. Renvoie ``""`` si rien n'est lisible.

    C'est ce qui permet d'analyser une capture **dans son propre rythme** : une capture
    en 5 minutes est analysée sur des bougies de 5 minutes, pas sur du journalier.
    """
    texte = (texte or "").strip()
    if not texte or "non lisible" in texte.lower():
        return ""
    for motif, intervalle in _TF_FIXES:
        if re.search(motif, texte):
            return intervalle
    for motif, nature in _TF_NUMERIQUES:
        trouve = re.search(motif, texte)
        if not trouve:
            continue
        nombre = int(trouve.group(1))
        if nature == "minutes":
            return _TIMEFRAME_MINUTES.get(
                nombre, "5m" if nombre < 5 else ("30m" if nombre < 60 else "1h")
            )
        return _TIMEFRAME_HOURS.get(nombre, "4h" if nombre < 24 else "1d")
    return ""


#: Période d'historique adaptée à chaque unité de temps (limites des fournisseurs
#: gratuits : 7 jours en 1 minute, 60 jours en 5/15/30 minutes, 2 ans en heures).
PERIODE_PAR_INTERVALLE: dict[str, str] = {
    "1m": "5d",
    "5m": "1mo",
    "15m": "1mo",
    "30m": "1mo",
    "1h": "3mo",
    "4h": "6mo",
    "1d": "6mo",
    "1wk": "2y",
    "1mo": "5y",
}


def default_period_for_interval(interval: str) -> str:
    """Période d'historique à utiliser quand l'utilisateur n'en impose pas."""
    return PERIODE_PAR_INTERVALLE.get((interval or "").lower(), "6mo")


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


def reset_status_cache() -> None:
    """Vide le cache du diagnostic de marché (tests, changement de configuration)."""
    _STATUS_CACHE.update({"time": 0.0, "provider": "", "data": None})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    jours = _PERIOD_DAYS.get(period, 186)
    if interval == "1m" and jours > 7:
        # 1 minute n'est fourni que sur une semaine glissante.
        period = "5d"
    elif interval in {"5m", "15m", "30m"} and jours > 60:
        # Yahoo limite l'intraday court ; on ajuste automatiquement.
        period = "1mo"
    elif interval in {"1h", "4h"} and jours > 366:
        # Historique horaire : deux ans maximum chez les fournisseurs gratuits.
        period = "1y"
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


#: Unités de temps reconstruites à partir d'une unité plus fine.
#: « 4 heures » n'est pas fourni par Yahoo : on regroupe quatre bougies horaires.
_AGGREGATION: dict[str, tuple[str, int]] = {"4h": ("1h", 4)}


def _aggregate_candles(candles: list[Candle], facteur: int) -> list[Candle]:
    """Regroupe ``facteur`` bougies consécutives (O premier, H/L extrêmes, C dernier)."""
    if facteur < 2 or len(candles) < facteur * 2:
        return candles
    sortie: list[Candle] = []
    for debut in range(0, len(candles) - facteur + 1, facteur):
        bloc = candles[debut : debut + facteur]
        sortie.append(
            Candle(
                t=bloc[-1].t,
                o=bloc[0].o,
                h=max(bougie.h for bougie in bloc),
                l=min(bougie.l for bougie in bloc),
                c=bloc[-1].c,
                v=sum(bougie.v for bougie in bloc),
            )
        )
    return sortie or candles


#: Correspondances Stooq pour les symboles Yahoo courants.
_STOOQ_MAP: dict[str, str] = {
    "^GSPC": "^spx",
    "^IXIC": "^ndq",
    "^DJI": "^dji",
    "^FCHI": "^cac",
    "^GDAXI": "^dax",
    "^FTSE": "^ukx",
    "^N225": "^nkx",
    "BTC-USD": "btcusd",
    "ETH-USD": "ethusd",
    "SOL-USD": "solusd",
    "EURUSD=X": "eurusd",
    "GBPUSD=X": "gbpusd",
    "USDJPY=X": "usdjpy",
    "USDCHF=X": "usdchf",
    "AUDUSD=X": "audusd",
    "EURGBP=X": "eurgbp",
    "XAUUSD=X": "xauusd",
    "GC=F": "gc.f",
    "CL=F": "cl.f",
}


def _stooq_ticker(symbol: str) -> str:
    """Traduit un symbole Yahoo (AAPL, ^FCHI, EURUSD=X, BTC-USD) en symbole Stooq."""
    majuscule = symbol.upper()
    if majuscule in _STOOQ_MAP:
        return _STOOQ_MAP[majuscule]
    if majuscule.endswith("=X") and len(majuscule) >= 7:
        return majuscule[:-2].lower()  # EURUSD=X -> eurusd
    if majuscule.endswith("-USD"):
        return majuscule.replace("-", "").lower()  # BTC-USD -> btcusd
    if majuscule.endswith("=F"):
        return majuscule[:-2].lower() + ".f"  # GC=F -> gc.f
    ticker = symbol.lower()
    if "." in ticker:
        base, _, suffix = ticker.partition(".")
        return f"{base}.{suffix}" if suffix in {"pa", "de", "us", "uk", "jp"} else f"{base}.us"
    return f"{ticker}.us"


def _from_stooq(symbol: str, period: str, interval: str, timeout: float) -> Instrument:
    """Stooq (CSV, sans clé) — unités journalières/hébdomadaires uniquement.

    Fonctionne pour les actions, les indices et aussi le forex (EURUSD=X → eurusd)
    et la crypto (BTC-USD → btcusd).
    """
    if interval not in {"1d", "1wk"}:
        raise MarketDataError("Stooq ne fournit que du journalier/hebdomadaire.")
    ticker = _stooq_ticker(symbol)
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
#  Binance (crypto : OHLCV réels, sans clé, adapté aux serveurs)
# ------------------------------------------------------------------ #

_BINANCE_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1wk": "1w",
    "1mo": "1M",
}


def _binance_pair(symbol: str) -> str:
    """BTC-USD → BTCUSDT (Binance cote en USDT/USDC)."""
    base = symbol.upper().replace("-USD", "").replace("-USDT", "").replace("USDT", "")
    return f"{base}USDT"


def _from_binance(symbol: str, period: str, interval: str, timeout: float) -> Instrument:
    """Données crypto via l'API publique Binance (aucune clé, très fiable en datacenter)."""
    if not symbol.upper().endswith(("-USD", "USDT")):
        raise MarketDataError("Binance n'est utilisé que pour les cryptos (ex. BTC-USD).")
    paire = _binance_pair(symbol)
    intervalle = _BINANCE_INTERVALS.get(interval, "1d")
    jours = _PERIOD_DAYS.get(period, 186)
    if intervalle == "1w":
        limite = max(30, jours // 7)
    elif intervalle == "1M":
        limite = max(24, jours // 30)
    elif intervalle.endswith("m"):
        limite = max(120, min(1000, jours * 24 * 60 // int(intervalle[:-1])))
    elif intervalle.endswith("h"):
        limite = max(120, min(1000, jours * 24 // int(intervalle[:-1])))
    else:
        limite = max(60, min(1000, jours))

    dernier_erreur = ""
    for hote in ("https://api.binance.com", "https://api.binance.us"):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(
                    f"{hote}/api/v3/klines",
                    params={"symbol": paire, "interval": intervalle, "limit": limite},
                )
            if response.status_code >= 400:
                dernier_erreur = f"{hote} a répondu {response.status_code}"
                continue
            lignes = response.json()
        except httpx.HTTPError as exc:
            dernier_erreur = f"{hote} injoignable ({exc})"
            continue
        if not isinstance(lignes, list) or len(lignes) < 10:
            dernier_erreur = f"{hote} n'a pas de données pour {paire}"
            continue
        candles = [
            Candle(
                t=int(ligne[0] // 1000),
                o=float(ligne[1]),
                h=float(ligne[2]),
                l=float(ligne[3]),
                c=float(ligne[4]),
                v=float(ligne[5] or 0.0),
            )
            for ligne in lignes
        ]
        return Instrument(
            symbol=symbol.upper(),
            name=f"{_binance_pair(symbol)[:-4]} (crypto)",
            currency="USD",
            timeframe=interval,
            source="binance",
            candles=candles,
        )
    raise MarketDataError(f"Binance : {dernier_erreur or 'aucune donnée'}")


def _is_crypto_symbol(symbol: str) -> bool:
    return symbol.upper().endswith(("-USD", "USDT")) and not symbol.upper().endswith("=X")


# ------------------------------------------------------------------ #
#  Mode démonstration (hors-ligne, déterministe par symbole)
# ------------------------------------------------------------------ #

#: Plafond du nombre de bougies générées en mode démonstration.
_DEMO_CAP = 800

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


#: Nombre de périodes élémentaires par jour, selon l'unité de temps.
_INTERVAL_PER_DAY: dict[str, float] = {
    "1m": 1440.0,
    "5m": 288.0,
    "15m": 96.0,
    "30m": 48.0,
    "1h": 24.0,
    "4h": 6.0,
    "1d": 1.0,
    "1wk": 1 / 7,
    "1mo": 1 / 30,
}


def demo_candle_count(period: str, interval: str, maximum: int = 800) -> int:
    """Nombre de bougies cohérent avec la période et l'unité de temps demandées.

    Corrige une incohérence : la série de démonstration contenait toujours ~260
    bougies journalières, quelle que soit la période (« 1 jour » affichait un an
    de données, « 5 minutes » des bougies quotidiennes).
    """
    jours = float(_PERIOD_DAYS.get(period, 186))
    par_jour = _INTERVAL_PER_DAY.get(interval, 1.0)
    naturel = int(round(jours * par_jour))
    return max(60, min(naturel, max(60, int(maximum))))


def _demo_end(interval: str) -> datetime:
    """Dernière bougie : aujourd'hui (date seule) ou « maintenant » en intraday."""
    maintenant = datetime.now(timezone.utc)
    if interval in {"1d", "1wk", "1mo"}:
        return maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    return maintenant.replace(second=0, microsecond=0)


def _demo_step(interval: str) -> timedelta:
    """Espacement réel entre deux bougies de démonstration."""
    if interval.endswith("m"):
        return timedelta(minutes=int(interval[:-1]))
    if interval.endswith("h"):
        return timedelta(hours=int(interval[:-1]))
    if interval == "1wk":
        return timedelta(days=7)
    if interval == "1mo":
        return timedelta(days=30)
    return timedelta(days=1)


def demo_instrument(
    symbol: str, period: str = "6mo", interval: str = "1d", candles: int | None = None
) -> Instrument:
    """Génère une série réaliste et déterministe (mêmes données à chaque appel).

    La série respecte la période et l'unité de temps demandées ; ``candles``
    (optionnel) plafonne le nombre de bougies générées.
    """
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

    steps = demo_candle_count(period, interval, candles or _DEMO_CAP)
    step = _demo_step(interval)
    price = base_price * (1.0 - drift * 0.15)
    series: list[Candle] = []
    end = _demo_end(interval)
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
        moment = end - step * (steps - index - 1)
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
    period_demande = period or settings.default_period
    period, interval = validate_params(period_demande, interval or settings.default_interval)
    provider = (settings.market_provider or "auto").strip().lower()
    note_ajustement = ""
    if period != (period_demande or "").lower():
        # Yahoo limite l'historique intraday : on le dit explicitement à l'utilisateur
        # au lieu de changer discrètement la période demandée.
        note_ajustement = (
            f"Historique intraday limité : la période « {period_demande} » a été ramenée "
            f"à « {period} » pour l'unité de temps {interval}."
        )

    key = _cache_key(symbol, period, interval, provider)
    now = time.time()
    if not force_refresh and key in _CACHE:
        created, instrument = _CACHE[key]
        if now - created < _CACHE_TTL:
            return instrument

    errors: list[str] = []

    def try_provider(name: str, function) -> Optional[Instrument]:
        try:
            intervalle_reel, facteur = _AGGREGATION.get(interval, (interval, 1))
            if name == "binance":
                # Binance fournit nativement les unités horaires (dont 4h).
                intervalle_reel, facteur = interval, 1
            instrument = function(symbol, period, intervalle_reel, settings.market_timeout_s)
            if facteur > 1:
                instrument.candles = _aggregate_candles(instrument.candles, facteur)
            instrument.timeframe = interval
            return instrument
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            return None

    instrument: Optional[Instrument] = None
    crypto = _is_crypto_symbol(symbol)

    # Pour la crypto, Binance est bien plus fiable depuis un serveur (pas de blocage
    # datacenter) : on l'essaie en premier.
    if crypto and provider in {"auto", "binance"}:
        instrument = try_provider("binance", _from_binance)
    if instrument is None and provider in {"auto", "yfinance"}:
        instrument = try_provider("yfinance", _from_yfinance)
    if instrument is None and provider in {"auto", "yahoo", "yfinance"}:
        instrument = try_provider("yahoo", _from_yahoo_api)
    if instrument is None and provider in {"auto", "stooq"}:
        instrument = try_provider("stooq", _from_stooq)
    if instrument is None and crypto and provider in {"auto", "binance"}:
        instrument = try_provider("binance", _from_binance)
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

    if note_ajustement and note_ajustement not in instrument.notes:
        instrument.notes.append(note_ajustement)
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


_STATUS_LOCK = threading.Lock()
_STATUS_PROBE_EN_COURS = False


def _probe_providers(provider: str, probe_timeout: float) -> list[dict[str, Any]]:
    """Teste réellement chaque fournisseur (appels réseau — lent)."""
    checks: list[dict[str, Any]] = []
    mapping = {
        "yfinance": lambda: _from_yfinance("AAPL", "1mo", "1d", probe_timeout),
        "yahoo": lambda: _from_yahoo_api("AAPL", "1mo", "1d", probe_timeout),
        "stooq": lambda: _from_stooq("AAPL", "1mo", "1d", probe_timeout),
        "binance": lambda: _from_binance("BTC-USD", "1mo", "1d", probe_timeout),
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
    return checks


def _status_from_checks(provider: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble le diagnostic à partir des tests (ordre réellement utilisé)."""
    disponibles = {
        item["provider"] for item in checks if item["status"] == "disponible"
    }
    # Ordre réellement utilisé par get_instrument (hors crypto)…
    ordre = [name for name in ("yfinance", "yahoo", "stooq") if name in disponibles]
    # …et ordre spécifique aux cryptos (Binance en premier).
    ordre_crypto = [name for name in ("binance", "yfinance", "yahoo", "stooq") if name in disponibles]
    aucune_source = not ordre and not ordre_crypto
    return {
        "provider_configure": provider,
        "provider_actif": ordre[0] if ordre else ("binance" if ordre_crypto else "demo"),
        "provider_actif_crypto": ordre_crypto[0] if ordre_crypto else (ordre[0] if ordre else "demo"),
        "providers_disponibles": ordre_crypto or ordre,
        "checks": checks,
        "teste_le": _now_iso(),
        "aucune_source_reelle": aucune_source,
        "symboles_populaires": POPULAR_SYMBOLS,
        "periodes": PERIODS,
        "intervalles": INTERVALS,
    }


def _refresh_status_en_arriere_plan(provider: str, probe_timeout: float) -> None:
    """Lance le test des fournisseurs dans un fil : l'appel HTTP ne bloque jamais."""
    global _STATUS_PROBE_EN_COURS
    with _STATUS_LOCK:
        if _STATUS_PROBE_EN_COURS:
            return
        _STATUS_PROBE_EN_COURS = True

    def _travail() -> None:
        global _STATUS_PROBE_EN_COURS
        try:
            checks = _probe_providers(provider, probe_timeout)
            _STATUS_CACHE.update(
                {"time": time.time(), "provider": provider, "data": _status_from_checks(provider, checks)}
            )
        except Exception:  # pragma: no cover - réseau
            pass
        finally:
            with _STATUS_LOCK:
                _STATUS_PROBE_EN_COURS = False

    threading.Thread(target=_travail, name="diagnostic-marche", daemon=True).start()


def _status_en_attente(provider: str, ancien: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Diagnostic rapide (aucun réseau) : dernier résultat connu ou « en cours de test »."""
    if ancien is not None:
        return {**ancien, "provider_configure": provider, "test_en_cours": True}
    return {
        "provider_configure": provider,
        "provider_actif": "inconnu",
        "provider_actif_crypto": "inconnu",
        "providers_disponibles": [],
        "checks": [],
        "test_en_cours": True,
        "teste_le": "",
        "aucune_source_reelle": False,
        "symboles_populaires": POPULAR_SYMBOLS,
        "periodes": PERIODS,
        "intervalles": INTERVALS,
    }


def market_status(
    settings: Settings | None = None, *, probe: bool | None = None
) -> dict[str, Any]:
    """Diagnostic : quels fournisseurs répondent réellement ?

    ``probe`` contrôle le comportement réseau (les sondes prennent plusieurs
    secondes : elles ne doivent jamais bloquer l'affichage d'une page) :

    * ``None`` (défaut) : renvoie le dernier diagnostic connu et, s'il est périmé,
      relance un test **en arrière-plan** ;
    * ``True`` : test bloquant (utilisé par ``/api/health?refresh=1``) ;
    * ``False`` : aucun test, uniquement le cache (rendu de la page d'accueil).
    """
    settings = settings or get_settings()
    provider = (settings.market_provider or "auto").strip().lower()
    now = time.time()
    cache_valide = (
        _STATUS_CACHE["data"] is not None
        and _STATUS_CACHE["provider"] == provider
        and now - _STATUS_CACHE["time"] < _STATUS_TTL
    )
    if probe is not True and cache_valide:
        return {**_STATUS_CACHE["data"], "test_en_cours": False}

    if probe is False:
        return _status_en_attente(provider, _STATUS_CACHE["data"] if _STATUS_CACHE["provider"] == provider else None)

    probe_timeout = min(8.0, max(3.0, settings.market_timeout_s / 2))
    if probe is True:
        checks = _probe_providers(provider, probe_timeout)
        resultat = _status_from_checks(provider, checks)
        resultat["test_en_cours"] = False
        _STATUS_CACHE.update({"time": time.time(), "provider": provider, "data": resultat})
        return dict(resultat)

    # probe is None : réponse immédiate + test en arrière-plan
    _refresh_status_en_arriere_plan(provider, probe_timeout)
    return _status_en_attente(
        provider, _STATUS_CACHE["data"] if _STATUS_CACHE["provider"] == provider else None
    )
