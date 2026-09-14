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
