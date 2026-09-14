"""Base vectorielle.

Par défaut : **SQLite** (fichier unique, aucune dépendance, aucun serveur) —
suffisant pour des milliers de chunks et parfait pour les hébergements gratuits.

Optionnel : **ChromaDB** si vous l'installez (``VECTOR_BACKEND=chroma``).
Les deux implémentations exposent la même interface, le reste de l'application
ne sait pas laquelle est utilisée.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import Settings, get_settings
from .embeddings import cosine_similarity, from_bytes, to_bytes
from .textutils import Chunk


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StoredChunk:
    """Chunk indexé renvoyé par la base."""

    chunk_id: str
    doc_id: str
    text: str
    section: str = ""
    page: int = 0
    ordinal: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None


@dataclass
class SearchHit:
    """Résultat de recherche (dense, lexical ou hybride)."""

    chunk: StoredChunk
    score: float
    dense_score: float = 0.0
    lexical_score: float = 0.0

    @property
    def source(self) -> str:
        return str(self.chunk.metadata.get("source", "document"))

    @property
    def title(self) -> str:
        return str(self.chunk.metadata.get("title", self.chunk.doc_id))

    def citation(self) -> str:
        parts = [self.title]
        if self.chunk.section:
            parts.append(self.chunk.section)
        if self.chunk.page:
            parts.append(f"p.{self.chunk.page}")
        return " › ".join(parts)


# --------------------------------------------------------------------- #
#  Implémentation SQLite
# --------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    title       TEXT,
    source      TEXT,
    kind        TEXT,
    pages       INTEGER DEFAULT 0,
    chunk_count INTEGER DEFAULT 0,
    created_at  TEXT,
    meta        TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id   TEXT NOT NULL,
    ordinal  INTEGER DEFAULT 0,
    text     TEXT NOT NULL,
    section  TEXT DEFAULT '',
    page     INTEGER DEFAULT 0,
    meta     TEXT,
    vector   BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class SQLiteVectorStore:
    """Base vectorielle locale sur fichier SQLite."""

    backend = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._revision = 0
        self._init_schema()

    # ------------------------------------------------------------ interne
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    @property
    def revision(self) -> int:
        """Incrémenté à chaque écriture (sert à invalider les caches)."""
        return self._revision

    def _touch(self) -> None:
        self._revision += 1

    # ------------------------------------------------------------ écriture
    def add_document(
        self,
        *,
        doc_id: str | None = None,
        title: str,
        source: str,
        kind: str = "text",
        pages: int = 0,
        chunks: Sequence[Chunk] = (),
        vectors: Sequence[Sequence[float]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Ajoute (ou remplace) un document et ses chunks."""
        doc_id = doc_id or uuid.uuid4().hex[:12]
        vectors = list(vectors or [])
        created = utcnow()
        self.delete_document(doc_id, missing_ok=True)

        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO documents "
                "(doc_id, title, source, kind, pages, chunk_count, created_at, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    title,
                    source,
                    kind,
                    int(pages),
                    len(chunks),
                    created,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            rows = []
            for position, chunk in enumerate(chunks):
                vector = vectors[position] if position < len(vectors) else None
                meta = {
                    "source": source,
                    "title": title,
                    "kind": kind,
                    "created_at": created,
                    **chunk.as_metadata(),
                }
                rows.append(
                    (
                        f"{doc_id}:{position}",
                        doc_id,
                        position,
                        chunk.text,
                        chunk.section,
                        int(chunk.page or 0),
                        json.dumps(meta, ensure_ascii=False),
                        to_bytes(vector) if vector else None,
                    )
                )
            connection.executemany(
                "INSERT OR REPLACE INTO chunks "
                "(chunk_id, doc_id, ordinal, text, section, page, meta, vector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            connection.commit()
        self._touch()
        return doc_id

    def delete_document(self, doc_id: str, *, missing_ok: bool = False) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,))
            if cursor.fetchone() is None:
                if missing_ok:
                    return False
                return False
            connection.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            connection.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            connection.commit()
        self._touch()
        return True

    def reset(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript("DELETE FROM chunks; DELETE FROM documents;")
            connection.commit()
        self._touch()

    def set_meta(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            connection.commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    # ------------------------------------------------------------ lecture
    def list_documents(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        documents = []
        for row in rows:
            try:
                meta = json.loads(row["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            documents.append(
                {
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "source": row["source"],
                    "kind": row["kind"],
                    "pages": row["pages"],
                    "chunk_count": row["chunk_count"],
                    "created_at": row["created_at"],
                    "metadata": meta,
                }
            )
        return documents

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        for document in self.list_documents():
            if document["doc_id"] == doc_id:
                return document
        return None

    def iter_chunks(self, doc_id: str | None = None) -> list[StoredChunk]:
        query = "SELECT * FROM chunks"
        params: tuple[Any, ...] = ()
        if doc_id:
            query += " WHERE doc_id = ?"
            params = (doc_id,)
        query += " ORDER BY doc_id, ordinal"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        chunks: list[StoredChunk] = []
        for row in rows:
            try:
                meta = json.loads(row["meta"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            blob = row["vector"]
            chunks.append(
                StoredChunk(
                    chunk_id=row["chunk_id"],
                    doc_id=row["doc_id"],
                    text=row["text"],
                    section=row["section"] or "",
                    page=row["page"] or 0,
                    ordinal=row["ordinal"] or 0,
                    metadata=meta,
                    vector=from_bytes(blob) if blob else None,
                )
            )
        return chunks

    def search_dense(
        self, vector: Sequence[float], top_k: int = 10, *, doc_ids: Sequence[str] | None = None
    ) -> list[SearchHit]:
        if not vector:
            return []
        allowed = set(doc_ids) if doc_ids else None
        hits: list[SearchHit] = []
        for chunk in self.iter_chunks():
            if allowed and chunk.doc_id not in allowed:
                continue
            if not chunk.vector:
                continue
            score = cosine_similarity(vector, chunk.vector)
            hits.append(SearchHit(chunk=chunk, score=score, dense_score=score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            documents = connection.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]
            chunks = connection.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
            with_vectors = connection.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE vector IS NOT NULL"
            ).fetchone()["n"]
        return {
            "backend": self.backend,
            "documents": int(documents),
            "chunks": int(chunks),
            "chunks_with_vectors": int(with_vectors),
            "path": str(self.path),
        }

    def clear_vectors(self) -> None:
        """Supprime les vecteurs (utile si le fournisseur d'embeddings change)."""
        with self._lock, self._connect() as connection:
            connection.execute("UPDATE chunks SET vector = NULL")
            connection.commit()
        self._touch()


# --------------------------------------------------------------------- #
#  Implémentation ChromaDB (optionnelle)
# --------------------------------------------------------------------- #

class ChromaVectorStore:
    """Adaptateur ChromaDB (nécessite ``pip install chromadb``)."""

    backend = "chroma"

    def __init__(self, path: str | Path, collection: str = "knowledge") -> None:
        import chromadb  # type: ignore  # noqa: WPS433 (import paresseux volontaire)

        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._revision = 0
        self._client = chromadb.PersistentClient(path=str(self.path))
        self._collection = self._client.get_or_create_collection(
            name=collection, metadata={"hnsw:space": "cosine"}
        )

    @property
    def revision(self) -> int:
        return self._revision

    def add_document(
        self,
        *,
        doc_id: str | None = None,
        title: str,
        source: str,
        kind: str = "text",
        pages: int = 0,
        chunks: Sequence[Chunk] = (),
        vectors: Sequence[Sequence[float]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        doc_id = doc_id or uuid.uuid4().hex[:12]
        self.delete_document(doc_id, missing_ok=True)
        created = utcnow()
        if not chunks:
            return doc_id
        vectors = list(vectors or [])
        embeddings = [list(v) for v in vectors] if vectors else None
        documents = [chunk.text for chunk in chunks]
        ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "doc_id": doc_id,
                "source": source,
                "title": title,
                "kind": kind,
                "section": chunk.section,
                "page": int(chunk.page or 0),
                "ordinal": i,
                "created_at": created,
                "extra": json.dumps(metadata or {}, ensure_ascii=False),
                "pages_total": int(pages),
            }
            for i, chunk in enumerate(chunks)
        ]
        with self._lock:
            self._collection.add(
                ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
            )
        self._revision += 1
        return doc_id

    def delete_document(self, doc_id: str, *, missing_ok: bool = False) -> bool:
        with self._lock:
            existing = self._collection.get(where={"doc_id": doc_id}, limit=1)
            if not existing.get("ids"):
                if missing_ok:
                    return False
                return False
            self._collection.delete(where={"doc_id": doc_id})
        self._revision += 1
        return True

    def reset(self) -> None:
        with self._lock:
            ids = self._collection.get(limit=100000).get("ids", [])
            if ids:
                self._collection.delete(ids=ids)
        self._revision += 1

    def set_meta(self, key: str, value: Any) -> None:
        (self.path / "kv.json").parent.mkdir(parents=True, exist_ok=True)
        store = {}
        kv_file = self.path / "kv.json"
        if kv_file.exists():
            try:
                store = json.loads(kv_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                store = {}
        store[key] = value
        kv_file.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")

    def get_meta(self, key: str, default: Any = None) -> Any:
        kv_file = self.path / "kv.json"
        if not kv_file.exists():
            return default
        try:
            return json.loads(kv_file.read_text(encoding="utf-8")).get(key, default)
        except json.JSONDecodeError:
            return default

    def _to_document(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "doc_id": metadata.get("doc_id", ""),
            "title": metadata.get("title", ""),
            "source": metadata.get("source", ""),
            "kind": metadata.get("kind", "text"),
            "pages": metadata.get("pages_total", 0),
            "chunk_count": 0,
            "created_at": metadata.get("created_at", ""),
            "metadata": {},
        }

    def list_documents(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._collection.get(include=["metadatas"], limit=100000)
        by_doc: dict[str, dict[str, Any]] = {}
        for metadata in data.get("metadatas") or []:
            doc_id = str(metadata.get("doc_id", ""))
            if not doc_id:
                continue
            entry = by_doc.setdefault(doc_id, self._to_document(metadata))
            entry["chunk_count"] += 1
        return sorted(by_doc.values(), key=lambda doc: doc["created_at"], reverse=True)

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        for document in self.list_documents():
            if document["doc_id"] == doc_id:
                return document
        return None

    def iter_chunks(self, doc_id: str | None = None) -> list[StoredChunk]:
        where = {"doc_id": doc_id} if doc_id else None
        with self._lock:
            data = self._collection.get(
                where=where,
                include=["documents", "metadatas", "embeddings"],
                limit=100000,
            )
        chunks: list[StoredChunk] = []
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []
        embeddings = data.get("embeddings")
        if embeddings is None:
            embeddings = []
        embeddings = list(embeddings) if len(embeddings) else []
        for index, text_value in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            vector = None
            if index < len(embeddings) and embeddings[index] is not None:
                vector = [float(value) for value in embeddings[index]]
            chunks.append(
                StoredChunk(
                    chunk_id=str(data["ids"][index]),
                    doc_id=str(metadata.get("doc_id", "")),
                    text=text_value or "",
                    section=str(metadata.get("section", "")),
                    page=int(metadata.get("page", 0) or 0),
                    ordinal=int(metadata.get("ordinal", 0) or 0),
                    metadata=metadata,
                    vector=vector,
                )
            )
        return chunks

    def search_dense(
        self, vector: Sequence[float], top_k: int = 10, *, doc_ids: Sequence[str] | None = None
    ) -> list[SearchHit]:
        if not vector:
            return []
        where = {"doc_id": {"$in": list(doc_ids)}} if doc_ids else None
        with self._lock:
            data = self._collection.query(
                query_embeddings=[list(vector)],
                n_results=max(1, top_k),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        hits: list[SearchHit] = []
        documents = (data.get("documents") or [[]])[0]
        metadatas = (data.get("metadatas") or [[]])[0]
        distances = (data.get("distances") or [[]])[0]
        ids = (data.get("ids") or [[]])[0]
        for index, text_value in enumerate(documents):
            metadata = metadatas[index] if index < len(metadatas) else {}
            distance = float(distances[index]) if index < len(distances) else 1.0
            score = max(0.0, 1.0 - distance)
            hits.append(
                SearchHit(
                    chunk=StoredChunk(
                        chunk_id=ids[index] if index < len(ids) else "",
                        doc_id=str(metadata.get("doc_id", "")),
                        text=text_value or "",
                        section=str(metadata.get("section", "")),
                        page=int(metadata.get("page", 0) or 0),
                        ordinal=int(metadata.get("ordinal", 0) or 0),
                        metadata=metadata,
                    ),
                    score=score,
                    dense_score=score,
                )
            )
        return hits

    def stats(self) -> dict[str, Any]:
        with self._lock:
            count = self._collection.count()
        return {
            "backend": self.backend,
            "documents": len(self.list_documents()),
            "chunks": count,
            "chunks_with_vectors": count,
            "path": str(self.path),
        }

    def clear_vectors(self) -> None:  # pragma: no cover - non nécessaire avec Chroma
        return None


# --------------------------------------------------------------------- #
#  Fabrique
# --------------------------------------------------------------------- #

_STORE_CACHE: dict[str, Any] = {}


def get_vector_store(settings: Settings | None = None, *, refresh: bool = False):
    """Renvoie la base vectorielle configurée (SQLite par défaut)."""
    settings = settings or get_settings()
    backend = (settings.vector_backend or "auto").strip().lower()
    path = settings.vector_path
    key = f"{backend}|{path}"

    if not refresh and key in _STORE_CACHE:
        return _STORE_CACHE[key]

    store = None
    if backend == "chroma":
        try:
            store = ChromaVectorStore(path)
        except Exception:
            store = None
    if store is None:
        store = SQLiteVectorStore(path / "vectors.sqlite3")
    _STORE_CACHE[key] = store
    return store


def clear_store_cache() -> None:
    _STORE_CACHE.clear()
