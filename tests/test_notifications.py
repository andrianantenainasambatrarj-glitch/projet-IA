"""Tests des notifications (Telegram / webhook) et de la veille automatique.

Aucun appel réseau externe : un petit serveur HTTP local joue le rôle du webhook.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.notifications import (
    Notifier,
    analyze_and_notify,
    format_analysis_message,
    format_digest_message,
    notify_status,
    run_watchlist_cycle,
)


class _Collecteur(BaseHTTPRequestHandler):
    """Serveur de test : enregistre les corps JSON reçus."""

    recus: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 (API http.server)
        length = int(self.headers.get("Content-Length", "0"))
        brut = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            _Collecteur.recus.append(json.loads(brut))
        except json.JSONDecodeError:
            _Collecteur.recus.append({"brut": brut})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args) -> None:  # silence
        return


@pytest.fixture()
def webhook():
    """Démarre un serveur local et renvoie son URL (nettoyé après le test)."""
    _Collecteur.recus = []
    serveur = HTTPServer(("127.0.0.1", 0), _Collecteur)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{serveur.server_port}/hook"
    yield url
    serveur.shutdown()
    serveur.server_close()


def test_status_sans_canal(settings):
    etat = notify_status(settings)
    assert etat["pret"] is False
    assert etat["canaux"] == "aucun canal configuré"
    assert "TELEGRAM_BOT_TOKEN" in etat["aide"]


def test_envoi_webhook_reel(settings, webhook):
    settings.notify_webhook_url = webhook
    notifier = Notifier(settings)
    assert notifier.ready and notifier.webhook_ready

    resultat = notifier.send_test()
    assert resultat["succes"] is True
    assert resultat["resultats"][0]["statut"] == "envoye"

    assert _Collecteur.recus, "le webhook aurait dû recevoir un message"
    corps = _Collecteur.recus[0]
    assert "text" in corps and "TradeVision" in corps["text"]


def test_envoi_analyse_complete(settings, webhook):
    settings.notify_webhook_url = webhook
    resultat = analyze_and_notify("AAPL", settings=settings, send=True)

    assert resultat["envoye"] is True
    assert resultat["symbol"] == "AAPL"
    assert resultat["report_id"]

    message = _Collecteur.recus[0]["text"]
    for attendu in ("Analyse", "AAPL", "Plan de trading", "Stop", "RSI"):
        assert attendu in message, f"« {attendu} » absent du message envoyé"
    assert "conseil" in message.lower()  # avertissement présent


def test_analyse_sans_envoi(settings):
    resultat = analyze_and_notify("AAPL", settings=settings, send=False)
    assert resultat["envoye"] is False
    assert resultat["analysis"] is not None


def test_scan_watchlist_et_seuil(settings, webhook):
    settings.notify_webhook_url = webhook
    settings.watchlist = "AAPL,BTC-USD"

    # Seuil très élevé : aucun marché ne doit être retenu -> aucun envoi
    settings.notify_min_score = 99.0
    resultat = run_watchlist_cycle(settings=settings, send=True)
    assert resultat["symboles_analyses"] == 2
    assert resultat["symboles_retenus"] == 0
    assert resultat["envoye"] is False
    assert "seuil" in resultat.get("raison", "").lower()
    assert _Collecteur.recus == []

    # Seuil bas : les deux marchés sont retenus -> un résumé est envoyé
    settings.notify_min_score = -100.0
    resultat = run_watchlist_cycle(settings=settings, send=True)
    assert resultat["symboles_retenus"] == 2
    assert resultat["envoye"] is True
    assert len(_Collecteur.recus) == 1
    assert "Veille de marché" in _Collecteur.recus[0]["text"]


def test_rendu_des_messages():
    charge = {
        "analysis": {
            "instrument": {"symbol": "BTC-USD", "currency": "USD", "timeframe": "1d", "source": "yahoo"},
            "price": 61234.5,
            "label": "haussier",
            "score": 27.5,
            "confidence": 0.62,
            "setup": {"direction": "achat", "entry": 61234.5, "stop": 59800.0, "target1": 63000.0,
                      "target2": 65000.0, "risk_reward": 1.8},
            "indicators": {"rsi14": 58.2, "atr_pct": 2.4, "macd_histogram": 120.0},
            "levels": {"supports": [{"price": 60000.0}], "resistances": [{"price": 63000.0}]},
            "patterns": [{"name": "Double creux (W)", "bias": "haussier", "confidence": 0.65}],
            "scenarios": [{"name": "Scénario haussier", "trigger": "Clôture > 63000"}],
        },
        "sources": [{"title": "02 figures chartistes", "section": "2.3 Figures de retournement"}],
        "warnings": ["Données de démonstration."],
    }
    texte = format_analysis_message(charge, title="Analyse BTC-USD", base_url="https://exemple.app")
    assert "BTC-USD" in texte
    assert "61234.500000" in texte
    assert "Double creux" in texte
    assert "02 figures chartistes" in texte
    assert "https://exemple.app" in texte
    assert len(texte) < 4096  # limite Telegram

    resume = format_digest_message([charge], base_url="https://exemple.app")
    assert "Veille de marché" in resume
    assert "BTC-USD" in resume


def test_statut_avec_telegram_complet(settings):
    settings.telegram_bot_token = "123456:ABC"
    settings.telegram_chat_id = "987654"
    etat = notify_status(settings)
    assert etat["pret"] is True
    assert etat["telegram_configure"] is True
    assert "Telegram" in etat["canaux"]


def test_veille_demarre_seulement_si_canal_configure(settings, webhook):
    from app.scheduler import scheduler_status, start_scheduler, stop_scheduler

    # 1) activée mais aucun canal -> refus explicite
    settings.notify_enabled = True
    assert start_scheduler(settings) is False
    assert scheduler_status()["actif"] is False
    assert "canal" in scheduler_status()["raison"]

    # 2) désactivée -> refus
    settings.notify_enabled = False
    settings.notify_webhook_url = webhook
    assert start_scheduler(settings) is False
    assert "NOTIFY_ENABLED" in scheduler_status()["raison"]

    # 3) activée avec un canal -> fil démarré
    settings.notify_enabled = True
    try:
        assert start_scheduler(settings) is True
        etat = scheduler_status()
        assert etat["actif"] is True
        assert etat["intervalle_minutes"] >= 5
        assert etat["watchlist"]
    finally:
        stop_scheduler()
    assert scheduler_status()["actif"] is False


def test_api_notifications(client, webhook, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    settings.notify_webhook_url = webhook

    etat = client.get("/api/notifications").json()
    assert etat["notifications"]["pret"] is True

    assert client.post("/api/notifications/test").json()["succes"] is True

    envoi = client.post("/api/notifications/analysis", json={"symbol": "AAPL", "interval": "1d"}).json()
    assert envoi["envoye"] is True

    scan = client.post(
        "/api/notifications/watchlist",
        json={"watchlist": "AAPL,BTC-USD", "interval_minutes": 60, "min_score": -100, "start": False},
    ).json()
    assert scan["symboles_analyses"] == 2
    assert scan["envoye"] is True

    # envoi d'une analyse de l'historique
    report_id = client.get("/api/reports?limit=1").json()["analyses"][0]["report_id"]
    assert client.post("/api/notifications/send", json={"report_id": report_id}).json()["succes"] is True
    assert client.post("/api/notifications/send", json={}).status_code == 400

    # endpoints de veille
    demarrage = client.post(
        "/api/notifications/veille/start",
        json={"watchlist": "AAPL", "interval_minutes": 15, "min_score": 0, "start": True},
    ).json()
    assert demarrage["demarree"] is True
    assert client.post("/api/notifications/veille/stop").json()["veille"]["actif"] is False

    # la santé expose l'état des notifications et de la veille
    sante = client.get("/api/health").json()
    assert "notifications" in sante and "veille" in sante
