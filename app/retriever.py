"""Recherche hybride (vecteurs + BM25) dans la base de connaissances.

Formule : score = alpha * similarité_vectorielle + (1 - alpha) * score_BM25,
chaque composante étant normalisée sur [0, 1] avant combinaison.

Intérêt : les vecteurs captent le *sens* (« figure de retournement après
tendance haussière »), BM25 capte les *termes exacts* (« tête-épaules », « RSI 14 »).
Si les embeddings sont indisponibles (pas de clé, quota, hors-ligne), la
recherche continue en BM25 pur : jamais d'échec.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Sequence

from .bm25 import BM25
from .config import Settings, get_settings
from .embeddings import EmbeddingError, get_embedder
from .vectorstore import SearchHit, get_vector_store

_lock = threading.RLock()


@dataclass
class RetrievalResult:
    """Résultat complet d'une recherche."""

    hits: list[SearchHit]
    mode: str  # "hybride" | "bm25" | "vecteurs"
    notes: list[str]

    @property
    def sources(self) -> list[dict[str, Any]]:
        return [
            {
                "title": hit.title,
                "source": hit.source,
                "section": hit.chunk.section,
                "page": hit.chunk.page,
                "score": round(hit.score, 4),
                "dense_score": round(hit.dense_score, 4),
                "lexical_score": round(hit.lexical_score, 4),
                "extract": hit.chunk.text[:400],
            }
            for hit in self.hits
        ]


class _IndexCache:
    """Cache mémoire (BM25 + vecteurs) invalidé quand la base change."""

    def __init__(self) -> None:
        self.revision = -1
        self.embedder_label = ""
        self.chunks: list[Any] = []
        self.bm25 = BM25()

    def refresh(self, store, embedder_label: str) -> None:
        if self.revision == store.revision and self.embedder_label == embedder_label:
            return
        self.chunks = store.iter_chunks()
        self.bm25 = BM25()
        self.bm25.build([(chunk.chunk_id, f"{chunk.section} {chunk.text}") for chunk in self.chunks])
        self.revision = store.revision
        self.embedder_label = embedder_label


_CACHE = _IndexCache()


def _normalize_scores(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    low = min(values.values())
    high = max(values.values())
    if high - low < 1e-9:
        return {key: 1.0 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def search(
    query: str,
    *,
    top_k: int | None = None,
    settings: Settings | None = None,
    doc_ids: Sequence[str] | None = None,
    alpha: float | None = None,
) -> RetrievalResult:
    """Recherche hybride, robuste aux pannes d'embeddings."""
    settings = settings or get_settings()
    top_k = top_k or settings.top_k
    alpha = settings.hybrid_alpha if alpha is None else max(0.0, min(1.0, alpha))
    store = get_vector_store(settings)
    embedder = get_embedder(settings)
    notes: list[str] = []

    with _lock:
        _CACHE.refresh(store, embedder.label)
        candidates = _CACHE.chunks
        bm25 = _CACHE.bm25

    if not candidates:
        return RetrievalResult(hits=[], mode="vide", notes=["Base de connaissances vide."])

    # --- scores lexicaux (BM25) ------------------------------------- #
    lexical: dict[str, float] = {}
    chunk_by_id = {chunk.chunk_id: chunk for chunk in candidates}
    for chunk_id, score in bm25.search(query, top_k=max(top_k * 6, 24)):
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        if doc_ids and chunk.doc_id not in set(doc_ids):
            continue
        lexical[chunk_id] = score

    # --- scores vectoriels ------------------------------------------ #
    dense: dict[str, float] = {}
    mode = "bm25"
    try:
        vector = embedder.embed_query(query)
        if vector and len(vector) == embedder.dim:
            dense_hits = store.search_dense(vector, top_k=max(top_k * 6, 24), doc_ids=doc_ids)
            dense = {hit.chunk.chunk_id: hit.dense_score for hit in dense_hits}
            mode = "vecteurs" if not lexical else "hybride"
    except EmbeddingError as exc:
        notes.append(f"Embeddings indisponibles ({exc}) — recherche par mots-clés uniquement.")
    except Exception as exc:  # pragma: no cover - réseau, quota...
        notes.append(f"Recherche vectorielle en échec ({exc}) — repli mots-clés.")

    if dense and lexical:
        mode = "hybride"
    elif dense:
        mode = "vecteurs"

    dense_norm = _normalize_scores(dense)
    lexical_norm = _normalize_scores(lexical)

    combined: dict[str, float] = {}
    for chunk_id in set(dense_norm) | set(lexical_norm):
        dense_value = dense_norm.get(chunk_id, 0.0)
        lexical_value = lexical_norm.get(chunk_id, 0.0)
        if chunk_id not in dense_norm:  # absent du classement vectoriel
            combined[chunk_id] = (1 - alpha) * lexical_value * 0.85
        elif chunk_id not in lexical_norm:
            combined[chunk_id] = alpha * dense_value * 0.85
        else:
            combined[chunk_id] = alpha * dense_value + (1 - alpha) * lexical_value

    ranked = sorted(combined.items(), key=lambda item: item[1], reverse=True)[:top_k]
    hits = [
        SearchHit(
            chunk=chunk_by_id[chunk_id],
            score=score,
            dense_score=dense.get(chunk_id, 0.0),
            lexical_score=lexical.get(chunk_id, 0.0),
        )
        for chunk_id, score in ranked
        if chunk_id in chunk_by_id
    ]
    if not notes and mode == "bm25":
        notes.append("Recherche par mots-clés (embeddings non utilisés).")
    return RetrievalResult(hits=hits, mode=mode, notes=notes)


def search_multi(
    queries: Sequence[str],
    *,
    top_k: int | None = None,
    settings: Settings | None = None,
) -> RetrievalResult:
    """Recherche sur plusieurs requêtes puis fusion (utile : description + question)."""
    settings = settings or get_settings()
    top_k = top_k or settings.top_k
    accumulated: dict[str, SearchHit] = {}
    modes: list[str] = []
    notes: list[str] = []
    for query in [q for q in queries if q and q.strip()]:
        result = search(query, top_k=top_k, settings=settings)
        modes.append(result.mode)
        notes.extend(note for note in result.notes if note not in notes)
        for hit in result.hits:
            existing = accumulated.get(hit.chunk.chunk_id)
            if existing is None or hit.score > existing.score:
                accumulated[hit.chunk.chunk_id] = hit
    ranked = sorted(accumulated.values(), key=lambda hit: hit.score, reverse=True)[:top_k]
    mode = "hybride" if "hybride" in modes else (modes[0] if modes else "vide")
    return RetrievalResult(hits=ranked, mode=mode, notes=notes)


def build_context(hits: Sequence[SearchHit], max_chars: int = 14000) -> str:
    """Assemble les extraits récupérés en un bloc de contexte citables."""
    blocks: list[str] = []
    used = 0
    for position, hit in enumerate(hits, start=1):
        block = (
            f"[Source {position}] {hit.citation()}\n"
            f"{hit.chunk.text.strip()}\n"
        )
        if used + len(block) > max_chars:
            block = block[: max(0, max_chars - used)]
        if not block.strip():
            break
        blocks.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return "\n".join(blocks)


def invalidate_cache() -> None:
    """Force la reconstruction de l'index en mémoire."""
    with _lock:
        _CACHE.revision = -1
        _CACHE.chunks = []
        _CACHE.bm25 = BM25()
