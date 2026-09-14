"""Tests d'ingestion (PDF/texte), de base vectorielle et de recherche hybride."""

from __future__ import annotations

import pytest

from app.knowledge import (
    IngestionError,
    ingest_bytes,
    ingest_file,
    read_stats,
    sync_all,
)
from app.retriever import build_context, search, search_multi
from app.vectorstore import get_vector_store


def test_indexation_du_corpus_d_exemple(settings):
    rapport = sync_all(settings=settings, force=True)
    statistiques = read_stats(settings)

    assert statistiques["documents"] >= 5
    assert statistiques["chunks"] >= 20
    assert statistiques["chunks_with_vectors"] == statistiques["chunks"]
    assert statistiques["embedding_provider"].startswith("hash")
    assert rapport["chunks_indexed"] > 0


def test_recherche_hybride_trouve_la_bonne_section(indexed_settings):
    resultat = search("Qu'est-ce qu'une épaule-tête-épaules ?", top_k=3)
    assert resultat.mode in {"hybride", "vecteurs", "bm25"}
    assert resultat.hits
    textes = " ".join(hit.chunk.text.lower() for hit in resultat.hits)
    assert "paule" in textes  # « épaule » ou « épaules »


def test_recherche_multi_fusionne_les_resultats(indexed_settings):
    resultat = search_multi(
        ["gestion du risque stop loss", "divergence RSI"], top_k=4
    )
    assert 0 < len(resultat.hits) <= 4
    titres = {hit.chunk.metadata.get("title") for hit in resultat.hits}
    assert titres


def test_contexte_limite_en_taille(indexed_settings):
    resultat = search("moyennes mobiles tendance", top_k=5)
    contexte = build_context(resultat.hits, max_chars=1200)
    assert len(contexte) <= 1400
    assert "[Source 1]" in contexte


def test_repli_bm25_quand_les_embeddings_echouent(indexed_settings, monkeypatch):
    import app.retriever as retriever
    from app.embeddings import EmbeddingError

    class EmbedderCasse:
        name = "casse"
        label = "casse"

        def embed_query(self, text):
            raise EmbeddingError("panne simulée")

        def embed_documents(self, texts):
            raise EmbeddingError("panne simulée")

    monkeypatch.setattr(retriever, "get_embedder", lambda settings=None, refresh=False: EmbedderCasse())
    resultat = search("tête-épaules retournement", top_k=3)
    assert resultat.mode == "bm25"
    assert resultat.hits
    assert any("mots-clés" in note or "Embeddings" in note for note in resultat.notes)


def test_ingestion_texte_et_idempotence(settings):
    contenu = (
        "Ma méthode personnelle : je trade le CAC 40 en journalier, "
        "avec un stop sous le dernier creux de structure et un risque de 1 %."
    ).encode("utf-8")

    premier = ingest_bytes(contenu, "methode-perso.md", settings=settings)
    assert premier["status"] == "ingested"
    assert premier["chunks"] >= 1

    # Deuxième ingestion du même contenu : nouveau fichier (hash dans le nom),
    # donc deux documents distincts mais indexés sans erreur.
    second = ingest_bytes(contenu, "methode-perso.md", settings=settings)
    assert second["doc_id"] != premier["doc_id"]

    statistiques = read_stats(settings)
    assert statistiques["documents"] == 2
    # Le même contenu réel ne doit pas créer deux fois le même document :
    chemin = premier["source"]
    from pathlib import Path

    identique = ingest_file(Path(settings.pdf_path) / chemin, settings=settings, namespace="upload")
    assert identique["status"] == "unchanged"


def test_extension_non_supportee_rejetee(settings, tmp_path):
    fichier = tmp_path / "donnees.xlsx"
    fichier.write_bytes(b"contenu")
    with pytest.raises(IngestionError):
        ingest_file(fichier, settings=settings)


def test_suppression_de_document(indexed_settings):
    store = get_vector_store(indexed_settings)
    documents = store.list_documents()
    assert documents
    cible = documents[0]["doc_id"]
    assert store.delete_document(cible) is True
    assert store.get_document(cible) is None
    assert all(doc["doc_id"] != cible for doc in store.list_documents())
