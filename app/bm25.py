"""BM25 (Okapi) en Python pur — recherche lexicale, aucun modèle à télécharger.

Complète la recherche vectorielle : BM25 excelle sur les termes exacts
(« tête-épaules », « RSI 14 », « support 1.0850 »), les vecteurs sur le sens.
"""

from __future__ import annotations

import math
from collections import Counter

from .textutils import tokenize


class BM25:
    """Index BM25 minimaliste (k1/b réglables)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_len: list[int] = []
        self.avg_len: float = 0.0
        self.term_freqs: list[Counter] = []
        self.idf: dict[str, float] = {}

    def build(self, docs: list[tuple[str, str]]) -> None:
        """Construit l'index à partir d'une liste (doc_id, texte)."""
        self.doc_ids = [doc_id for doc_id, _ in docs]
        self.doc_tokens = [tokenize(text) for _, text in docs]
        self.doc_len = [len(tokens) for tokens in self.doc_tokens]
        self.avg_len = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.term_freqs = [Counter(tokens) for tokens in self.doc_tokens]

        df: Counter = Counter()
        for tokens in self.doc_tokens:
            df.update(set(tokens))
        total = len(self.doc_tokens) or 1
        self.idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Renvoie [(doc_id, score)] triés par pertinence décroissante."""
        if not self.doc_ids:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores: dict[int, float] = {}
        for token in query_tokens:
            idf = self.idf.get(token)
            if idf is None:
                continue
            weight = idf * (1.0 if len(token) > 4 else 0.6)
            for position, counts in enumerate(self.term_freqs):
                freq = counts.get(token)
                if not freq:
                    continue
                norm = 1 - self.b + self.b * (self.doc_len[position] / (self.avg_len or 1))
                scores[position] = scores.get(position, 0.0) + weight * (
                    freq * (self.k1 + 1) / (freq + self.k1 * norm)
                )
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self.doc_ids[pos], score) for pos, score in ranked]

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.doc_ids)
