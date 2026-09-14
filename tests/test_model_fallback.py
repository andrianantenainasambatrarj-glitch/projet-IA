"""Tests de la robustesse aux changements de modèles chez les fournisseurs IA.

Cas réel rencontré : Google a retiré ``gemini-2.5-flash`` pour les nouveaux comptes
(erreur 404 « This model ... is no longer available to new users. Please update your
code to use models/gemini-3.6-flash »). L'application doit alors découvrir les
modèles réellement disponibles et basculer automatiquement.
"""

from __future__ import annotations

import json

import pytest

from app.llm import (
    GEMINI_MODEL_PREFERENCES,
    GeminiLLM,
    LLMError,
    _is_model_unavailable,
    clear_llm_error,
    last_llm_error,
    record_llm_error,
)


class _Reponse:
    def __init__(self, status_code: int, payload: dict | str) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return self._payload if isinstance(self._payload, dict) else {}


class _ClientGemini:
    """Client httpx factice simulant l'API Google avec des modèles retirés."""

    def __init__(self, modeles_disponibles: list[str], refus: tuple[str, ...] = ()) -> None:
        self.modeles_disponibles = list(modeles_disponibles)
        self.refus = set(refus)
        self.appels: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        return _Reponse(
            200,
            {
                "models": [
                    {
                        "name": f"models/{nom}",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                    for nom in self.modeles_disponibles
                ]
                + [
                    {
                        "name": "models/gemini-embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    }
                ]
            },
        )

    def post(self, url, **kwargs):
        modele = url.split("/models/")[1].split(":")[0]
        self.appels.append(modele)
        if modele in self.refus:
            return _Reponse(
                404,
                {
                    "error": {
                        "code": 404,
                        "status": "NOT_FOUND",
                        "message": (
                            f"This model models/{modele} is no longer available to new users. "
                            "Please update your code to use models/gemini-3.6-flash."
                        ),
                    }
                },
            )
        return _Reponse(
            200,
            {
                "candidates": [{"content": {"parts": [{"text": f"réponse de {modele}"}]}}],
                "usageMetadata": {"totalTokenCount": 12},
            },
        )


def _installer(monkeypatch, client: _ClientGemini) -> None:
    from app import llm as module_llm

    monkeypatch.setattr(module_llm.httpx, "Client", lambda *a, **k: client)
    module_llm._MODELS_CACHE.clear()


def test_detection_des_modeles_disponibles(monkeypatch):
    client = _ClientGemini(["gemini-3.6-flash", "gemini-3.5-flash"])
    _installer(monkeypatch, client)

    gemini = GeminiLLM("AQ.cleDeTest")
    assert gemini.list_models() == ["gemini-3.6-flash", "gemini-3.5-flash"]
    # Les modèles d'embedding ne sont pas proposés pour la génération
    assert "gemini-embedding-001" not in gemini.list_models()


def test_repli_automatique_quand_un_modele_est_retire(monkeypatch):
    """Le modèle configuré (2.5-flash) est refusé → bascule sur le modèle disponible."""
    client = _ClientGemini(["gemini-3.6-flash"], refus=("gemini-2.5-flash",))
    _installer(monkeypatch, client)

    gemini = GeminiLLM("AQ.cleDeTest", vision_model="gemini-2.5-flash", text_model="gemini-2.5-flash")
    reponse = gemini.generate(system="s", messages=[{"role": "user", "content": "x"}], use_vision=False)

    assert reponse.model == "gemini-3.6-flash"
    assert "gemini-2.5-flash" in client.appels[0]
    assert client.appels[-1] == "gemini-3.6-flash"
    assert gemini.label == "gemini:gemini-3.6-flash"


def test_le_modele_resolu_est_reutilise_ensuite(monkeypatch):
    """Après la première bascule, on ne redemande plus le modèle retiré."""
    client = _ClientGemini(["gemini-3.6-flash"], refus=("gemini-2.5-flash",))
    _installer(monkeypatch, client)

    gemini = GeminiLLM("AQ.cle2", vision_model="gemini-2.5-flash")
    gemini.generate(system="s", messages=[{"role": "user", "content": "1"}], use_vision=False)
    premier_appel = list(client.appels)
    client.appels.clear()

    gemini.generate(system="s", messages=[{"role": "user", "content": "2"}], use_vision=False)
    assert client.appels == ["gemini-3.6-flash"], "aucun essai inutile au 2ᵉ appel"
    assert len(premier_appel) >= 1


def test_erreur_claire_si_aucun_modele_ne_repond(monkeypatch):
    from app.llm import DEFAULT_MODELS  # noqa: F401  (documente la dépendance)

    client = _ClientGemini(["gemini-3.6-flash"], refus=GEMINI_MODEL_PREFERENCES)
    _installer(monkeypatch, client)

    gemini = GeminiLLM("AQ.cle3", vision_model="gemini-2.5-flash")
    with pytest.raises(LLMError) as erreur:
        gemini.generate(system="s", messages=[{"role": "user", "content": "x"}], use_vision=False)

    message = str(erreur.value)
    assert "Modèles essayés" in message
    assert "VISION_MODEL" in message


def test_quota_non_confondu_avec_un_modele_retire(monkeypatch):
    """Une erreur de quota (429) ne doit pas déclencher de bascule de modèle."""

    class _ClientQuota(_ClientGemini):
        def post(self, url, **kwargs):
            return _Reponse(429, {"error": {"message": "Quota exceeded"}})

    _installer(monkeypatch, _ClientQuota(["gemini-3.6-flash"]))
    gemini = GeminiLLM("AQ.cle4")

    with pytest.raises(LLMError) as erreur:
        gemini.generate(system="s", messages=[{"role": "user", "content": "x"}], use_vision=False)
    assert "429" in str(erreur.value)


def test_preferences_contiennent_le_modele_recommande_par_google():
    assert GEMINI_MODEL_PREFERENCES[0] == "gemini-3.6-flash"
    assert "gemini-2.5-flash" in GEMINI_MODEL_PREFERENCES  # anciens comptes toujours servis


def test_detection_des_messages_de_modele_retire():
    assert _is_model_unavailable(
        "Gemini (404) : This model models/gemini-2.5-flash is no longer available to new users"
    )
    assert _is_model_unavailable("models/foo is not found")
    assert not _is_model_unavailable("Quota gratuit atteint")
    assert not _is_model_unavailable("Clé API invalide (401)")


def test_suivi_des_erreurs_pour_l_interface():
    clear_llm_error("gemini")
    assert last_llm_error("gemini") == {}

    record_llm_error("gemini", "Gemini (404) : no longer available")
    erreur = last_llm_error("gemini")
    assert "404" in erreur["message"]
    assert erreur["horodatage"]

    clear_llm_error("gemini")
    assert last_llm_error("gemini") == {}


def test_health_reflete_l_erreur_et_ne_promet_pas_la_vision(client, monkeypatch):
    """L'interface ne doit plus afficher « Vision active » quand les appels échouent."""
    from app.config import get_settings
    from app.llm import clear_llm_cache, clear_llm_error, record_llm_error

    settings = get_settings()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    settings.llm_provider = "gemini"
    settings.gemini_api_key = "AQ.cleDeTest"
    clear_llm_cache()

    clear_llm_error("gemini")
    llm = client.get("/api/health").json()["llm"]
    assert llm["provider_actif"] == "gemini"
    assert llm["vision_disponible"] is True

    record_llm_error("gemini", "Gemini (404) : model no longer available")
    llm = client.get("/api/health").json()["llm"]
    assert llm["vision_disponible"] is False
    assert "404" in llm["derniere_erreur"]["message"]

    page = client.get("/").text
    assert "Le fournisseur IA a renvoyé une erreur" in page

    clear_llm_error("gemini")
    settings.gemini_api_key = ""
    settings.llm_provider = "demo"
    clear_llm_cache()
