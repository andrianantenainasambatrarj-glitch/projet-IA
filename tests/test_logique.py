"""Tests de non-régression sur les erreurs de logique corrigées.

Chaque test décrit ici correspond à un défaut réellement présent dans
l'application (et non à une fonctionnalité nouvelle) :

* un RSI neutre (50) était compté comme « survente » avec une pénalité de -3 ;
* les deux scénarios pouvaient totaliser 130 % de probabilité ;
* le diagnostic de marché sondait le réseau à chaque affichage de page ;
* le formulaire de veille recevait « AAPL BTC-USD ^FCHI » (un seul symbole invalide) ;
* l'import d'un fichier non-image renvoyait une erreur interne 500 ;
* la série de démonstration ignorait la période demandée.
"""

from __future__ import annotations

import time

import pytest


# ------------------------------------------------------------------ RSI & score

def test_rsi_neutre_ne_est_pas_une_survente():
    from app.analysis import compute_score

    snapshot = {"rsi14": 50.0, "macd_histogram": 0.0, "volume_ratio": 1.0, "bollinger_middle": None}
    score, _confiance, notes = compute_score({"score": 0.0}, snapshot, [], {"range_position_pct": 50.0}, 100.0)

    ligne_rsi = [note for note in notes if note.startswith("RSI")]
    assert ligne_rsi == ["RSI 50 neutre (pas de signal de momentum)."]
    assert "survente" not in ligne_rsi[0]
    assert score == 0.0, "un marché parfaitement neutre doit donner un score nul"
    assert any("MACD neutre" in note for note in notes), "un MACD plat n'est pas baissier"


def test_zones_rsi_coherentes():
    from app.analysis import rsi_contribution

    assert rsi_contribution(75.0)[0] == 3.0  # surachat : prudence, pas de signal fort
    assert "surachat" in rsi_contribution(75.0)[1]
    assert rsi_contribution(60.0)[0] == 8.0
    assert rsi_contribution(40.0)[0] == -8.0
    assert rsi_contribution(20.0)[0] == -3.0
    assert "survente" in rsi_contribution(20.0)[1]
    assert rsi_contribution(50.0)[0] == 0.0
    assert rsi_contribution(46.0)[0] == 0.0


def test_probabilites_des_scenarios_complementaires(settings):
    from app.analysis import build_levels, build_scenarios, indicator_snapshot
    from app.market import demo_instrument

    instrument = demo_instrument("AAPL", "6mo", "1d")
    niveaux = build_levels(instrument)
    snapshot = indicator_snapshot(instrument)

    for score in (-80.0, -30.0, 0.0, 30.0, 80.0):
        scenarios = build_scenarios(instrument, niveaux, snapshot, score)
        haussier, baissier = scenarios[0]["probability_hint"], scenarios[1]["probability_hint"]
        assert haussier + baissier == pytest.approx(100.0), "les deux scénarios totalisent 100 %"
        assert 15.0 <= haussier <= 85.0, "jamais de probabilité de certitude"
        if score > 0:
            assert haussier > baissier
        elif score < 0:
            assert haussier < baissier
        else:
            assert haussier == baissier == 50.0


# --------------------------------------------------------- diagnostic du marché

def test_diagnostic_marche_ne_bloque_pas_la_page(settings, monkeypatch):
    """Aucun appel réseau ne doit être déclenché par le rendu d'une page."""
    from app import market as module_marche

    module_marche.reset_status_cache()

    def _reseau_interdit(*args, **kwargs):  # pragma: no cover - ne doit jamais être appelé
        raise AssertionError("aucun appel réseau ne doit être fait pendant le rendu")

    monkeypatch.setattr(module_marche, "_probe_providers", _reseau_interdit)
    statut = module_marche.market_status(settings, probe=False)

    assert statut["provider_actif"] == "inconnu"
    assert statut["test_en_cours"] is True


def test_diagnostic_marche_en_arriere_plan(settings, monkeypatch):
    """Le mode par défaut répond immédiatement et lance le test en tâche de fond."""
    from app import market as module_marche

    appels: list[float] = []

    def _faux_probe(provider, timeout):
        appels.append(timeout)
        return [
            {"provider": "yahoo", "status": "indisponible"},
            {"provider": "binance", "status": "disponible", "candles": 30, "dernier": "2026-01-01"},
            {"provider": "demo", "status": "toujours disponible (hors-ligne)"},
        ]

    monkeypatch.setattr(module_marche, "_probe_providers", _faux_probe)
    module_marche.reset_status_cache()

    debut = time.time()
    statut = module_marche.market_status(settings)
    assert time.time() - debut < 1.0, "la réponse doit être immédiate"

    for _ in range(100):  # le fil d'arrière-plan publie son résultat
        if module_marche._STATUS_CACHE["data"] is not None:
            break
        time.sleep(0.05)

    assert appels, "le test réseau doit avoir été lancé en arrière-plan"
    publie = module_marche.market_status(settings, probe=False)
    assert publie["provider_actif"] == "binance", "pour la crypto, Binance passe en premier"
    assert publie["provider_actif_crypto"] == "binance"

    module_marche.reset_status_cache()  # ne pas polluer les tests suivants


def test_provider_status_ne_fait_aucun_appel_reseau(settings, monkeypatch):
    """`/api/health` et la page d'accueil ne doivent jamais attendre Google."""
    from app import llm as module_llm

    class _ClientInterdit:  # pragma: no cover - ne doit jamais être instancié
        def __init__(self, *args, **kwargs):
            raise AssertionError("provider_status() ne doit pas appeler le réseau")

    monkeypatch.setattr(module_llm.httpx, "Client", _ClientInterdit)
    settings.gemini_api_key = "AQ.cleDeTest"
    settings.llm_provider = "gemini"
    module_llm.clear_llm_cache()
    module_llm._MODELS_CACHE.clear()

    statut = module_llm.provider_status(settings)
    assert statut["provider_actif"] == "gemini"
    assert statut["mode_demo"] is False
    assert statut["vision_disponible"] is True
    # Aucun modèle n'est encore connu (découverte faite au premier appel réel)
    assert statut["modele_vision"] in {"", "auto"}

    settings.gemini_api_key = ""
    settings.llm_provider = "demo"
    module_llm.clear_llm_cache()


def test_echec_de_decouverte_des_modeles_est_mis_en_cache(settings, monkeypatch):
    """Une découverte en échec ne doit pas être relancée à chaque appel."""
    from app import llm as module_llm

    compteur = {"appels": 0}

    class _ReponseErreur:
        status_code = 500
        text = "erreur"

        def json(self):
            return {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            compteur["appels"] += 1
            return _ReponseErreur()

    monkeypatch.setattr(module_llm.httpx, "Client", _Client)
    module_llm._MODELS_CACHE.clear()
    gemini = module_llm.GeminiLLM("AQ.cleDeTest")

    assert gemini.list_models() == []
    assert gemini.list_models() == []
    assert compteur["appels"] == 1, "le second appel doit utiliser le cache d'échec"

    module_llm._MODELS_CACHE.clear()


# --------------------------------------------------------- données de marché

def test_serie_de_demonstration_respecte_la_periode(settings):
    from app.market import demo_instrument

    court = demo_instrument("AAPL", "1mo", "1d")
    moyen = demo_instrument("AAPL", "6mo", "1d")
    long = demo_instrument("AAPL", "2y", "1d")

    assert len(court.candles) < len(moyen.candles) < len(long.candles)
    assert len(long.candles) >= 700
    # Espacement réel des bougies
    assert (moyen.candles[-1].t - moyen.candles[-2].t) == 86400

    intraday = demo_instrument("AAPL", "5d", "15m")
    assert (intraday.candles[-1].t - intraday.candles[-2].t) == 900
    assert len(intraday.candles) > 300


def test_periode_intraday_ajustee_est_signalee(settings):
    from app.market import get_instrument

    instrument = get_instrument("AAPL", "1y", "5m", settings=settings, force_refresh=True)
    assert instrument.timeframe == "5m"
    assert any("intraday" in note.lower() for note in instrument.notes), instrument.notes


def test_formulaire_de_veille_transmet_des_symboles_valides(client):
    """La valeur injectée dans le formulaire doit redevenir la liste d'origine."""
    from app.config import get_settings

    page = client.get("/").text
    attendu = ", ".join(get_settings().watchlist_symbols)
    assert f'value="{attendu}"' in page

    # …et cette valeur doit bien se décomposer en plusieurs symboles côté serveur
    settings = get_settings()
    settings.watchlist = attendu
    assert settings.watchlist_symbols == ["AAPL", "BTC-USD", "^FCHI"]


# --------------------------------------------------------------- API robuste

def test_import_non_image_renvoie_400(client):
    """Un fichier non-image doit donner une erreur claire, pas une erreur 500."""
    reponse = client.post(
        "/api/analyze/upload",
        files={"file": ("notes.txt", b"ceci n'est pas une image", "text/plain")},
    )
    assert reponse.status_code == 400
    assert "image" in reponse.json()["detail"].lower()


def test_import_image_vide_renvoie_400(client):
    reponse = client.post("/api/analyze/upload", files={"file": ("vide.png", b"", "image/png")})
    assert reponse.status_code == 400


def test_image_json_invalide_renvoie_400(client):
    reponse = client.post("/api/analyze", json={"symbol": "", "image_base64": "data:image/png;base64,AAAA"})
    assert reponse.status_code == 400
    assert "image" in reponse.json()["detail"].lower() or "format" in reponse.json()["detail"].lower()


def test_health_repond_vite(client):
    debut = time.time()
    reponse = client.get("/api/health")
    assert reponse.status_code == 200
    assert time.time() - debut < 3.0, "le diagnostic ne doit pas attendre les fournisseurs"
    marche = reponse.json()["marche"]
    assert "provider_actif" in marche
    assert "test_en_cours" in marche


def test_scan_watchlist_active_reellement_la_veille(client):
    from app.config import get_settings

    settings = get_settings()
    settings.notify_enabled = False
    reponse = client.post(
        "/api/notifications/watchlist",
        json={"watchlist": "AAPL", "interval_minutes": 30, "min_score": 0, "start": True},
    )
    assert reponse.status_code == 200
    corps = reponse.json()
    assert "demarrage" in corps, "la réponse doit indiquer si la veille a démarré"
    assert settings.notify_enabled is True, "start=true doit activer NOTIFY_ENABLED"

    client.post("/api/notifications/veille/stop")
    settings.notify_enabled = False
