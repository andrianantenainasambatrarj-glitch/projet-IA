"""Fixtures partagées : environnement de test isolé (aucun accès réseau)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Permet `pytest` depuis la racine du projet sans installation
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reset_caches() -> None:
    from app.config import reset_settings_cache
    from app.embeddings import clear_embedder_cache
    from app.llm import clear_llm_cache
    from app.reports import clear_report_stores
    from app.retriever import invalidate_cache
    from app.vectorstore import clear_store_cache

    reset_settings_cache()
    clear_store_cache()
    clear_report_stores()
    clear_embedder_cache()
    clear_llm_cache()
    invalidate_cache()


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Configuration isolée : données dans tmp_path, providers hors-ligne."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("MARKET_PROVIDER", "demo")
    monkeypatch.setenv("LLM_PROVIDER", "demo")
    monkeypatch.setenv("AUTO_INGEST_ON_START", "false")
    monkeypatch.setenv("API_ACCESS_TOKEN", "")
    monkeypatch.setenv("ENABLE_LOCAL_EMBEDDINGS", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OLLAMA_BASE_URL", "")

    _reset_caches()
    from app.config import get_settings

    config = get_settings()
    config.ensure_dirs()
    yield config
    _reset_caches()


@pytest.fixture()
def indexed_settings(settings):
    """Configuration dont la base de connaissances est déjà indexée."""
    from app.knowledge import sync_all

    sync_all(settings=settings, force=True)
    return settings


@pytest.fixture()
def client(indexed_settings):
    """Client HTTP de test sur l'application FastAPI."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
