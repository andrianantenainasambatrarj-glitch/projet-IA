"""Tests de la couche vision (décodage d'image, description structurée)."""

from __future__ import annotations

import base64
import json

import pytest

from app.llm import LLMResponse
from app.vision import decode_image_payload, parse_json_block, read_chart, validate_image

# Un PNG 1×1 pixel valide (le plus petit possible)
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


class FauxLLM:
    """LLM factice renvoyant une description JSON prédéfinie."""

    provider = "faux"
    supports_vision = True
    vision_model = "faux-vision"
    text_model = "faux-texte"

    def __init__(self, contenu: str) -> None:
        self.contenu = contenu

    def generate(self, **kwargs) -> LLMResponse:
        return LLMResponse(text=self.contenu, provider=self.provider, model=self.vision_model)


def test_decode_image_avec_prefixe_data_url():
    payload = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
    assert decode_image_payload(payload) == PNG_1PX


def test_decode_image_invalide():
    with pytest.raises(ValueError):
        decode_image_payload("ceci-n-est-pas-du-base64-valide-@@@")


def test_validate_image_accepte_png_et_refuse_autre_chose(settings):
    assert validate_image(PNG_1PX, settings) == PNG_1PX
    with pytest.raises(ValueError):
        validate_image(b"pas une image du tout", settings)


def test_validate_image_refuse_les_fichiers_trop_lourds(settings):
    settings.max_upload_mb = 1
    with pytest.raises(ValueError):
        validate_image(PNG_1PX + b"\x00" * (2 * 1024 * 1024), settings)


def test_parse_json_block_tolere_le_bruit_et_les_fences():
    assert parse_json_block('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_block('Voici le résultat : {"a": 2, "b": [1, 2],} ensuite')["a"] == 2
    assert parse_json_block("aucun json ici") == {}


def test_read_chart_sans_llm_renvoie_une_erreur_explicite(settings):
    observation = read_chart(PNG_1PX, llm=None, settings=settings)
    assert observation.available is False
    assert "vision" in observation.error.lower() or "LLM" in observation.error


def test_read_chart_avec_llm_structure_la_reponse(settings):
    contenu = json.dumps(
        {
            "type_de_graphique": "chandeliers japonais",
            "marche_ou_symbole_estime": "AAPL",
            "unite_de_temps_estimee": "D1",
            "tendance": "haussière, sommets et creux ascendants",
            "fourchette_de_prix": "180 - 205",
            "dernier_prix_estime": "203.4",
            "figures": ["tête-épaules", "doji"],
            "niveaux": ["support 188", "résistance 205"],
            "indicateurs_visibles": ["RSI 62"],
            "bougies_notables": ["avalement haussier"],
            "volume": "croissant",
            "resume": "Tendance haussière avec une résistance majeure à 205.",
            "elements_incertains": ["échelle exacte"],
            "confiance": 0.72,
        }
    )
    observation = read_chart(PNG_1PX, llm=FauxLLM(contenu), settings=settings)

    assert observation.available is True
    assert observation.symbol_guess == "AAPL"
    assert observation.timeframe == "D1"
    assert "tête-épaules" in observation.patterns
    assert observation.confidence == pytest.approx(0.72)
    assert "AAPL" in observation.rag_query
    assert "tête-épaules" in observation.rag_query
    assert "Réponse" not in observation.render()  # rendu texte propre


def test_guess_symbol(settings):
    from app.services.analysis_service import guess_symbol

    assert guess_symbol("Symbole estimé : BTC-USD (unité de temps D1)") == "BTC-USD"
    assert guess_symbol("On voit un graphique en D1 sans symbole lisible") == ""
    assert guess_symbol("indice ^FCHI sur 6 mois") == "^FCHI"


# --------------------------------------------------------------------- #
#  Compatibilité des clés Gemini du nouveau format « Auth » (préfixe AQ.)
# --------------------------------------------------------------------- #

class _FauxReponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FauxClient:
    """Client httpx factice : enregistre les appels au lieu de partir sur le réseau."""

    appels: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        _FauxClient.appels.append({"url": url, **kwargs})
        if "batchEmbedContents" in url:
            return _FauxReponse({"embeddings": [{"values": [0.1, 0.2, 0.3]}]})
        if ":embedContent" in url:
            return _FauxReponse({"embedding": {"values": [0.1, 0.2, 0.3]}})
        return _FauxReponse(
            {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {},
            }
        )


def test_cle_gemini_nouveau_format_envoyee_dans_l_en_tete(monkeypatch):
    """Les clés AQ. (AI Studio 2026) exigent l'en-tête x-goog-api-key, pas ?key=."""
    from app import llm as module_llm

    _FauxClient.appels = []
    monkeypatch.setattr(module_llm.httpx, "Client", _FauxClient)

    cle = "AQ.Ab8RN6Jqj2epTjl7UtMbfNxC_exemple_de_cle"
    client = module_llm.GeminiLLM(cle, vision_model="gemini-2.5-flash")
    reponse = client.generate(
        system="system", messages=[{"role": "user", "content": "bonjour"}], images=[], use_vision=False
    )

    assert reponse.text == "ok"
    appel = _FauxClient.appels[-1]
    assert appel["headers"]["x-goog-api-key"] == cle, "l'en-tête d'authentification est obligatoire"
    assert appel["params"]["key"] == cle, "l'ancien format (AIza…) reste supporté en parallèle"


def test_embeddings_gemini_utilisent_le_meme_en_tete(monkeypatch):
    from app import embeddings as module_embeddings

    _FauxClient.appels = []
    monkeypatch.setattr(module_embeddings.httpx, "Client", _FauxClient)

    cle = "AQ.Ab8RN6Jqj2epTjl7UtMbfNxC_exemple_de_cle"
    embedder = module_embeddings.GeminiEmbedder(cle)

    vecteurs = embedder.embed_documents(["un texte"])
    assert vecteurs
    appel = _FauxClient.appels[-1]
    assert appel["headers"]["x-goog-api-key"] == cle
    assert "?key=" not in appel["url"]

    _FauxClient.appels = []
    embedder.embed_query("une question")
    assert _FauxClient.appels[-1]["headers"]["x-goog-api-key"] == cle


def test_message_d_erreur_explique_les_cles_invalides(monkeypatch):
    from app import llm as module_llm

    class _ClientRefus(_FauxClient):
        def post(self, url, **kwargs):
            return _FauxReponsePasOk()

    class _FauxReponsePasOk:
        status_code = 404
        text = "models/gemini-2.5-flash is not found"

    monkeypatch.setattr(module_llm.httpx, "Client", _ClientRefus)
    client = module_llm.GeminiLLM("AQ.cleFausse")

    try:
        client.generate(system="s", messages=[{"role": "user", "content": "x"}], images=[])
    except module_llm.LLMError as exc:
        message = str(exc)
        assert "GEMINI_API_KEY" in message
        assert "AQ." in message
    else:
        raise AssertionError("une LLMError était attendue")
