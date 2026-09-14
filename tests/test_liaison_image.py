"""Non-régression : liaison entre la capture envoyée, les données de marché et le RAG.

Ces tests verrouillent la chaîne complète de l'analyse d'image :

    capture → passe vision (LLM) → confrontation avec le symbole demandé
            → requêtes de recherche dans les cours → réponse finale

Le bug d'origine : une capture EUR/USD analysée comme AAPL, sans le moindre
avertissement, parce que (1) l'actif lu sur l'image n'était jamais comparé au
symbole saisi, (2) « EUR/USD » n'était reconnu par aucune règle de détection de
symbole (seuls « . », « - » et « = » étaient acceptés), et (3) l'interface
n'affichait nulle part ce que le modèle avait réellement lu sur l'image.
"""

from __future__ import annotations

import json
import struct
import zlib
from typing import Any

import pytest

from app.llm import BaseLLM, LLMResponse
from app.services.analysis_service import (
    AnalysisRequest,
    find_pair_symbol,
    guess_symbol,
    run_analysis,
)


# --------------------------------------------------------------------- #
#  Outils de test
# --------------------------------------------------------------------- #

def png_image(width: int = 320, height: int = 180) -> bytes:
    """Fabrique un vrai PNG minimal (entête + pixels), sans dépendance externe."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        bloc = tag + data
        return struct.pack(">I", len(data)) + bloc + struct.pack(">I", zlib.crc32(bloc) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    lignes = b"".join(b"\x00" + b"\x20\x40\x60" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(lignes))
        + chunk(b"IEND", b"")
    )


class EspionLLM(BaseLLM):
    """Faux fournisseur : enregistre tout ce qui lui est envoyé."""

    provider = "espion"
    supports_vision = True

    def __init__(self, *, symbole_lu: str = "EUR/USD", unite: str = "H1") -> None:
        super().__init__(vision_model="modele-test", text_model="modele-test")
        self.appels: list[dict[str, Any]] = []
        self._json_payload = {
            "type_de_graphique": "chandeliers japonais",
            "marche_ou_symbole_estime": symbole_lu,
            "unite_de_temps_estimee": unite,
            "tendance": "baissière, sommets descendants",
            "fourchette_de_prix": "1.0720 - 1.0980",
            "dernier_prix_estime": "1.0764",
            "figures": ["tete-epaules", "double sommet"],
            "niveaux": ["resistance 1.0920"],
            "indicateurs_visibles": ["RSI 42"],
            "bougies_notables": ["bougie d'engloutissement le 2026-09-10"],
            "volume": "faible",
            "resume": f"Graphique {symbole_lu} en {unite}, tendance baissière.",
            "elements_incertains": ["échelle de l'axe peu lisible"],
            "confiance": 0.72,
        }

    def generate(self, *, system, messages, images=(), use_vision=None, max_tokens=None,
                 temperature=None, json_mode=False):
        self.appels.append(
            {
                "json_mode": json_mode,
                "use_vision": use_vision,
                "nb_images": len(images),
                "octets_images": [len(image) for image in images],
                "prompt": messages[-1]["content"] if messages else "",
            }
        )
        if json_mode:
            return LLMResponse(
                text=json.dumps(self._json_payload, ensure_ascii=False),
                provider=self.provider,
                model="modele-test",
            )
        return LLMResponse(
            text="### 1. Lecture du graphique\nRéponse de test.",
            provider=self.provider,
            model="modele-test",
        )


# --------------------------------------------------------------------- #
#  1) Reconnaissance des paires (formats réels des plateformes de trading)
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("EUR/USD H1 tendance baissière", "EURUSD=X"),
        ("graphique EUR-USD en 4 heures", "EURUSD=X"),
        ("EURUSD", "EURUSD=X"),
        ("symbole : EURUSD=X", "EURUSD=X"),
        ("BTC/USD", "BTC-USD"),
        ("ETH/USDT", "ETH-USD"),  # un stablecoin est traité comme l'USD
        ("graphique de l'euro dollar", "EURUSD=X"),
        ("USD/JPY quotidien", "USDJPY=X"),
        # Non-régression : comportements historiques conservés
        ("Symbole estimé : BTC-USD (unité de temps D1)", "BTC-USD"),
        ("On voit un graphique en D1 sans symbole lisible", ""),
        ("indice ^FCHI sur 6 mois", "^FCHI"),
        ("NVDA en pullback", "NVDA"),
    ],
)
def test_reconnaissance_des_paires(texte: str, attendu: str) -> None:
    assert guess_symbol(texte) == attendu


def test_find_pair_symbol_ne_confond_pas_les_mots() -> None:
    """Une phrase française ne doit jamais produire un faux symbole."""
    for phrase in [
        "indicateur personnel sur le graphique",
        "les prix montent doucement",
        "doji sur support majeur",
    ]:
        assert find_pair_symbol(phrase) == ""
        assert guess_symbol(phrase) == ""


# --------------------------------------------------------------------- #
#  2) La capture est bien transmise, et lue avant l'analyse
# --------------------------------------------------------------------- #

def test_capture_transmise_aux_deux_appels(settings, monkeypatch) -> None:
    espion = EspionLLM()
    monkeypatch.setattr("app.services.analysis_service.get_llm", lambda *a, **k: espion)

    reponse = run_analysis(
        AnalysisRequest(symbol="AAPL", image=png_image(), save=False), settings=settings
    )

    assert len(espion.appels) == 2, "la passe vision puis l'analyse doivent être appelées"
    vision, analyse = espion.appels
    assert vision["json_mode"] is True and vision["use_vision"] is True
    assert vision["nb_images"] == 1 and vision["octets_images"][0] > 0
    assert analyse["use_vision"] is True and analyse["nb_images"] == 1
    # La lecture de l'image est bien réinjectée dans le prompt d'analyse
    assert "LECTURE DE LA CAPTURE" in analyse["prompt"]
    assert "EUR/USD" in analyse["prompt"]
    assert reponse.image["fournie"] is True
    assert reponse.image["transmise_au_modele"] is True
    assert reponse.image["lecture_reussie"] is True


def test_tracabilite_de_la_capture_dans_la_reponse(settings, monkeypatch) -> None:
    espion = EspionLLM()
    monkeypatch.setattr("app.services.analysis_service.get_llm", lambda *a, **k: espion)
    reponse = run_analysis(
        AnalysisRequest(symbol="AAPL", image=png_image(), save=False), settings=settings
    )
    image = reponse.image
    assert image["taille_ko"] > 0
    assert image["modele_vision"] == "modele-test"
    assert image["erreur"] == ""
    assert image["lecture_reussie"] is True


# --------------------------------------------------------------------- #
#  3) Incohérence capture ↔ symbole : elle doit être détectée et dite
# --------------------------------------------------------------------- #

def test_incoherence_capture_et_symbole_signalee(settings, monkeypatch) -> None:
    espion = EspionLLM()
    monkeypatch.setattr("app.services.analysis_service.get_llm", lambda *a, **k: espion)

    reponse = run_analysis(
        AnalysisRequest(symbol="AAPL", image=png_image(), save=False), settings=settings
    )

    coherence = reponse.coherence
    assert coherence["symbole_demande"] == "AAPL"
    assert coherence["capture_lue"] == "EUR/USD"
    assert coherence["symbole_capture"] == "EURUSD=X"
    assert coherence["incoherent"] is True
    assert "EURUSD=X" in coherence["message"]

    avertissements = " ".join(reponse.warnings)
    assert "Incohérence détectée" in avertissements
    assert "EUR/USD" in avertissements and "AAPL" in avertissements

    # Le prompt doit porter une consigne impérative de confrontation
    prompt = espion.appels[1]["prompt"]
    assert "CONTRÔLE DE COHÉRENCE" in prompt
    assert "INCOHÉRENCE" in prompt
    assert "appuie toutes les figures" in prompt
    # Le rapport technique reste présent, mais présenté pour le bon actif
    assert "Actif des données chiffrées : AAPL" in prompt


def test_capture_concordante_pas_de_fausse_alerte(settings, monkeypatch) -> None:
    espion = EspionLLM(symbole_lu="AAPL")
    monkeypatch.setattr("app.services.analysis_service.get_llm", lambda *a, **k: espion)

    reponse = run_analysis(
        AnalysisRequest(symbol="AAPL", image=png_image(), save=False), settings=settings
    )
    assert reponse.coherence["incoherent"] is False
    assert reponse.coherence["symbole_capture"] == "AAPL"
    assert "concordent" in reponse.coherence["message"]
    assert "Incohérence détectée" not in " ".join(reponse.warnings)


def test_symbole_deduit_de_la_capture_quand_aucun_nest_saisi(settings, monkeypatch) -> None:
    """Sans symbole saisi, l'actif lu sur la capture est analysé (cas Forex)."""
    espion = EspionLLM()
    monkeypatch.setattr("app.services.analysis_service.get_llm", lambda *a, **k: espion)

    reponse = run_analysis(AnalysisRequest(symbol="", image=png_image(), save=False), settings=settings)

    assert reponse.analysis is not None
    assert reponse.analysis["instrument"]["symbol"] == "EURUSD=X"
    assert reponse.coherence["incoherent"] is False
    assert "EURUSD=X" in " ".join(reponse.warnings)


# --------------------------------------------------------------------- #
#  4) Liaison avec la base de cours (RAG) : les requêtes doivent être tracées
# --------------------------------------------------------------------- #

def test_requetes_rag_tracees_et_issues_de_limage(settings, monkeypatch) -> None:
    espion = EspionLLM()
    monkeypatch.setattr("app.services.analysis_service.get_llm", lambda *a, **k: espion)

    reponse = run_analysis(
        AnalysisRequest(symbol="", image=png_image(), question="Le stop est-il bien placé ?",
                        save=False),
        settings=settings,
    )

    assert reponse.rag_requetes, "les requêtes réellement utilisées doivent être renvoyées"
    jointes = " | ".join(reponse.rag_requetes)
    assert "Le stop est-il bien placé ?" in jointes
    assert "EUR/USD" in jointes, "la lecture de l'image doit alimenter la recherche documentaire"
    assert reponse.rag_mode, "le mode de recherche (hybride/BM25…) doit être renseigné"


# --------------------------------------------------------------------- #
#  5) Le fournisseur Gemini reçoit bien l'image dans sa requête HTTP
# --------------------------------------------------------------------- #

class FauxReponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FauxClient:
    """Capture le corps JSON envoyé à l'API, sans aucun accès réseau."""

    dernier: dict[str, Any] = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "FauxClient":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def get(self, *args, **kwargs) -> FauxReponse:
        return FauxReponse({"models": [{"name": "models/modele-test",
                                        "supportedGenerationMethods": ["generateContent"]}]})

    def post(self, url, *, params=None, headers=None, json=None) -> FauxReponse:
        FauxClient.dernier = {"url": url, "json": json or {}, "headers": headers or {}}
        return FauxReponse({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})


def test_gemini_recoit_limage_dans_la_requete(monkeypatch) -> None:
    from app import llm as module_llm

    monkeypatch.setattr(module_llm.httpx, "Client", FauxClient)
    client = module_llm.GeminiLLM("AQ.cle-de-test")
    image = png_image()

    client.generate(
        system="systeme",
        messages=[{"role": "user", "content": "décris ce graphique"}],
        images=[image],
        use_vision=True,
    )

    corps = FauxClient.dernier["json"]
    parties = corps["contents"][0]["parts"]
    inline = [partie for partie in parties if "inline_data" in partie]
    assert inline, "l'image doit être transmise en inline_data"
    assert inline[0]["inline_data"]["mime_type"] == "image/png"
    assert len(inline[0]["inline_data"]["data"]) > 100
    # Les clés « AQ. » doivent partir en en-tête, jamais en paramètre d'URL
    assert FauxClient.dernier["headers"].get("x-goog-api-key") == "AQ.cle-de-test"


def test_gemini_sans_image_pour_le_texte(monkeypatch) -> None:
    """Une analyse sans capture ne doit pas envoyer de partie binaire."""
    from app import llm as module_llm

    monkeypatch.setattr(module_llm.httpx, "Client", FauxClient)
    client = module_llm.GeminiLLM("AQ.cle-de-test")
    client.generate(system="systeme", messages=[{"role": "user", "content": "texte"}])

    parties = FauxClient.dernier["json"]["contents"][0]["parts"]
    assert not any("inline_data" in partie for partie in parties)
