"""Routes de l'API REST (JSON)."""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from ..analysis import analyze, render_report
from ..config import get_settings
from ..indicators import bollinger, ema, rsi, sma
from ..knowledge import (
    IngestionError,
    export_document_json,
    ingest_bytes,
    read_stats,
    sync_all,
)
from ..llm import provider_status
from ..market import (
    INTERVALS,
    PERIODS,
    POPULAR_SYMBOLS,
    MarketDataError,
    get_instrument,
    market_status,
)
from ..reports import get_report_store
from ..retriever import search as knowledge_search
from ..services.analysis_service import AnalysisRequest, run_analysis
from ..services.chat_service import ChatRequest, run_chat
from ..vectorstore import get_vector_store
from ..vision import decode_image_payload
from .deps import require_token

router = APIRouter(prefix="/api", tags=["analyse"], dependencies=[Depends(require_token)])


# --------------------------------------------------------------------- #
#  Schémas
# --------------------------------------------------------------------- #

class AnalyzePayload(BaseModel):
    """Corps JSON de la requête d'analyse."""

    symbol: str = Field(default="", max_length=24, description="Ex : AAPL, BTC-USD, ^FCHI")
    period: str = Field(default="6mo", max_length=8)
    interval: str = Field(default="1d", max_length=8)
    question: str = Field(default="", max_length=4000)
    image_base64: str = Field(default="", description="Capture du graphique (data URL ou base64)")
    top_k: int = Field(default=5, ge=1, le=12)
    analyze_detected_symbol: bool = True
    save: bool = True

    @field_validator("question")
    @classmethod
    def _clean_question(cls, value: str) -> str:
        return (value or "").strip()


class ChatPayload(BaseModel):
    """Corps JSON du chat documentaire."""

    question: str = Field(min_length=2, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=12)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class TextDocumentPayload(BaseModel):
    """Import d'un document texte (sans fichier)."""

    title: str = Field(min_length=2, max_length=160)
    content: str = Field(min_length=40, max_length=400_000)


class ReindexPayload(BaseModel):
    """Options de réindexation."""

    force: bool = True


# --------------------------------------------------------------------- #
#  Santé & configuration
# --------------------------------------------------------------------- #

@router.get("/health", summary="État de l'application et des services")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "application": settings.app_name,
        "version": settings.app_version,
        "llm": provider_status(settings),
        "marche": market_status(settings),
        "connaissances": read_stats(settings),
        "historique": get_report_store(settings).stats(),
        "configuration": {
            "provider_embeddings": settings.embedding_provider,
            "backend_vectoriel": settings.vector_backend,
            "chunk_size_words": settings.chunk_size_words,
            "top_k": settings.top_k,
            "hybrid_alpha": settings.hybrid_alpha,
            "protection_jeton": bool(settings.api_access_token),
        },
    }


@router.get("/symbols", summary="Symboles, périodes et unités de temps disponibles")
def symbols() -> dict[str, Any]:
    return {
        "symboles": POPULAR_SYMBOLS,
        "periodes": PERIODS,
        "intervalles": INTERVALS,
    }


# --------------------------------------------------------------------- #
#  Données de marché & graphique
# --------------------------------------------------------------------- #

def _series_last(values: list[Optional[float]], limit: int) -> list[Optional[float]]:
    return [None if value is None else round(float(value), 6) for value in values[-limit:]]


@router.get("/market/{symbol}", summary="Données de marché et séries pour le graphique")
def market_chart(
    symbol: str,
    period: str = Query(default="6mo"),
    interval: str = Query(default="1d"),
    limit: int = Query(default=240, ge=30, le=800),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    settings = get_settings()
    try:
        instrument = get_instrument(
            symbol, period, interval, settings=settings, force_refresh=refresh
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    result = analyze(instrument)
    closes = instrument.closes
    highs = instrument.highs
    lows = instrument.lows
    candles = instrument.candles[-limit:]
    offset = len(instrument.candles) - len(candles)
    boll = bollinger(closes, 20, 2.0)

    return {
        "instrument": instrument.to_dict(),
        "candles": [
            {
                "t": candle.t,
                "date": candle.day,
                "o": candle.o,
                "h": candle.h,
                "l": candle.l,
                "c": candle.c,
                "v": candle.v,
            }
            for candle in candles
        ],
        "overlays": {
            "ema20": _series_last(ema(closes, 20), limit),
            "ema50": _series_last(ema(closes, 50), limit),
            "sma200": _series_last(sma(closes, 200), limit),
            "bollinger_upper": _series_last(boll["upper"], limit),
            "bollinger_lower": _series_last(boll["lower"], limit),
        },
        "rsi14": _series_last(rsi(closes, 14), limit),
        "levels": result.levels,
        "summary": {
            "price": result.price,
            "label": result.label,
            "score": result.score,
            "confidence": result.confidence,
            "trend": result.trend,
            "setup": result.setup,
            "scenarios": result.scenarios,
            "stats": result.stats,
            "patterns": [pattern.to_dict() for pattern in result.patterns[:8]],
            "indicators": result.indicators,
            "warnings": result.warnings,
            "source": instrument.source,
            "offset": offset,
            "period_high": max(highs[-limit:]),
            "period_low": min(lows[-limit:]),
        },
    }


@router.get("/market/{symbol}/report", response_class=PlainTextResponse, summary="Rapport technique brut (Markdown)")
def market_report(symbol: str, period: str = "6mo", interval: str = "1d") -> str:
    settings = get_settings()
    try:
        instrument = get_instrument(symbol, period, interval, settings=settings)
    except MarketDataError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return render_report(analyze(instrument))


# --------------------------------------------------------------------- #
#  Analyse complète (symbole et/ou image)
# --------------------------------------------------------------------- #

@router.post("/analyze", summary="Analyse + prédiction (symbole et/ou capture d'écran)")
def analyze_endpoint(payload: AnalyzePayload) -> dict[str, Any]:
    settings = get_settings()
    image: Optional[bytes] = None
    if payload.image_base64:
        try:
            image = decode_image_payload(payload.image_base64)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.symbol and image is None:
        raise HTTPException(
            status_code=400,
            detail="Fournissez au moins un symbole (ex : AAPL) ou une image de graphique.",
        )

    request = AnalysisRequest(
        symbol=payload.symbol,
        period=payload.period,
        interval=payload.interval,
        question=payload.question,
        image=image,
        top_k=payload.top_k,
        analyze_detected_symbol=payload.analyze_detected_symbol,
        save=payload.save,
    )
    try:
        response = run_analysis(request, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response.to_dict()


@router.post("/analyze/upload", summary="Analyse à partir d'un fichier image (multipart)")
async def analyze_upload_endpoint(
    file: UploadFile = File(..., description="Capture du graphique (PNG/JPEG)"),
    symbol: str = Form(default=""),
    period: str = Form(default="6mo"),
    interval: str = Form(default="1d"),
    question: str = Form(default=""),
    top_k: int = Form(default=5),
) -> dict[str, Any]:
    settings = get_settings()
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux (limite {settings.max_upload_mb} Mo).",
        )
    request = AnalysisRequest(
        symbol=symbol,
        period=period,
        interval=interval,
        question=question,
        image=raw,
        top_k=max(1, min(12, int(top_k))),
    )
    return run_analysis(request, settings=settings).to_dict()


# --------------------------------------------------------------------- #
#  Chat documentaire
# --------------------------------------------------------------------- #

@router.post("/chat", summary="Poser une question sur vos cours (RAG)")
def chat_endpoint(payload: ChatPayload) -> dict[str, Any]:
    return run_chat(
        ChatRequest(
            question=payload.question,
            top_k=payload.top_k,
            history=payload.history or [],
        )
    ).to_dict()


# --------------------------------------------------------------------- #
#  Base de connaissances
# --------------------------------------------------------------------- #

@router.get("/knowledge", summary="Documents indexés et statistiques")
def knowledge_list() -> dict[str, Any]:
    settings = get_settings()
    store = get_vector_store(settings)
    return {
        "statistiques": read_stats(settings),
        "documents": store.list_documents(),
    }


@router.post("/knowledge/upload", summary="Importer un ou plusieurs documents (PDF, MD, TXT)")
async def knowledge_upload(
    files: list[UploadFile] = File(..., description="PDF, Markdown ou texte"),
) -> dict[str, Any]:
    settings = get_settings()
    results: list[dict[str, Any]] = []
    for upload in files:
        raw = await upload.read()
        if not raw:
            results.append({"source": upload.filename, "status": "error", "error": "Fichier vide."})
            continue
        if len(raw) > settings.max_upload_bytes:
            results.append(
                {
                    "source": upload.filename,
                    "status": "error",
                    "error": f"Fichier trop volumineux (limite {settings.max_upload_mb} Mo).",
                }
            )
            continue
        try:
            results.append(ingest_bytes(raw, upload.filename or "document", settings=settings))
        except IngestionError as exc:
            results.append({"source": upload.filename, "status": "error", "error": str(exc)})
    return {"resultats": results, "statistiques": read_stats(settings)}


@router.post("/knowledge/text", summary="Importer un document texte directement")
def knowledge_text(payload: TextDocumentPayload) -> dict[str, Any]:
    settings = get_settings()
    try:
        result = ingest_bytes(
            payload.content.encode("utf-8"),
            f"{payload.title}.md",
            settings=settings,
            title=payload.title,
        )
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"resultat": result, "statistiques": read_stats(settings)}


@router.delete("/knowledge/{doc_id}", summary="Supprimer un document de l'index")
def knowledge_delete(doc_id: str) -> dict[str, Any]:
    settings = get_settings()
    store = get_vector_store(settings)
    if not store.delete_document(doc_id):
        raise HTTPException(status_code=404, detail=f"Document introuvable : {doc_id}")
    return {"statut": "supprimé", "doc_id": doc_id, "statistiques": read_stats(settings)}


@router.get("/knowledge/{doc_id}/export", response_class=PlainTextResponse, summary="Exporter un document indexé (JSON)")
def knowledge_export(doc_id: str) -> str:
    try:
        return export_document_json(doc_id, get_settings())
    except IngestionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge/reindex", summary="Réindexer le corpus (cours d'exemple + imports)")
def knowledge_reindex(payload: ReindexPayload | None = None) -> dict[str, Any]:
    settings = get_settings()
    force = payload.force if payload else True
    return sync_all(settings=settings, force=force)


@router.get("/knowledge/search", summary="Tester la recherche hybride dans vos cours")
def knowledge_search_endpoint(
    q: str = Query(min_length=2, max_length=400),
    top_k: int = Query(default=5, ge=1, le=12),
) -> dict[str, Any]:
    result = knowledge_search(q, top_k=top_k)
    return {"question": q, "mode": result.mode, "notes": result.notes, "resultats": result.sources}


# --------------------------------------------------------------------- #
#  Historique des analyses
# --------------------------------------------------------------------- #

@router.get("/reports", summary="Historique des analyses")
def reports_list(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    symbol: str = Query(default=""),
) -> dict[str, Any]:
    store = get_report_store(get_settings())
    return {
        "analyses": store.list(limit=limit, offset=offset, symbol=symbol),
        "statistiques": store.stats(),
    }


@router.get("/reports/{report_id}", summary="Détail d'une analyse")
def reports_get(report_id: str) -> dict[str, Any]:
    report = get_report_store(get_settings()).get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Analyse introuvable : {report_id}")
    return report


@router.delete("/reports/{report_id}", summary="Supprimer une analyse")
def reports_delete(report_id: str) -> dict[str, Any]:
    deleted = get_report_store(get_settings()).delete(report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Analyse introuvable : {report_id}")
    return {"statut": "supprimé", "report_id": report_id}


@router.get("/reports/{report_id}/export", response_class=PlainTextResponse, summary="Exporter une analyse (Markdown)")
def reports_export(report_id: str) -> str:
    report = get_report_store(get_settings()).get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Analyse introuvable : {report_id}")
    analysis = report.get("analysis") or {}
    observation = report.get("observation") or {}
    lines = [
        f"# Analyse {report.get('symbol') or 'image'} — {report.get('created_at')}",
        "",
        f"- Mode : {report.get('mode')} ({report.get('provider')} {report.get('model')})".strip(),
        f"- Dernier prix : {report.get('price')}",
        f"- Conclusion : {report.get('label')} (score {report.get('score')}, "
        f"confiance {report.get('confidence')})",
        "",
    ]
    if observation:
        lines += ["## Lecture de l'image", "```json", json.dumps(observation, ensure_ascii=False, indent=2), "```", ""]
    if analysis:
        lines += ["## Données techniques", "```json", json.dumps(analysis, ensure_ascii=False, indent=2), "```", ""]
    lines += ["## Réponse complète", "", report.get("answer") or "", ""]
    sources = report.get("sources") or []
    if sources:
        lines += ["## Sources utilisées", ""]
        for index, source in enumerate(sources, start=1):
            lines.append(
                f"{index}. {source.get('title')} › {source.get('section') or 'document'} "
                f"(score {source.get('score')})"
            )
    return "\n".join(lines)
