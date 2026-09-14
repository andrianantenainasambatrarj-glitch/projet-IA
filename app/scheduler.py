"""Veille automatique : analyse périodique de la watchlist et envoi des alertes.

Activée uniquement si ``NOTIFY_ENABLED=true`` **et** qu'un canal (Telegram ou webhook)
est configuré : aucun fil d'arrière-plan inutile n'est lancé sinon.

Le fil est un simple ``threading.Thread`` (aucune dépendance type APScheduler) et
il est totalement silencieux en cas d'échec réseau : l'application ne tombe jamais
à cause d'une notification non délivrée.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from .config import Settings, get_settings
from .notifications import get_notifier, run_watchlist_cycle

logger = logging.getLogger("tradevision.scheduler")

#: État exposé par /api/health (dernier passage, prochain passage, erreurs…).
SCHEDULER_STATUS: dict[str, Any] = {
    "actif": False,
    "demarre_le": "",
    "dernier_passage": "",
    "prochain_passage": "",
    "passages": 0,
    "dernier_resultat": {},
    "derniere_erreur": "",
}

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loop(interval_minutes: int, base_url: str) -> None:  # pragma: no cover - fil d'arrière-plan
    """Boucle de veille : un passage immédiat, puis toutes les N minutes."""
    # Petite attente au démarrage : l'indexation initiale et la première visite passent avant.
    if _stop_event.wait(45):
        return
    while not _stop_event.is_set():
        try:
            resultat = run_watchlist_cycle(send=True, base_url=base_url)
            with _lock:
                SCHEDULER_STATUS.update(
                    {
                        "dernier_passage": _now_iso(),
                        "passages": int(SCHEDULER_STATUS.get("passages", 0)) + 1,
                        "dernier_resultat": {
                            "symboles_analyses": resultat.get("symboles_analyses", 0),
                            "symboles_retenus": resultat.get("symboles_retenus", 0),
                            "envoye": resultat.get("envoye", False),
                            "raison": resultat.get("raison", ""),
                        },
                        "derniere_erreur": "",
                    }
                )
            logger.info(
                "Veille : %s marchés analysés, %s retenus, envoi=%s",
                resultat.get("symboles_analyses"),
                resultat.get("symboles_retenus"),
                resultat.get("envoye"),
            )
        except Exception as exc:  # pragma: no cover
            with _lock:
                SCHEDULER_STATUS["derniere_erreur"] = str(exc)[:300]
            logger.warning("Veille en échec : %s", exc)

        with _lock:
            SCHEDULER_STATUS["prochain_passage"] = (
                datetime.now(timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M %Z")
            )
        # Attente fractionnée pour réagir vite à l'arrêt du serveur
        if _stop_event.wait(max(60, interval_minutes * 60)):
            return


def start_scheduler(settings: Optional[Settings] = None, *, base_url: str = "") -> bool:
    """Démarre la veille si elle est activée. Renvoie True si un fil a été lancé."""
    global _thread
    settings = settings or get_settings()
    notifier = get_notifier(settings)

    if not settings.notify_enabled:
        SCHEDULER_STATUS.update({"actif": False, "raison": "NOTIFY_ENABLED=false"})
        return False
    if not notifier.ready:
        SCHEDULER_STATUS.update(
            {"actif": False, "raison": "aucun canal de notification configuré"}
        )
        logger.info(
            "Veille demandée mais aucun canal configuré (TELEGRAM_BOT_TOKEN / "
            "NOTIFY_WEBHOOK_URL) : elle reste inactive."
        )
        return False

    interval = max(5, int(settings.notify_interval_minutes))
    with _lock:
        if _thread is not None and _thread.is_alive():
            # Le fil tourne déjà : si l'intervalle a changé (formulaire « Alertes &
            # veille »), on le redémarre pour appliquer la nouvelle cadence — sinon
            # les réglages affichés ne correspondaient pas au comportement réel.
            if int(SCHEDULER_STATUS.get("intervalle_minutes", interval)) == interval:
                SCHEDULER_STATUS.update(
                    {
                        "actif": True,
                        "canaux": notifier.label,
                        "watchlist": settings.watchlist_symbols,
                        "raison": "",
                    }
                )
                return True
            _stop_event.set()
            ancien = _thread
        else:
            ancien = None
        _stop_event.clear()
        SCHEDULER_STATUS.update(
            {
                "actif": True,
                "demarre_le": _now_iso(),
                "intervalle_minutes": interval,
                "canaux": notifier.label,
                "watchlist": settings.watchlist_symbols,
                "raison": "",
            }
        )
        _thread = threading.Thread(
            target=_loop, args=(interval, base_url), name="veille-marche", daemon=True
        )
        _thread.start()
    if ancien is not None and ancien.is_alive():
        ancien.join(timeout=2.0)
    logger.info(
        "Veille automatique démarrée : %s marché(s), toutes les %s min, envoi via %s",
        len(settings.watchlist_symbols),
        interval,
        notifier.label,
    )
    return True


def stop_scheduler() -> None:
    """Arrête proprement la veille (appelé à l'arrêt du serveur)."""
    _stop_event.set()
    with _lock:
        SCHEDULER_STATUS["actif"] = False
    logger.info("Veille automatique arrêtée.")


def scheduler_status() -> dict[str, Any]:
    """État courant de la veille (pour /api/health)."""
    with _lock:
        return dict(SCHEDULER_STATUS)
