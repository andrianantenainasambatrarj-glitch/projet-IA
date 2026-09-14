"""Fournisseurs d'embeddings interchangeables.

Quatre modes, du plus léger au plus performant :

* ``hashing`` : 100 % local, instantané, sans téléchargement (défaut gratuit).
  Feature hashing de mots + bigrammes + trigrammes de caractères.
* ``gemini``  : API Google (clé gratuite AI Studio), excellente qualité multilingue.
* ``openai``  : API OpenAI (``text-embedding-3-small``).
* ``local``   : sentence-transformers (``paraphrase-multilingual-MiniLM-L12-v2``),
  ~120 Mo téléchargés au premier lancement, puis hors-ligne.

Si un fournisseur échoue, la recherche bascule automatiquement sur BM25
(mots-clés) : l'application ne tombe jamais en panne à cause des embeddings.
"""

from __future__ import annotations

import array
import hashlib
import math
import re
from typing import Iterable, Sequence

import httpx

from .config import Settings, get_settings
from .textutils import normalize, tokenize


class EmbeddingError(RuntimeError):
    """Erreur d'embedding (clé absente, quota, réseau...)."""


# --------------------------------------------------------------------- #
#  Interface commune
# --------------------------------------------------------------------- #

class BaseEmbedder:
    """Interface minimaliste d'un fournisseur d'embeddings."""

    name: str = "base"
    model: str = ""
    kind: str = "dense"
    needs_network: bool = False

    def __init__(self, dim: int | None = None) -> None:
        self._dim = dim or 0

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def ready(self) -> bool:  # pragma: no cover - surchargé
        return True

    @property
    def label(self) -> str:
        return self.model or self.name

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else [0.0] * self.dim


# --------------------------------------------------------------------- #
#  1. Hashing (local, zéro dépendance, zéro téléchargement)
# --------------------------------------------------------------------- #

class HashingEmbedder(BaseEmbedder):
    """Embeddings par feature hashing : déterministe, local, sans modèle.

    Ce n'est pas un modèle sémantique profond, mais couplé à BM25 il donne
    une recherche tout à fait exploitable sur un corpus de cours de trading,
    et il rend l'application gratuite et instantanée à déployer.
    """

    name = "hashing"
    kind = "sparse-hash"
    needs_network = False

    def __init__(self, dim: int = 512) -> None:
        super().__init__(dim=max(64, int(dim)))
        self.model = f"hash-{self.dim}d"

    @staticmethod
    def _bucket(token: str, dim: int) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % dim
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return index, sign

    def _tokens_for(self, text: str) -> list[tuple[str, float]]:
        normalized = normalize(text)
        words = tokenize(normalized, stem=True)
        features: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]

        for i in range(len(words) - 1):
            features.append((f"b:{words[i]}_{words[i + 1]}", 1.4))

        compact = re.sub(r"\s+", " ", normalized)
        for i in range(len(compact) - 3):
            gram = compact[i : i + 3]
            if gram.strip():
                features.append((f"c:{gram}", 0.35))

        # termes numériques (niveaux, périodes : "1.0850", "rsi 14")
        for number in re.findall(r"\d+(?:[.,]\d+)?", compact):
            features.append((f"n:{number}", 1.2))
        return features

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            counts: dict[str, float] = {}
            for feature, weight in self._tokens_for(text):
                counts[feature] = counts.get(feature, 0.0) + weight

            vector = [0.0] * self.dim
            for feature, weight in counts.items():
                index, sign = self._bucket(feature, self.dim)
                vector[index] += sign * (1.0 + math.log(weight)) * weight
            vectors.append(_l2_normalize(vector))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else [0.0] * self.dim


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12:
        return vector
    return [value / norm for value in vector]


# --------------------------------------------------------------------- #
#  2. Google Gemini
# --------------------------------------------------------------------- #

class GeminiEmbedder(BaseEmbedder):
    """Embeddings via l'API Google Generative Language (clé AI Studio gratuite)."""

    name = "gemini"
    needs_network = True

    def __init__(self, api_key: str, model: str = "", dim: int = 0, timeout: float = 60.0) -> None:
        super().__init__(dim=dim)
        self.api_key = (api_key or "").strip()
        self.model = model or "text-embedding-004"
        self.timeout = timeout

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def _endpoint(self, action: str) -> str:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:{action}?key={self.api_key}"
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.ready:
            raise EmbeddingError("Clé GEMINI_API_KEY absente pour les embeddings Gemini.")
        if not texts:
            return []
        vectors: list[list[float]] = []
        batch_size = 16
        with httpx.Client(timeout=self.timeout) as client:
            for start in range(0, len(texts), batch_size):
                batch = [t[:8000] for t in texts[start : start + batch_size]]
                payload = {
                    "requests": [
                        {
                            "model": f"models/{self.model}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": "RETRIEVAL_DOCUMENT",
                        }
                        for text in batch
                    ]
                }
                response = client.post(self._endpoint("batchEmbedContents"), json=payload)
                if response.status_code >= 400:
                    raise EmbeddingError(
                        f"Gemini embeddings ({response.status_code}) : {response.text[:200]}"
                    )
                data = response.json()
                for item in data.get("embeddings", []):
                    values = item.get("values") or []
                    if values:
                        vectors.append([float(v) for v in values])
        if vectors and not self._dim:
            self._dim = len(vectors[0])
        return [_l2_normalize(v) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        if not self.ready:
            raise EmbeddingError("Clé GEMINI_API_KEY absente pour les embeddings Gemini.")
        payload = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text[:8000]}]},
            "taskType": "RETRIEVAL_QUERY",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self._endpoint("embedContent"), json=payload)
        if response.status_code >= 400:
            raise EmbeddingError(
                f"Gemini embeddings ({response.status_code}) : {response.text[:200]}"
            )
        values = response.json().get("embedding", {}).get("values", [])
        if values and not self._dim:
            self._dim = len(values)
        return _l2_normalize([float(v) for v in values])


# --------------------------------------------------------------------- #
#  3. OpenAI / compatible
# --------------------------------------------------------------------- #

class OpenAIEmbedder(BaseEmbedder):
    """Embeddings via l'API OpenAI (ou tout endpoint compatible)."""

    name = "openai"
    needs_network = True

    def __init__(
        self,
        api_key: str,
        model: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        super().__init__()
        self.api_key = (api_key or "").strip()
        self.model = model or "text-embedding-3-small"
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.ready:
            raise EmbeddingError("Clé OPENAI_API_KEY absente pour les embeddings.")
        if not texts:
            return []
        vectors: list[list[float]] = []
        batch_size = 64
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            for start in range(0, len(texts), batch_size):
                batch = [t[:8000] for t in texts[start : start + batch_size]]
                response = client.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json={"model": self.model, "input": batch},
                )
                if response.status_code >= 400:
                    raise EmbeddingError(
                        f"OpenAI embeddings ({response.status_code}) : {response.text[:200]}"
                    )
                data = response.json()
                ordered = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
                for item in ordered:
                    values = item.get("embedding") or []
                    if values:
                        vectors.append([float(v) for v in values])
        if vectors and not self._dim:
            self._dim = len(vectors[0])
        return [_l2_normalize(v) for v in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        vectors = self._embed([text])
        return vectors[0] if vectors else []


# --------------------------------------------------------------------- #
#  4. sentence-transformers (local, hors-ligne après téléchargement)
# --------------------------------------------------------------------- #

class LocalEmbedder(BaseEmbedder):
    """Embeddings via sentence-transformers (modèle multilingue)."""

    name = "local"
    needs_network = False

    def __init__(self, model: str = "") -> None:
        super().__init__()
        self.model = model or "paraphrase-multilingual-MiniLM-L12-v2"
        self._model = None
        self._error: str = ""

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            self._error = (
                "sentence-transformers indisponible. Installez "
                "`pip install sentence-transformers` ou choisissez un autre "
                "fournisseur d'embeddings."
            )
            raise EmbeddingError(self._error) from exc
        try:
            self._model = SentenceTransformer(self.model)
            self._dim = int(self._model.get_sentence_embedding_dimension())
        except Exception as exc:  # pragma: no cover
            raise EmbeddingError(f"Chargement du modèle local impossible : {exc}") from exc
        return self._model

    @property
    def ready(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            return False
        return True

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else []


# --------------------------------------------------------------------- #
#  Sélection du fournisseur
# --------------------------------------------------------------------- #

_EMBEDDER_CACHE: dict[str, BaseEmbedder] = {}


def _resolve_provider(settings: Settings) -> str:
    provider = (settings.embedding_provider or "auto").strip().lower()
    if provider != "auto":
        return provider
    if settings.gemini_api_key:
        return "gemini"
    if settings.openai_api_key:
        return "openai"
    if settings.enable_local_embeddings:
        try:
            import sentence_transformers  # noqa: F401

            return "local"
        except Exception:
            pass
    return "hashing"


def build_embedder(settings: Settings | None = None) -> BaseEmbedder:
    """Construit le fournisseur d'embeddings demandé (avec repli automatique)."""
    settings = settings or get_settings()
    provider = _resolve_provider(settings)

    try:
        if provider == "gemini" and settings.gemini_api_key:
            return GeminiEmbedder(
                settings.gemini_api_key,
                model=settings.embedding_model or "text-embedding-004",
                timeout=settings.llm_timeout_s,
            )
        if provider == "openai" and settings.openai_api_key:
            return OpenAIEmbedder(
                settings.openai_api_key,
                model=settings.embedding_model or "text-embedding-3-small",
                base_url=settings.openai_base_url,
                timeout=settings.llm_timeout_s,
            )
        if provider == "local" and settings.enable_local_embeddings:
            embedder = LocalEmbedder(settings.embedding_model)
            if embedder.ready:
                return embedder
    except Exception:
        pass
    return HashingEmbedder(settings.embedding_dim)


def get_embedder(settings: Settings | None = None, *, refresh: bool = False) -> BaseEmbedder:
    """Renvoie le fournisseur d'embeddings (mis en cache)."""
    settings = settings or get_settings()
    key = "|".join(
        [
            settings.embedding_provider,
            settings.embedding_model,
            str(settings.embedding_dim),
            "1" if settings.gemini_api_key else "0",
            "1" if settings.openai_api_key else "0",
            "1" if settings.enable_local_embeddings else "0",
        ]
    )
    if refresh or key not in _EMBEDDER_CACHE:
        _EMBEDDER_CACHE[key] = build_embedder(settings)
    return _EMBEDDER_CACHE[key]


def clear_embedder_cache() -> None:
    _EMBEDDER_CACHE.clear()


# --------------------------------------------------------------------- #
#  Utilitaires vectoriels (sans numpy)
# --------------------------------------------------------------------- #

def to_bytes(vector: Iterable[float]) -> bytes:
    """Sérialise un vecteur en float32 (compact, portable)."""
    buf = array.array("f", [float(value) for value in vector])
    return buf.tobytes()


def from_bytes(blob: bytes) -> list[float]:
    """Désérialise un vecteur float32."""
    buf = array.array("f")
    buf.frombytes(blob)
    return list(buf)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Similarité cosinus entre deux vecteurs (protégée contre les dimensions différentes)."""
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = norm_a = norm_b = 0.0
    for i in range(size):
        va = a[i]
        vb = b[i]
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)
