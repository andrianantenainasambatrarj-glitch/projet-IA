"""Tests des services d'orchestration (analyse complète, chat, mode démo)."""

from __future__ import annotations

import base64

from app.services.analysis_service import AnalysisRequest, quick_diagnostic, run_analysis
from app.services.chat_service import ChatRequest, run_chat

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def test_analyse_complete_en_mode_demo(indexed_settings):
    reponse = run_analysis(
        AnalysisRequest(
            symbol="AAPL",
            period="6mo",
            interval="1d",
            question="Puis-je acheter et où placer mon stop ?",
        ),
        settings=indexed_settings,
    )

    assert reponse.mode == "demo"
    assert reponse.analysis is not None
    assert reponse.sources, "le RAG doit remonter des extraits de cours"
    assert "### 1. Lecture du graphique" in reponse.answer
    assert "### 3. Prédiction probabiliste" in reponse.answer
    assert "### 4. Plan de trading" in reponse.answer
    assert "AVERTISSEMENT" in reponse.answer
    assert reponse.report_id


def test_analyse_avec_image_sans_llm_signale_l_absence_de_vision(indexed_settings):
    reponse = run_analysis(
        AnalysisRequest(image=PNG_1PX, symbol="AAPL"),
        settings=indexed_settings,
    )
    assert reponse.observation is not None
    assert reponse.observation["available"] is False
    assert any("vision" in avertissement.lower() for avertissement in reponse.warnings)
    assert reponse.answer  # une réponse exploitable est tout de même produite


def test_analyse_refuse_une_image_invalide(indexed_settings):
    try:
        run_analysis(AnalysisRequest(image=b"pas une image"), settings=indexed_settings)
    except ValueError as exc:
        assert "image" in str(exc).lower()
    else:
        raise AssertionError("une erreur était attendue pour une image invalide")


def test_analyse_sans_donnees_ni_image_previent_l_utilisateur(indexed_settings):
    reponse = run_analysis(AnalysisRequest(), settings=indexed_settings)
    assert reponse.analysis is None
    assert reponse.warnings


def test_chat_documentaire_en_mode_demo(indexed_settings):
    reponse = run_chat(
        ChatRequest(question="Comment placer mon stop loss ?", top_k=3),
        settings=indexed_settings,
    )
    assert reponse.mode == "demo"
    assert reponse.sources
    assert "Source 1" in reponse.answer
    assert "conseil en investissement" in reponse.answer


def test_chat_avec_base_vide(settings):
    reponse = run_chat(ChatRequest(question="Qu'est-ce que l'ADX ?"), settings=settings)
    assert "Aucun document" in reponse.answer or "Aucun extrait" in reponse.answer


def test_diagnostic_complet(indexed_settings):
    diagnostic = quick_diagnostic(indexed_settings)
    assert diagnostic["llm"]["mode_demo"] is True
    assert diagnostic["marche"]["provider_actif"] in {"demo", "yahoo", "stooq", "yfinance"}
    assert diagnostic["connaissances"]["chunks"] > 0


def test_bout_en_bout_avec_modele_gemini_retire(indexed_settings, monkeypatch):
    """Scénario réel vécu : la clé est bonne, mais le modèle demandé n'existe plus.

    L'analyse doit basculer sur le modèle disponible, produire une réponse IA
    (et non le repli local), et le lien vision doit fonctionner avec l'image.
    """
    import base64 as b64

    from app import llm as module_llm
    from app.llm import GeminiLLM, clear_llm_cache

    png = b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )

    appels: list[str] = []

    class _Reponse:
        def __init__(self, code, payload):
            import json as jsonlib

            self.status_code = code
            self._payload = payload
            self.text = payload if isinstance(payload, str) else jsonlib.dumps(payload)

        def json(self):
            return self._payload if isinstance(self._payload, dict) else {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kwargs):
            return _Reponse(200, {"models": [
                {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
            ]})

        def post(self, url, **kwargs):
            modele = url.split("/models/")[1].split(":")[0]
            appels.append(modele)
            if modele != "gemini-3.6-flash":
                return _Reponse(404, {"error": {"message": (
                    f"This model models/{modele} is no longer available to new users."
                )}})
            contenu = "### 1. Lecture du graphique\nAnalyse produite par le modèle de secours."
            return _Reponse(200, {"candidates": [{"content": {"parts": [{"text": contenu}]}}]})

    monkeypatch.setattr(module_llm.httpx, "Client", _Client)
    module_llm._MODELS_CACHE.clear()

    settings = indexed_settings
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    settings.llm_provider = "gemini"
    settings.gemini_api_key = "AQ.cleDeTestBoutEnBout"
    settings.vision_model = "gemini-2.5-flash"  # modèle retiré par Google
    clear_llm_cache()

    reponse = run_analysis(AnalysisRequest(symbol="AAPL", image=png), settings=settings)

    assert reponse.mode == "ia", reponse.warnings
    assert reponse.model == "gemini-3.6-flash"
    assert "Analyse produite par le modèle de secours" in reponse.answer
    assert "gemini-2.5-flash" in appels and "gemini-3.6-flash" in appels
    assert reponse.observation["available"] is True

    settings.gemini_api_key = ""
    settings.vision_model = ""
    clear_llm_cache()
