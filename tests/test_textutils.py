"""Tests du découpage de texte et de la tokenisation."""

from __future__ import annotations

from app.textutils import (
    chunk_document,
    chunk_text,
    keywords,
    normalize,
    split_sections,
    tokenize,
)


def test_normalize_retire_les_accents_et_la_casse():
    assert normalize("Tête-Épaules  RENVERSÉE") == "tete-epaules renversee"


def test_tokenize_filtre_les_mots_vides_et_racinise():
    tokens = tokenize("Le support est cassé par les vendeurs")
    assert "support" in tokens
    assert "vendeur" in tokens  # « vendeurs » racinisé
    assert "les" not in tokens and "par" not in tokens


def test_chunk_text_respecte_la_taille_et_le_chevauchement():
    texte = " ".join(f"mot{n}" for n in range(1000))
    chunks = chunk_text(texte, chunk_size_words=200, overlap_words=40, min_words=30)
    assert len(chunks) >= 5
    assert all(40 <= len(chunk.text.split()) <= 320 for chunk in chunks)
    # les index sont croissants et le chevauchement existe
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    fin_premier = chunks[0].text.split()[-1]
    assert fin_premier in chunks[1].text


def test_chunk_text_fusionne_les_queues_trop_courtes():
    texte = " ".join(f"mot{n}" for n in range(105))
    chunks = chunk_text(texte, chunk_size_words=100, overlap_words=10, min_words=40)
    assert len(chunks) == 1  # la queue de 5 mots est fusionnée, pas perdue
    assert len(chunks[0].text.split()) == 105


def test_split_sections_detecte_les_titres():
    document = (
        "1. Tendance et structure\n"
        "Le prix évolue en tendances.\n\n"
        "2. Supports et résistances\n"
        "Une zone testée trois fois est significative.\n"
    )
    sections = split_sections(document)
    titres = [titre for titre, _ in sections]
    assert any("Tendance" in titre for titre in titres)
    assert any("Supports" in titre for titre in titres)


def test_chunk_document_conserve_la_section():
    document = "## Marteau\n" + ("Un marteau apparaît après une baisse. " * 60)
    chunks = chunk_document(document, chunk_size_words=120, overlap_words=20, min_words=20)
    assert chunks
    assert all("Marteau" in chunk.section for chunk in chunks)


def test_keywords_classe_par_frequence():
    texte = "support support support résistance résistance tendance"
    mots = keywords(texte, limit=3)
    # Les mots-clés gardent leur forme lisible (accents), classés par fréquence
    assert normalize(mots[0]) == "support"
    assert "resistance" in [normalize(mot) for mot in mots]
    assert [normalize(mot) for mot in mots][:2] == ["support", "resistance"]
