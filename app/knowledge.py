"""Ingestion de la base de connaissances : PDF, Markdown, texte.

Extraction du texte → découpage par sections → embeddings → base vectorielle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import BASE_DIR, Settings, get_settings
from .embeddings import get_embedder
from .textutils import chunk_document
from .vectorstore import get_vector_store

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".text", ".rst", ".csv"}

#: Dossier du corpus d'exemple livré avec le projet (versionné dans Git).
SAMPLE_KNOWLEDGE_DIR = BASE_DIR / "knowledge"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IngestionError(RuntimeError):
    """Erreur d'ingestion (fichier illisible, PDF protégé, etc.)."""


# --------------------------------------------------------------------- #
#  Extraction du texte
# --------------------------------------------------------------------- #

def extract_pdf(path: Path) -> tuple[list[str], int]:
    """Renvoie (liste_des_pages, nombre_de_pages) pour un PDF."""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise IngestionError(
            "pypdf n'est pas installé : `pip install pypdf` pour lire les PDF."
        ) from exc

    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")  # PDF avec mot de passe vide
            except Exception:
                raise IngestionError(f"PDF protégé par mot de passe : {path.name}") from None
        pages = [(page.extract_text() or "") for page in reader.pages]
        return pages, len(reader.pages)
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(f"Lecture du PDF impossible ({path.name}) : {exc}") from exc


def extract_text(path: Path) -> tuple[list[str], int]:
    """Renvoie (liste_des_pages, nombre_de_pages) pour un fichier texte."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            content = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        raise IngestionError(f"Encodage non reconnu : {path.name}")
    content = content.strip()
    # On considère ~3000 caractères comme une « page » pour garder des références utiles.
    page_size = 3000
    pages = [content[i : i + page_size] for i in range(0, max(1, len(content)), page_size)]
    return pages or [""], max(1, len(pages))


def extract_any(path: Path) -> tuple[list[str], int, str]:
    """Extrait un fichier supporté. Renvoie (pages, nb_pages, kind)."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise IngestionError(
            f"Format non supporté : {suffix or 'inconnu'}. "
            f"Formats acceptés : {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if suffix == ".pdf":
        pages, count = extract_pdf(path)
        return pages, count, "pdf"
    pages, count = extract_text(path)
    return pages, count, "markdown" if suffix in {".md", ".markdown"} else "text"


# --------------------------------------------------------------------- #
#  Ingestion unitaire
# --------------------------------------------------------------------- #

def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime": int(stat.st_mtime)}


def document_id_for(path: Path, namespace: str = "doc") -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"{namespace}-{digest}"


def ingest_file(
    path: str | Path,
    *,
    settings: Settings | None = None,
    title: str | None = None,
    namespace: str = "doc",
    force: bool = False,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Ingère un fichier dans la base vectorielle. Renvoie un résumé."""
    settings = settings or get_settings()
    path = Path(path).resolve()
    if not path.exists():
        raise IngestionError(f"Fichier introuvable : {path}")

    store = get_vector_store(settings)
    doc_id = document_id_for(path, namespace)
    signature = _file_signature(path)

    if not force:
        existing = store.get_document(doc_id)
        if existing and existing.get("metadata", {}).get("signature") == signature:
            return {
                "doc_id": doc_id,
                "title": existing.get("title", path.stem),
                "chunks": existing.get("chunk_count", 0),
                "pages": existing.get("pages", 0),
                "status": "unchanged",
                "source": existing.get("source", str(path)),
            }

    pages, page_count, kind = extract_any(path)
    all_chunks = []
    for page_number, page_text in enumerate(pages, start=1):
        if not page_text.strip():
            continue
        page_chunks = chunk_document(
            page_text,
            chunk_size_words=settings.chunk_size_words,
            overlap_words=settings.chunk_overlap_words,
            min_words=settings.min_chunk_words,
            page=page_number,
        )
        for chunk in page_chunks:
            chunk.index = len(all_chunks)
            all_chunks.append(chunk)

    if not all_chunks:
        raise IngestionError(
            f"Aucun texte exploitable dans {path.name} "
            "(PDF scanné ? Il faudrait de l'OCR)."
        )

    embedder = get_embedder(settings)
    vectors: list[list[float]] = []
    embedding_provider = "aucun"
    embedding_error = ""
    try:
        vectors = embedder.embed_documents([chunk.text for chunk in all_chunks])
        embedding_provider = embedder.label
    except Exception as exc:  # on indexe quand même : la recherche BM25 prend le relais
        embedding_error = str(exc)
        vectors = []

    doc_title = title or path.stem.replace("_", " ").replace("-", " ").strip()
    doc_id = store.add_document(
        doc_id=doc_id,
        title=doc_title,
        source=source_label or str(path),
        kind=kind,
        pages=page_count,
        chunks=all_chunks,
        vectors=vectors,
        metadata={
            "signature": signature,
            "filename": path.name,
            "namespace": namespace,
            "ingested_at": utcnow(),
            "embedding_provider": embedding_provider,
            "embedding_dim": embedder.dim if vectors else 0,
            "embedding_error": embedding_error,
            "chunk_settings": {
                "size_words": settings.chunk_size_words,
                "overlap_words": settings.chunk_overlap_words,
            },
        },
    )
    store.set_meta(
        "index_meta",
        {
            "embedding_provider": embedder.label,
            "embedding_dim": embedder.dim,
            "updated_at": utcnow(),
        },
    )

    return {
        "doc_id": doc_id,
        "title": doc_title,
        "chunks": len(all_chunks),
        "pages": page_count,
        "status": "ingested",
        "source": source_label or str(path),
        "kind": kind,
        "vectors": len(vectors),
        "embedding_provider": embedding_provider,
        "embedding_error": embedding_error,
    }


def ingest_bytes(
    data: bytes,
    filename: str,
    *,
    settings: Settings | None = None,
    title: str | None = None,
    namespace: str = "upload",
) -> dict[str, Any]:
    """Enregistre un fichier reçu par l'API puis l'ingère."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    safe_name = Path(filename or "document").name
    target = settings.pdf_path / safe_name
    if target.exists():
        stem, suffix = target.stem, target.suffix
        target = settings.pdf_path / f"{stem}-{hashlib.sha1(data).hexdigest()[:6]}{suffix}"
    target.write_bytes(data)
    return ingest_file(
        target,
        settings=settings,
        title=title,
        namespace=namespace,
        force=True,
        source_label=safe_name,
    )


# --------------------------------------------------------------------- #
#  Synchronisation d'un dossier
# --------------------------------------------------------------------- #

def iter_supported_files(directory: Path) -> Iterable[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def sync_directory(
    directory: str | Path,
    *,
    settings: Settings | None = None,
    namespace: str = "sample",
    force: bool = False,
) -> list[dict[str, Any]]:
    """Ingère tous les fichiers supportés d'un dossier (idempotent)."""
    settings = settings or get_settings()
    directory = Path(directory)
    results: list[dict[str, Any]] = []
    for path in iter_supported_files(directory):
        try:
            results.append(
                ingest_file(
                    path,
                    settings=settings,
                    namespace=namespace,
                    force=force,
                    source_label=path.name,
                )
            )
        except IngestionError as exc:
            results.append({"status": "error", "source": path.name, "error": str(exc)})
    return results


def sync_all(
    *, settings: Settings | None = None, force: bool = False
) -> dict[str, Any]:
    """Synchronise le corpus d'exemple livré + les documents uploadés."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    results = {
        "sample": sync_directory(
            SAMPLE_KNOWLEDGE_DIR, settings=settings, namespace="sample", force=force
        ),
        "uploads": sync_directory(
            settings.pdf_path, settings=settings, namespace="upload", force=force
        ),
        "knowledge_dir": sync_directory(
            settings.knowledge_path, settings=settings, namespace="local", force=force
        ),
    }
    total_chunks = sum(
        item.get("chunks", 0) for group in results.values() for item in group
    )
    return {
        "results": results,
        "chunks_indexed": total_chunks,
        "stats": get_vector_store(settings).stats(),
    }


def read_stats(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    store = get_vector_store(settings)
    embedder = get_embedder(settings)
    return {
        **store.stats(),
        "embedding_provider": embedder.label,
        "embedding_dim": embedder.dim,
        "embedding_ready": embedder.ready,
        "index_meta": store.get_meta("index_meta", {}),
    }


def export_document_json(doc_id: str, settings: Settings | None = None) -> str:
    """Exporte un document indexé (chunks + métadonnées) en JSON."""
    settings = settings or get_settings()
    store = get_vector_store(settings)
    document = store.get_document(doc_id)
    if not document:
        raise IngestionError(f"Document inconnu : {doc_id}")
    payload = {
        "document": document,
        "chunks": [
            {
                "ordinal": chunk.ordinal,
                "page": chunk.page,
                "section": chunk.section,
                "text": chunk.text,
            }
            for chunk in store.iter_chunks(doc_id)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
