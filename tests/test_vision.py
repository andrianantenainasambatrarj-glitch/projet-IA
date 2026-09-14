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
